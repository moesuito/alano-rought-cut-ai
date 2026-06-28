# Step 07 - Runtime Revision

Goal: revise the EDL against the target runtime without damaging meaning.

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
6. Update `edit/run_state.md` with total duration and revision rationale.

## Rough Defaults When No Target Exists

- Short social talking-head: 30-90s
- Testimonial/case study: 60-120s
- Tutorial/educational: preserve enough length for clarity
- Podcast/interview excerpt: 60-180s
- Internal/course content: prioritize coherence over aggressive shortening

These are guidance, not hard rules.

## Output

- revised `edit/edl.json`, if needed
- updated `edit/run_state.md`
