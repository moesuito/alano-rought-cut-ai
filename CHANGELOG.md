# Changelog

## Unreleased

### Documentation

- Clarified that `alanocut update` refreshes the global installation and existing workspaces require `alanocut init` to copy the new harness locally.

## v0.3.0 - 2026-07-10

### Changed

- Made the full-context capable-agent protocol the default operating path.
- Kept the existing step-by-step context-loading policy as a constrained-agent fallback.
- Replaced model-name-only routing with capability-based routing while retaining common capable-agent names as hints.
- Added `.agents/core/capable_agent_protocol.md` as a unified end-to-end protocol.
- Restored `SKILL.md` as an active protocol router instead of a deprecated compatibility stub.
- Made core invariants, workflow, and step modules the single normative source for both protocols.
- Promoted source coverage, transcript coverage, editorial planning, EDL integrity, QC, and handoff gates into shared modules.
- Added shared completion criteria so capable and constrained agents use identical quality standards.
- Added operating-protocol tracking to `edit/run_state.md`.

## v0.2.0 - 2026-06-27

Large harness update for the Alano Rough Cut AI workflow.

### Added

- Modular `AGENTS.md` + `.agents/` workflow with step-specific loading.
- Archetype modules for common rough-cut formats.
- Boundary QC helper: `helpers/validate_edl_boundaries.py`.
- Preview transcript QC helper: `helpers/preview_transcript_qc.py`.
- XML-to-EDL round-trip helper: `helpers/fcpxml_to_edl.py`.
- Context-aware Premiere project/sequence names in `helpers/edl_to_fcpxml.py`.
- `helpers/transcribe.py --force` to refresh preview transcripts after re-rendering.
- Persistent harness validation report: `HARNESS_IMPROVEMENT_REPORT.md`.

### Changed

- `SKILL.md` is now a compatibility stub; `AGENTS.md` is the canonical entrypoint.
- `alanocut init` copies the modular `.agents/` instruction tree.
- `alanocut update` now compares semantic versions and avoids downgrading a newer local build to an older release.
- Preview QC now expects boundary QC and, when possible, preview transcript QC before XML export.
- XML export now uses `metadata.timeline_name` or `--timeline-name` instead of a hardcoded timeline name.

### Fixed

- Avoided stale `transcripts/preview.json` by adding forced preview transcription.
- Avoided over-trusting ASR cut boundaries by combining word timing with waveform energy.
- XML round-trip import now prefers local `--media-root` files over stale absolute XML `pathurl` locations.

## v0.1.1

- Bumped config version to `v0.1.1`.

## v0.1.0

- Initial global installer and `alanocut init` workflow.
