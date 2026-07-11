# PRD - Alano Cut

Status: living document.  
Updated: 2026-07-11

## Vision

Alano Cut is a transcript-driven rough-cut harness for talking-head and course footage. It turns an editorial EDL into a Premiere-compatible FCP7 XML while preserving original media and avoiding finishing work.

## Users

- Course/video editor — needs repeatable removal of retakes, direction cues, dead air, and unsafe cut boundaries.
- AI editing agent — needs compact transcripts, explicit gates, deterministic helpers, and resumable artifacts.

## Requirements

### CUT-01 — Rough-cut XML

- WHEN an approved EDL references original media THEN Alano Cut SHALL export a Premiere-compatible `timeline.xml` without rendering a final delivery video.

### CUT-02 — Audio-safe boundaries

- WHEN an EDL is refined THEN the system SHALL preserve intended words while tightening silence by using ASR anchors, waveform evidence, and exportable video frames.

### CUT-03 — Editorial coverage

- WHEN a lesson declares required beats THEN the system SHALL verify their evidence in the selected source transcript and the preview transcript before the agent considers the edit ready.

### CUT-04 — Private media

- WHEN local lesson media is used for regression THEN it SHALL remain ignored by Git and absent from public CI artifacts.

## Out Of Scope

- Final-quality video rendering, subtitles, overlays, color grading, animation, publishing, or automatic visual review.

## Change Log

- 2026-07-11 - Development harness introduced; v0.4.0 audio boundary refinement planned.
