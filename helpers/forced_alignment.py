"""Cross-vendor GPU-accelerated Wav2Vec2 CTC Forced Alignment using DirectML & PyTorch.

Aligns Whisper transcripts to sample-accurate acoustic boundaries, eliminating
the multi-second attention drifts and dead air produced by vanilla Whisper models.
"""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import onnxruntime as ort
import torch
import torchaudio

try:
    from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC
except ImportError:
    Wav2Vec2Processor = None
    Wav2Vec2ForCTC = None


DEFAULT_ALIGNMENT_MODEL = "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese"


def get_alignment_cache_dir() -> Path:
    """Return local directory for cached ONNX Wav2Vec2 models."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "AlanoCut" / "models" / "wav2vec2-onnx"


def get_onnx_model_path(model_id: str = DEFAULT_ALIGNMENT_MODEL) -> Path:
    safe_name = model_id.replace("/", "--")
    return get_alignment_cache_dir() / f"{safe_name}.onnx"


def ensure_wav2vec2_onnx(model_id: str = DEFAULT_ALIGNMENT_MODEL) -> tuple[Path, dict[str, int], int]:
    """Ensure ONNX export and vocab mapping exists for Wav2Vec2 model."""
    target_dir = get_alignment_cache_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = get_onnx_model_path(model_id)
    vocab_path = onnx_path.with_suffix(".vocab.json")

    import json

    if onnx_path.is_file() and vocab_path.is_file():
        vocab_data = json.loads(vocab_path.read_text(encoding="utf-8"))
        return onnx_path, vocab_data["vocab"], vocab_data["pad_id"]

    if Wav2Vec2Processor is None or Wav2Vec2ForCTC is None:
        raise RuntimeError("transformers is required to initialize Wav2Vec2 model. Run: pip install transformers")

    print(f"Exporting Wav2Vec2 model '{model_id}' to ONNX for GPU DirectML acceleration...", flush=True)
    processor = Wav2Vec2Processor.from_pretrained(model_id)
    model = Wav2Vec2ForCTC.from_pretrained(model_id)
    model.eval()

    vocab = processor.tokenizer.get_vocab()
    pad_id = processor.tokenizer.pad_token_id or 0

    dummy_input = torch.randn(1, 16000 * 5)
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy_input,
            str(onnx_path),
            input_names=["input_values"],
            output_names=["logits"],
            dynamic_axes={"input_values": {1: "sequence_length"}, "logits": {1: "time_frames"}},
            opset_version=14,
            dynamo=False,
        )

    vocab_data = {"vocab": vocab, "pad_id": pad_id, "model_id": model_id}
    vocab_path.write_text(json.dumps(vocab_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wav2Vec2 model ready at {onnx_path}", flush=True)
    return onnx_path, vocab, pad_id


class Wav2Vec2Aligner:
    """Performs acoustic CTC forced alignment on speech audio using DirectML GPU."""

    def __init__(
        self,
        model_id: str = DEFAULT_ALIGNMENT_MODEL,
        device: str = "auto",
    ):
        self.model_id = model_id
        self.onnx_path, self.vocab, self.pad_id = ensure_wav2vec2_onnx(model_id)
        
        # Build normalized character lookup table
        self.char_map: dict[str, int] = {}
        for k, v in self.vocab.items():
            self.char_map[k] = v
            self.char_map[k.lower()] = v
            self.char_map[k.upper()] = v

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        available = ort.get_available_providers()
        if device == "dml" or (device == "auto" and "DmlExecutionProvider" in available):
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
            
        self.session = ort.InferenceSession(str(self.onnx_path), sess_options=sess_options, providers=providers)

    def align_segment(
        self,
        audio_samples: np.ndarray,
        words: list[dict[str, Any]],
        *,
        sample_rate: int = 16000,
        time_offset: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Align a chunk of audio samples against a sequence of words."""
        if len(words) == 0 or len(audio_samples) < 1600:
            return words

        # 1. Compute acoustic emissions on GPU via DirectML
        input_data = audio_samples.astype(np.float32).reshape(1, -1)
        logits = self.session.run(None, {"input_values": input_data})[0]  # (1, T, C)
        emissions = torch.from_numpy(logits).log_softmax(dim=-1)

        time_frames = emissions.shape[1]
        frame_duration = (len(audio_samples) / float(sample_rate)) / float(time_frames)

        # 2. Map words to tokens
        tokenized_words: list[tuple[dict[str, Any], list[int]]] = []
        targets: list[int] = []

        for w_dict in words:
            raw_text = str(w_dict.get("text", "")).strip()
            clean_chars = [c for c in raw_text.lower() if c in self.char_map and c != "|"]
            if not clean_chars:
                tokenized_words.append((w_dict, []))
                continue
            word_tokens = [self.char_map[c] for c in clean_chars]
            tokenized_words.append((w_dict, word_tokens))
            targets.extend(word_tokens)

        if not targets or len(targets) > time_frames:
            # Fallback if audio is shorter than text tokens
            return words

        targets_tensor = torch.tensor([targets], dtype=torch.int64)
        input_lengths = torch.tensor([time_frames], dtype=torch.int64)
        target_lengths = torch.tensor([len(targets)], dtype=torch.int64)

        try:
            aligned_tokens, scores = torchaudio.functional.forced_align(
                emissions,
                targets_tensor,
                input_lengths=input_lengths,
                target_lengths=target_lengths,
                blank=self.pad_id,
            )
            unflattened = torchaudio.functional.merge_tokens(aligned_tokens[0], scores[0])
        except Exception as exc:
            # If CTC trellis alignment fails (e.g. out of bound path), preserve original times
            return words

        # 3. Reassign word start and end boundaries
        aligned_words: list[dict[str, Any]] = []
        token_cursor = 0

        for w_dict, word_tokens in tokenized_words:
            if not word_tokens:
                aligned_words.append(w_dict)
                continue

            word_spans = unflattened[token_cursor : token_cursor + len(word_tokens)]
            token_cursor += len(word_tokens)

            if word_spans:
                start_frame = word_spans[0].start
                end_frame = word_spans[-1].end
                
                # Compute acoustic timestamps
                exact_start = time_offset + (start_frame * frame_duration)
                exact_end = time_offset + (end_frame * frame_duration)
                if exact_end <= exact_start:
                    exact_end = exact_start + 0.05

                # Mean CTC token score (convert log-prob to probability)
                span_scores = [span.score for span in word_spans if hasattr(span, "score")]
                if span_scores:
                    raw_score = float(np.mean(span_scores))
                    prob = float(np.exp(raw_score)) if raw_score <= 0.0 else raw_score
                    mean_score = float(np.clip(prob, 0.0, 1.0))
                else:
                    mean_score = 1.0

                w_copy = dict(w_dict)
                w_copy["start"] = round(float(exact_start), 3)
                w_copy["end"] = round(float(exact_end), 3)
                w_copy["score"] = round(float(mean_score), 4)
                w_copy["timing_source"] = "forced_alignment"
                aligned_words.append(w_copy)
            else:
                w_copy = dict(w_dict)
                w_copy["score"] = float(np.clip(float(w_copy.get("score", 1.0)), 0.0, 1.0))
                aligned_words.append(w_copy)

        return aligned_words

    def align_full_transcript(
        self,
        audio_samples: np.ndarray,
        raw_words: list[dict[str, Any]],
        *,
        sample_rate: int = 16000,
        chunk_duration_sec: float = 30.0,
    ) -> list[dict[str, Any]]:
        """Align full audio by breaking into overlapping or sentence-bounded chunks."""
        if not raw_words or len(audio_samples) == 0:
            return raw_words

        total_audio_sec = len(audio_samples) / float(sample_rate)

        # Break words into natural sentence or pause chunks (< chunk_duration_sec)
        chunks: list[tuple[float, float, list[dict[str, Any]]]] = []
        current_chunk_words: list[dict[str, Any]] = []
        chunk_start_time = 0.0

        for i, w in enumerate(raw_words):
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", w_start + 0.1))

            if not current_chunk_words:
                chunk_start_time = max(0.0, w_start - 0.5)
                current_chunk_words.append(w)
            else:
                chunk_span = w_end - chunk_start_time
                prev_end = float(current_chunk_words[-1].get("end", 0.0))
                pause = w_start - prev_end
                
                # Split at pauses (> 1.0s) or when chunk reaches max duration
                if (pause > 1.0 and chunk_span > 10.0) or chunk_span >= chunk_duration_sec:
                    chunk_end_time = min(total_audio_sec, prev_end + 0.5)
                    chunks.append((chunk_start_time, chunk_end_time, current_chunk_words))
                    chunk_start_time = max(0.0, w_start - 0.5)
                    current_chunk_words = [w]
                else:
                    current_chunk_words.append(w)

        if current_chunk_words:
            chunk_end_time = min(total_audio_sec, float(current_chunk_words[-1].get("end", 0.0)) + 0.5)
            chunks.append((chunk_start_time, chunk_end_time, current_chunk_words))

        # Align each chunk
        all_aligned_words: list[dict[str, Any]] = []
        for c_start, c_end, c_words in chunks:
            start_sample = int(c_start * sample_rate)
            end_sample = min(len(audio_samples), int(c_end * sample_rate))
            chunk_audio = audio_samples[start_sample:end_sample]

            aligned = self.align_segment(
                chunk_audio,
                c_words,
                sample_rate=sample_rate,
                time_offset=c_start,
            )
            all_aligned_words.extend(aligned)

        # Monotonicity, duration, and max overlap sanitization
        for i in range(len(all_aligned_words)):
            w = all_aligned_words[i]
            w_start = float(w["start"])
            w_end = float(w["end"])
            w["score"] = float(np.clip(float(w.get("score", 1.0)), 0.0, 1.0))

            if i > 0:
                prev = all_aligned_words[i - 1]
                prev_start = float(prev["start"])
                prev_end = float(prev["end"])

                # Enforce start monotonicity
                if w_start < prev_start:
                    w_start = prev_start

                # Prevent overlap exceeding 0.05s
                if prev_end - w_start > 0.05:
                    prev["end"] = round(w_start + 0.02, 3)

            if w_end <= w_start:
                w_end = w_start + 0.05

            w["start"] = round(w_start, 3)
            w["end"] = round(w_end, 3)

        return all_aligned_words
