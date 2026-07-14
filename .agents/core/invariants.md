# Invariants

Keep these rules in context throughout the run.

- Final deliverable is `<videos_dir>/edit/timeline.xml`.
- All session outputs go to `<videos_dir>/edit/`.
- After Step 01, `edit/...` is shorthand for `<videos_dir>/edit/...`.
- Never write session output inside the repo folder.
- Never cut inside a word.
- Do not trust ASR boundary timestamps alone when a cut feels tight; validate against waveform energy too.
- Source and preview transcription must use the same provider/configuration declared in workspace `alanocut.json`; silent provider fallback is prohibited.
- WhisperX is CUDA-only and requires forced-aligned words. Community-1 speakers are preferred; explicit `none` diarization is allowed with no speaker IDs and must be recorded as reduced precision. ElevenLabs requires provider word timestamps and provider diarization.
- Every normative transcript word must have a positive timed interval; CPU fallback, unaligned Whisper, missing selected-provider timing, and silent provider fallback are prohibited.
- Use canonical provider JSON word timestamps when trimming inside a packed phrase.
- `takes_packed.md` is the primary reading view, but not the final edit.
- The transcript is the map. The LLM is the editor.
- Use LLM editorial judgment, not deterministic scoring algorithms.
- Cache transcripts only when source and configuration fingerprints match; legacy or differently configured transcripts are stale.
- Do not re-transcribe unchanged files whose canonical cache contract still matches.
- XML should point to original media.
- The agent-facing QA path is audio-only. It does not inspect video frames and does not add fades, loudness processing, or other finishing effects.
- Preview renders are dry PCM WAV QA artifacts only.
- After editorial/runtime decisions are stable, execute this gate chain without skipping or reordering it: boundary refiner -> WAV/timeline-map renderer -> preview audio QC -> semantic QC -> forced persisted preview transcription with the preview WAV hash -> join-centric preview transcript QC -> readiness gate exit code 0 -> XML export.
- Preview/fixed timeline transcripts are mandatory QA artifacts; use their timed words and join evidence to catch duplicated, clipped, orphaned, or semantically wrong final content.
- `timeline_view.py` and `validate_edl_boundaries.py` are legacy manual diagnostics outside the normative workflow. They cannot replace the refiner or any mandatory gate and are scheduled for removal in v0.5.0.
- Do not export XML when a mandatory artifact is missing or stale, a QC status is not `pass`, or `verify_edit_ready.py` returns anything other than exit code 0.
- Do not create a final high-quality MP4.
- Do not add finishing features: subtitles, overlays, color grading, animations, Remotion, Manim, HyperFrames, YouTube download, publishing, or final-render features.
- Ask the user only when missing information would materially harm the edit.
