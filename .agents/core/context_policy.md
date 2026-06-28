# Context Policy For Small Models

The instruction system is modular so local/small LLMs do not lose critical rules.

- Never load the entire `.agents/` directory.
- Never load all archetypes.
- Never keep old step details in active context once the step is complete.
- Persist conclusions to `edit/run_state.md` before moving on.
- Use `edit/run_state.md` as the working memory bridge between steps.
- Use `edit/project.md` as long-term memory across sessions.
- When context feels crowded, summarize current decisions into `edit/run_state.md`, then continue from the next step.
- Prefer reading source artifacts on demand:
  - `takes_packed.md` for transcript overview;
  - raw Scribe JSON only when exact word timestamps are needed;
  - `timeline_view.py` only for visual ambiguity or cut-boundary QC.
