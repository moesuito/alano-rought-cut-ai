# Changelog

## v0.6.0 - 2026-08-20

### Added
- **Autonomous Agentic Editorial Loop Engine (`helpers/agentic_editor.py`)**: Multi-turn sequential cognitive loop managing Task 1 (Strategy & Retake Mapping), Task 2 (Assembly), and Task 3 (Self-Critique & Autonomous Refinement Loop with dynamic `APPROVED` sign-off).
- **Modular Task Prompts (`helpers/prompts/agentic_prompts.py`)**: Specialized prompt contracts for narrative strategy, assembly, and quality supervisor reflection.
- **Dedicated Agentic System Prompt (`helpers/prompts/agentic_editor_system_prompt.md`)**: Editable Markdown source for the multi-turn editor persona and durable editorial knowledge.
- **Master Senior Editor System Prompt (`helpers/prompts/editor_system_prompt.md`)**: 46-section exhaustive reference prompt for one-shot mode.
- **Orchestrator Mode Switch (`--mode agentic` vs `--mode one-shot`)**: Full integration into `helpers/orchestrator.py` with telemetry recorded to `editorial_audit.txt` and `session.log`.
- **Unit Test Suite for Agentic Loop (`tests/test_agentic_editor.py`)**: Validates normalization, prompt formatting, happy-path approval, and iterative refinement.
- **Technical Architecture Documentation (`docs/AGENTIC_LOOP_V0.6.0.md`)**: In-depth design guide detailing single-agent multi-turn loop mechanics.

### Changed
- Consolidated the repository documentation around the local-first v0.6 architecture, provisional terminal UI, and future local-model/desktop-GUI direction.
- Declared Whisper Vulkan, Wav2Vec2 DirectML, Pyannote ONNX DirectML, and DeepFilterNet as the canonical transcription/acoustic stack.
- Marked WhisperX, ElevenLabs, AssemblyAI, and one-shot paths as legacy pending a dependency-aware cleanup.
- Aligned project, CLI, and configuration version metadata to `v0.6.0`.
- Corrected the session location to `%LOCALAPPDATA%/AlanoCut` and removed stale worktree paths from developer instructions.

## v0.5.0 - 2026-08-15

### Added
- Embedded Autonomous Orchestrator (`helpers/orchestrator.py`) supporting OpenAI-compatible LLM backends (NVIDIA NIM, Ollama, OpenAI, Groq).
- Zero-pollution session architecture storing all intermediate artifacts in `%LOCALAPPDATA%/AlanoCut/sessions/`.
- Interactive TUI (`alanocut`) with Rich spinners, format configuration, and direct delivery of `timeline.xml`.
- Wav2Vec2 CTC DirectML GPU forced alignment engine with pause-based chunking and intra-word trellis jump suppression.

## v0.4.0 - 2026-07-11

### Added
- Added guided terminal setup for a per-workspace ElevenLabs Scribe or CUDA WhisperX profile, with secret-free `alanocut.json` settings and global-only credentials.
- Added optional Community-1 diarization, a no-diarization pinned Silero VAD profile, model prefetch/doctor, and canonical ElevenLabs Scribe transcript conversion.
- Added a shared, pinned Python 3.12 CUDA transcription runtime with WhisperX 3.8.6, faster-whisper `large-v3`, Pyannote Community-1, runtime doctor, and installer command.
- Added a canonical hash-bound transcript schema requiring 100% forced-aligned word timing and Community-1 speaker IDs.
- Added `verify_edit_ready.py` gate validation prior to XML timeline export in Step 09.
- Added non-blocking QC verification warning when executing `edl_to_fcpxml.py` manually.
- Support for `source_in_frame` / `source_out_frame` in XML conversion, matching preview frames exactly.
- Added opt-in private regression testing using `ALANOCUT_LESSON08_DIR` environment variable.
- Added join identity/evidence to preview artifacts and join-centric audio/transcript validation.
- Added mandatory `required_beats` alternatives and `beat_id` coverage checks.

### Changed
- Made the selected workspace transcription profile normative for both source and preview; ElevenLabs and WhisperX never silently fall back into one another.
- Bound contextual preview transcription to the exact provider/configuration provenance shared by the EDL's source transcripts.
- Shifted the workflow from the legacy video preview to audio-only `preview.wav` and `preview_timeline.json` artifacts.
- Made the gate order mandatory: refine -> render -> audio QC -> semantic QC -> persisted preview transcript/hash -> join transcript QC -> readiness exit 0 -> XML.
- Kept global transcript similarity/recall as supplemental evidence and removed project-specific domain assumptions from generic QC.
- Marked `timeline_view.py` as legacy, slated for removal in v0.5.0.
- Updated README, installation instructions, and modular steps.

### Fixed
- Made every selected canonical word gap strictly above 300ms an automatic jump cut, with exact-300ms retention, reasoned per-gap overrides, and an independently recomputed readiness gate.
- Kept disconnected breaths/transients out of new jump-cut tails; when two clean tail frames are physically unavailable, the one-frame constraint is explicit and verified by preview audio plus the second WhisperX pass.
- Prevented legacy, unrefined, untimed, incomplete-join, or stale artifact sets from being treated as ready for agent-driven XML export.
- Prevented long CTC blank spans from swallowing omitted recording cues; windowed two-pass consensus now recovers cues and re-aligns them against raw/RNNoise activity without trusting ordinary secondary-ASR text.
- Prevented auditable out-of-selection acoustic activity from forcing an identical WhisperX retranscription on every run; provisional caches now continue to the mandatory selected-interval readiness audit.
- Prevented short residual energy between words from hiding an omitted token; selected residuals now require consecutive neighbor alignment and an empty mapped interval in the independent preview transcription.

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
