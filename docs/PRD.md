# PRD - Alano Cut

Status: living document.  
Updated: 2026-07-13

## Vision

Alano Cut is a transcript-driven rough-cut harness for talking-head and course footage. It turns an editorial EDL into a Premiere-compatible FCP7 XML while preserving original media and avoiding finishing work.

## Users

- Course/video editor — needs repeatable removal of retakes, direction cues, dead air, and unsafe cut boundaries.
- AI editing agent — needs compact transcripts, explicit gates, deterministic helpers, and resumable artifacts.

## Requirements

### CUT-00 — Local aligned and diarized transcription

- WHEN source or preview audio is transcribed THEN the normative system SHALL run faster-whisper `large-v3`, WhisperX forced word alignment, and `pyannote/speaker-diarization-community-1` locally with NVIDIA CUDA.
- Every lexical word SHALL have a positive forced-aligned interval and canonical speaker ID; missing timing, missing diarization, CPU fallback, legacy provider caches, and silent provider fallback SHALL block the workflow.
- Transcript caches SHALL be bound to source SHA-256, canonical schema, provider, models, runtime versions, and configuration SHA-256.
- Hugging Face credentials SHALL remain local and SHALL NOT appear in argv, transcripts, reports, logs, exceptions, or Git.

### CUT-01 — Rough-cut XML

- WHEN an approved EDL references original media THEN Alano Cut SHALL export a Premiere-compatible `timeline.xml` without rendering a final delivery video.

### CUT-02 — Audio-safe boundaries

- WHEN an EDL is refined THEN the system SHALL preserve intended words while tightening silence by using ASR anchors, waveform evidence, and exportable video frames.
- WHEN a preview is rendered THEN the system SHALL create only a dry PCM WAV/map and SHALL represent every range entry and join in audio QC.
- WHEN a boundary has excessive entry inactivity, a tight lexical attack, residual rejected activity, damaged tail, clipping, or a severe join discontinuity THEN the agent SHALL stop before XML.

### CUT-03 — Editorial coverage

- WHEN a lesson declares required beats THEN the system SHALL verify their evidence in the selected source transcript and the preview transcript before the agent considers the edit ready.
- `metadata.required_beats` SHALL always be present, may be empty, and SHALL express alternative evidence as nested phrase groups referenced by `ranges[].beat_id`.
- WHEN preview audio is rendered THEN it SHALL always be transcribed into a persisted timed-word artifact bound to that WAV hash.
- WHEN a preview has joins THEN transcript QC SHALL compare the expected left suffix/right prefix at every join and block orphan prefixes, crossed joins, missing/deformed expected words, direction cues, or untimed words.

### CUT-05 — Fail-closed workflow

- WHEN the agent exports XML THEN it SHALL have executed `refine -> render WAV/map -> audio QC -> semantic QC -> persist preview transcript/hash -> join transcript QC -> readiness` in that order, and readiness SHALL have returned exit code 0 for the exact current artifacts.
- WHEN an EDL changes THEN every downstream artifact SHALL be considered stale and the workflow SHALL restart at refinement.
- Manual legacy diagnostics or the warning-only manual XML command SHALL NOT substitute for a successful agent workflow gate.

### CUT-04 — Private media

- WHEN local lesson media is used for regression THEN it SHALL remain ignored by Git and absent from public CI artifacts.

## Out Of Scope

- Final-quality video rendering, subtitles, overlays, color grading, animation, publishing, or automatic visual review.
- Fades, video-frame inspection, or visual analysis in the agent QA path.

## Change Log

- 2026-07-11 - Development harness introduced; v0.4.0 audio boundary refinement planned.
- 2026-07-13 - Join-centric audio/transcript QC and the fail-closed v0.4.0 workflow made normative.
- 2026-07-13 - Local CUDA WhisperX/faster-whisper with Community-1 exclusive diarization became the normative transcription source.
