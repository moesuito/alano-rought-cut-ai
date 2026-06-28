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
5. Infer objective, audience/use, pacing, rough target duration, and XML timeline naming fields if missing.
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
   - XML timeline naming fields:
     - video type label, e.g. `reels`, `aula`, `tutorial`, `demo`, `vsl`;
     - content number only if explicit or clearly implied by the brief/source context;
     - concise content slug, e.g. `cadastro`, `envio_documentos`, `onboarding`;
     - final timeline name in the format `<video_type> <number>_<content>_alano-cut` when a number exists, or `<video_type>_<content>_alano-cut` when it does not;
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
