# Step 05 - Editorial Strategy

Goal: choose the narrative structure and summarize the editorial plan before EDL generation.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file
- only the matching archetype file under `.agents/archetypes/`

If the material is genuinely ambiguous, load at most two archetype files.

## Tasks

1. Choose the narrative structure from the selected archetype, adapt it, combine it, or invent a better custom structure.
2. Do not force every archetype beat if the content does not support it.
3. Assemble by story logic, not source file order.
4. Prioritize the strongest coherent sequence.
5. Write concise pre-scan notes to `edit/run_state.md`:
   - likely narrative structure;
   - obvious false starts;
   - repeated takes / alternate versions;
   - best hook candidates;
   - strongest proof/result moments;
   - weak or tangent sections to cut;
   - must-preserve moments;
   - unclear moments needing visual inspection.

## Rules

- Do not ask for approval.
- Ask only if missing information would materially harm the edit.
- Do not create hand-scored moment rankings.
- Use editorial judgment and document assumptions.

## Output

- updated `edit/run_state.md` with strategy and pre-scan notes

Do not generate an EDL in this step.
