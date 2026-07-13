# v0.4.0 Audio-Exact Boundary Refinement

## Requirement Traceability

| Requirement | Task | Commit |
| --- | --- | --- |
| CUT-02 | F1.1, F1.2 | pending |
| CUT-03 | F1.3 | pending |
| CUT-01, CUT-04 | F1.4 | pending |
| CUT-01, CUT-02, CUT-03 | F1.5 | `cabe8d6` |
| CUT-02 | F1.6 | pending |
| CUT-02, CUT-03 | F1.7 | pending |

## Ordered Tasks

1. F1.1 — rational timing, bundled RNNoise model, cached audio analysis, and in-place EDL boundary refinement.
2. F1.2 — dry frame-exact WAV preview, timeline sample map, and join audio QC.
3. F1.3 — required beat validation, mandatory preview transcript binding, and readiness gate.
4. F1.4 — XML parity, workflow/docs/installer migration, full test suite, and v0.4.0 preparation.
5. F1.5 — fail-closed pipeline integration, timed-transcript enforcement, and rational XML/WAV parity.
6. F1.6 — adaptive lexical/acoustic boundary selection, rejected-neighbor guarding, and local confidence.
7. F1.7 — join-centric audio/transcript QC plus mandatory product-workflow integration.

The tasks are sequential because they share the timing and EDL contract. Every task creates one local commit; the ORCHESTRATOR reviews it before activating the next task.

## Manual Regression Finding — 2026-07-13

The first C022 Premiere stint invalidated the earlier release-ready assumption:

- the product workflow called the legacy boundary validator instead of `refine_edl_boundaries.py`;
- the rendered EDL had no `source_in_frame` / `source_out_frame` but still passed readiness;
- the preview transcript had no timed words but still passed;
- the WAV map used 30 fps while the XML used a 24 fps fallback for the WAV-only source;
- a rejected `Corta` component remained acoustically connected to the intended `manter` entry.

F1.5 repairs the fail-open contracts first. F1.6 repairs adaptive boundary selection. F1.7 validates every rendered join and wires the mandatory workflow without introducing a global fixed trim or fixed pre-roll.
