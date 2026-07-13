# Roadmap - Alano Cut

Status: living document.  
Updated: 2026-07-13

## F0 — Development Harness

| Branch | Goal | Status |
| --- | --- | --- |
| `codex/chore-dev-harness` | Durable development roles, worktree flow, and Python validation contract. | in progress |

## F1 — Audio-Exact Boundary Refinement (v0.4.0)

| Task | Goal | Status |
| --- | --- | --- |
| F1.1 | Rational timing, bundled RNNoise model, in-place boundary refiner. | completed |
| F1.2 | Frame-exact dry WAV preview and baseline join QC. | completed |
| F1.3 | Required beats, mandatory preview transcript, readiness gate. | completed |
| F1.4 | XML parity, docs, installer, tests, and v0.4.0 release preparation. | completed |
| F1.5 | Fail-closed exact-frame/map/readiness hardening. | completed |
| F1.6 | Adaptive lexical/acoustic boundary selection and rejected-neighbor guards. | completed |
| F1.7 | Join-centric audio/transcript QC and mandatory product-workflow integration. | in progress |

## Later

- v0.5.0: remove the legacy `timeline_view.py` and `validate_edl_boundaries.py` manual-diagnostic paths after migration evidence.
