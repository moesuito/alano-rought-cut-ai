"""Unit tests for GPU-accelerated Wav2Vec2 CTC Forced Alignment."""

from __future__ import annotations

import numpy as np
import pytest

from helpers.forced_alignment import (
    Wav2Vec2Aligner,
    ensure_wav2vec2_onnx,
    get_onnx_model_path,
)


def test_wav2vec2_model_is_available_and_cached():
    onnx_path, vocab, pad_id = ensure_wav2vec2_onnx()
    assert onnx_path.is_file()
    assert isinstance(vocab, dict)
    assert len(vocab) > 20
    assert isinstance(pad_id, int)


def test_align_segment_handles_empty_and_short_inputs():
    aligner = Wav2Vec2Aligner()
    # Empty
    assert aligner.align_segment(np.array([], dtype=np.float32), []) == []
    
    # Short audio
    raw_words = [{"text": "teste", "start": 0.0, "end": 0.5, "score": 1.0}]
    short_audio = np.zeros(800, dtype=np.float32)
    assert aligner.align_segment(short_audio, raw_words) == raw_words


def test_align_full_transcript_preserves_words_and_adjusts_boundaries():
    aligner = Wav2Vec2Aligner()
    # 5 seconds of synthetic audio
    audio = np.random.randn(16000 * 5).astype(np.float32) * 0.01
    
    raw_words = [
        {"text": "olá", "start": 0.5, "end": 1.0, "score": 1.0},
        {"text": "mundo", "start": 1.2, "end": 2.0, "score": 1.0},
    ]
    
    aligned = aligner.align_full_transcript(audio, raw_words)
    assert len(aligned) == 2
    assert aligned[0]["text"] == "olá"
    assert aligned[1]["text"] == "mundo"
    assert float(aligned[0]["end"]) >= float(aligned[0]["start"])
    assert float(aligned[1]["end"]) >= float(aligned[1]["start"])
    assert float(aligned[1]["start"]) >= float(aligned[0]["start"])
