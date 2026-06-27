# Step 04 - Brief Inference

Goal: infer what the rough cut should become before planning the edit.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Brief Priority Ladder

1. Explicit user brief or `edit/USER_BRIEF.md`.
2. Existing `edit/project.md`.
3. Inference from `takes_packed.md` and source material.
4. Ask only if the missing information would materially harm the edit.

## Tasks

1. Read `edit/USER_BRIEF.md` if present.
2. Read only relevant recent parts of `edit/project.md` if present.
3. Read `edit/takes_packed.md`.
4. Infer content type after reading the material. Do not assume upfront.
5. Infer objective, audience/use, pacing, and rough target duration if missing.
6. Identify uncertainty level:
   - low: proceed;
   - medium: proceed and document assumptions;
   - high: ask a short clarification only if proceeding would likely produce the wrong edit.
7. Update `edit/run_state.md` with:
   - user brief;
   - inferred type;
   - objective;
   - target runtime;
   - selected or likely archetype;
   - uncertainty and reason.

## Content Type Options

- Talking-head social content
- Testimonial / case study
- Sales/VSL style video
- Tutorial / educational content
- Interview / Q&A
- Podcast excerpt
- Course lesson
- Internal/company communication
- Product demo
- Event recap / narrative recap
- Documentary-style narrative
- Other / custom structure

## Output

- updated `edit/run_state.md`

Do not generate an EDL in this step.
