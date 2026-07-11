# v0.4.0 Audio-Exact Boundary Refinement

## Requirement Traceability

| Requirement | Task | Commit |
| --- | --- | --- |
| CUT-02 | F1.1, F1.2 | pending |
| CUT-03 | F1.3 | pending |
| CUT-01, CUT-04 | F1.4 | pending |

## Ordered Tasks

1. F1.1 — rational timing, bundled RNNoise model, cached audio analysis, and in-place EDL boundary refinement.
2. F1.2 — dry frame-exact WAV preview, timeline sample map, and join audio QC.
3. F1.3 — required beat validation, mandatory preview transcript binding, and readiness gate.
4. F1.4 — XML parity, workflow/docs/installer migration, full test suite, and v0.4.0 preparation.

The tasks are sequential because they share the timing and EDL contract. Every task creates one local commit; the ORCHESTRATOR reviews it before activating the next task.
