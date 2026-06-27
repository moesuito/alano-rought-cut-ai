# Invariants

Keep these rules in context throughout the run.

- Final deliverable is `<videos_dir>/edit/timeline.xml`.
- All session outputs go to `<videos_dir>/edit/`.
- After Step 01, `edit/...` is shorthand for `<videos_dir>/edit/...`.
- Never write session output inside the repo folder.
- Never cut inside a word.
- Do not trust ASR boundary timestamps alone when a cut feels tight; validate against waveform energy too.
- Use word-level verbatim ASR.
- Use raw Scribe JSON word timestamps when trimming inside a packed phrase.
- `takes_packed.md` is the primary reading view, but not the final edit.
- The transcript is the map. The LLM is the editor.
- Use LLM editorial judgment, not deterministic scoring algorithms.
- Cache transcripts per source.
- Do not re-transcribe unchanged files.
- XML should point to original media.
- Preview renders are for QA only.
- Preview/fixed timeline transcripts are QA artifacts; use them to catch duplicated, clipped, or semantically wrong final content.
- Do not create a final high-quality MP4.
- Do not add finishing features: subtitles, overlays, color grading, animations, Remotion, Manim, HyperFrames, YouTube download, publishing, or final-render features.
- Ask the user only when missing information would materially harm the edit.
