# Step 06 - EDL Generation

Goal: select the rough-cut ranges and write `edit/edl.json`.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file
- `.agents/prompts/editor_subagent.md` if using a sub-agent
- the selected archetype file, if helpful

## Editorial Selection Rules

- Use LLM editorial reasoning to select ranges.
- Identify retakes by meaning, not exact wording.
- Compare clarity, confidence, concision, energy, continuity, and whether the take resolves earlier mistakes.
- The latest take is often, but not always, the best.
- Do not keep a semantically wrong phrase just because the surrounding take is cleaner.
- If one take has the best surrounding delivery but another failed/partial take contains the correct noun, entity, or qualifier, create a small composite repair from word-boundary ranges instead of accepting the wrong meaning.
- Remove false starts, self-corrections, filler-heavy phrasing, repeated attempts, and tangents.
- Do not remove phrases needed for meaning.
- Preserve emotional peaks, punchlines, laughs, strong result statements, and clean CTA/end beats.
- Preserve natural cadence.
- Do not over-tighten.
- Use word boundaries for all cut edges.
- Use raw Scribe JSON for exact word timestamps when trimming inside packed phrases.
- Pad cut boundaries within the 30-200ms working window to absorb ASR timestamp drift.
- Prefer silences >= 400ms as cut targets.
- Treat 150-400ms phrase boundaries as usable with care and visual/audio inspection.
- Treat gaps < 150ms as unsafe unless there is a strong editorial reason.
- Give speaker handoffs enough air when needed; 400-600ms is a common range for natural turns.

## Retake And Semantic Repair

- Build the edit by intended beat, not by source clip order.
- When resolving repeated takes, note which take wins each beat and why.
- If a correction requires stitching a short phrase from a rejected take into an otherwise good take, keep the stitched ranges separate in `edl.json`.
- In the stitched range `reason`, document the semantic repair explicitly, for example: "uses correct pessoa juridica phrase from alternate take; surrounding take had cleaner delivery."
- After any stitched repair, Step 08 must validate the join with both waveform boundary QC and preview transcript QC.

## EDL Format

Write `edit/edl.json` using the current Alano format:

```json
{
  "version": 1,
  "sources": {
    "C0103": "/abs/path/C0103.MP4"
  },
  "ranges": [
    {
      "source": "C0103",
      "start": 2.42,
      "end": 6.85,
      "beat": "HOOK",
      "quote": "...",
      "reason": "..."
    }
  ],
  "total_duration_s": 4.43
}
```

## Tasks

1. Read `edit/takes_packed.md`.
2. Use `edit/run_state.md` strategy and pre-scan notes.
3. Resolve repeated takes and alternate versions.
4. Mark any semantic repair candidates where a short phrase from a rejected take should replace a wrong phrase in the cleaner take.
5. Select ranges with word-boundary start/end times.
6. Add appropriate 30-200ms edge padding without cutting into neighboring words or unwanted filler.
7. Use raw JSON word timestamps for exact inside-phrase cuts.
8. Calculate `total_duration_s`.
9. Write `edit/edl.json`.
10. Update `edit/run_state.md` with selected range summary, stitched repairs, and known compromises.

## Output

- `edit/edl.json`
- updated `edit/run_state.md`
