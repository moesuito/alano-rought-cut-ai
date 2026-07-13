# Step 07 - Runtime Revision

Goal: finish editorial/runtime revision, then deterministically refine every boundary to exact source frames.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Read `edit/edl.json`.
2. Determine target runtime:
   - if the user gave a target duration, treat it as the rough target;
   - otherwise use the inferred target from `edit/run_state.md`.
3. If the EDL exceeds target:
   - remove redundant context;
   - trim long lead-ins and tails;
   - drop weaker beats;
   - preserve the core message;
   - preserve the strongest hook, result, and CTA/end moments.
4. If the target duration is unsuitable for content clarity, document the compromise instead of destroying meaning.
5. Update `edit/edl.json` if revised.
6. Once editorial ranges are stable, run the mandatory boundary refiner:

```powershell
.venv\Scripts\python.exe helpers\refine_edl_boundaries.py <edit_dir>\edl.json --transcripts <edit_dir>\transcripts --report <edit_dir>\edl_boundary_qc.json
```

7. Require exit code 0. Exit code 2 means review is still required; exit code 1 is a structural/runtime failure. Do not render a preview after either non-zero result.
8. The refiner automatically splits every selected canonical consecutive-word gap strictly greater than 300ms before acoustic snapping. Confirm `edl_boundary_qc.json.internal_silence_policy` reports the fixed threshold and every preserved gap has an exact reasoned per-gap override.
9. Confirm every range now has integer `source_in_frame` / `source_out_frame`, `review_required: false`, and matching evidence in `edl_boundary_qc.json`.
10. If an editorial change is made after refinement, restart from this refiner command; downstream artifacts are stale.
11. Update `edit/run_state.md` with total duration, revision rationale, EDL/report hashes, and boundary-refiner status.

## Rough Defaults When No Target Exists

- Short social talking-head: 30-90s
- Testimonial/case study: 60-120s
- Tutorial/educational: preserve enough length for clarity
- Podcast/interview excerpt: 60-180s
- Internal/course content: prioritize coherence over aggressive shortening

These are guidance, not hard rules.

## Output

- runtime-revised and boundary-refined `edit/edl.json`
- `edit/edl_boundary_qc.json`
- exact source frames and `review_required: false` on every range
- updated `edit/run_state.md`
