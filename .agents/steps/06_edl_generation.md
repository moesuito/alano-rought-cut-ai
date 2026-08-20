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
- Use canonical provider JSON for exact word timestamps when trimming inside packed phrases; WhisperX timestamps are forced-aligned.
- Every lexical gap strictly greater than 350ms inside a standard selected range is a mandatory jump cut. Step 07 enforces this deterministically from canonical provider word timestamps.
- For lists and enumerations (e.g. sequence of items, features, products), pauses up to 500ms represent natural cadence and must NOT be chopped into micro-cuts. Mark the range with `"is_list": true` or `"max_gap_seconds": 0.500`.
- A gap of exactly 350ms (or 500ms in lists) remains untouched. There is no warning band.
- Treat gaps < 150ms as unsafe unless there is a strong editorial reason.
- When a speaker handoff genuinely needs 400-600ms of air, preserve that exact gap through the reasoned per-gap override below.
- Preserve an intentional gap over 350ms (or over 500ms in lists) only with a per-gap `boundary_constraints.preserve_internal_silences` entry containing the exact consecutive canonical word indices/text and a non-empty editorial reason. Wildcard range overrides are forbidden.

## Retake And Semantic Repair

- Build the edit by intended beat, not by source clip order.
- When resolving repeated takes, note which take wins each beat and why.
- If a correction requires stitching a short phrase from a rejected take into an otherwise good take, keep the stitched ranges separate in `edl.json`.
- In the stitched range `reason`, document the semantic repair explicitly, for example: "uses the corrected qualifier from an alternate take; the surrounding take had cleaner delivery."
- After any stitched repair, Step 07 must refine both edges and Step 08 must validate the mapped join in both preview audio QC and timed preview transcript QC.

## EDL Format

Write `edit/edl.json` using the current Alano format:

```json
{
  "version": 1,
  "metadata": {
    "timeline_name": "reels 35_cadastro_alano-cut",
    "video_type": "reels",
    "content_number": "35",
    "content_slug": "cadastro",
    "sequence_fps": "30000/1001",
    "required_beats": [
      {
        "id": "SECRET_KEY_ONCE",
        "description": "Explain that the secret key is shown only once",
        "evidence_any_of": [
          ["secret key", "only once"],
          ["download", "copy immediately"]
        ]
      }
    ]
  },
  "sources": {
    "C0103": "/abs/path/C0103.MP4"
  },
  "ranges": [
    {
      "source": "C0103",
      "start": 2.42,
      "end": 6.85,
      "beat_id": "SECRET_KEY_ONCE",
      "quote": "...",
      "reason": "..."
    }
  ],
  "total_duration_s": 4.43
}
```

`metadata.required_beats` is mandatory and may be an empty list when the edit has no required editorial beat. Each `evidence_any_of` entry is an alternative group; every phrase inside one group must be supported by the selected source transcript. Use product/project language only in the project EDL, never as a generic QC rule.

An intentional pause above the automatic threshold must be narrow and auditable:

```json
{
  "boundary_constraints": {
    "preserve_internal_silences": [
      {
        "left_word_index": 116,
        "left_word": "Salvar.",
        "right_word_index": 117,
        "right_word": "Um",
        "reason": "Pausa narrativa intencional"
      }
    ]
  }
}
```

## Timeline Naming

- Always include `metadata.timeline_name` in `edit/edl.json`.
- Use the final format `<video_type> <number>_<content>_alano-cut` when a content number is explicit or clearly implied.
- If there is no number, use `<video_type>_<content>_alano-cut`.
- Use concise lowercase slugs for the content part: `cadastro`, `envio_documentos`, `primeiros_passos`.
- Do not invent a number if the brief/source context does not indicate one.
- Examples: `reels 35_cadastro_alano-cut`, `aula 35_cadastro_alano-cut`, `tutorial_envio_documentos_alano-cut`.

## Tasks

1. Read `edit/takes_packed.md`.
2. Use `edit/run_state.md` strategy and pre-scan notes.
3. Resolve repeated takes and alternate versions.
4. Mark any semantic repair candidates where a short phrase from a rejected take should replace a wrong phrase in the cleaner take.
5. Select ranges with word-boundary start/end times.
6. Do not invent fixed padding; preserve the intended lexical attack and leave exact acoustic/frame snapping to Step 07.
7. Use raw JSON word timestamps for exact inside-phrase selections.
8. Calculate `total_duration_s`.
9. Write `edit/edl.json` with `metadata.timeline_name`, rational `metadata.sequence_fps`, mandatory `metadata.required_beats`, and `ranges[].beat_id` references.
10. Update `edit/run_state.md` with selected range summary, timeline name, stitched repairs, and known compromises.

## EDL Integrity Gate

Before runtime revision, verify:

- every `ranges[].source` key exists in `sources` and resolves to the intended original media;
- every range has numeric boundaries with `end > start`;
- ranges appear in intended timeline order;
- every cut edge follows the word-boundary rules above;
- `total_duration_s` matches the sum of all range durations within normal rounding tolerance;
- `metadata.timeline_name` follows the naming rules above.
- `metadata.sequence_fps` is a valid rational rate shared by all CFR sources;
- `metadata.required_beats` is present, and every non-empty beat referenced by a range uses `beat_id`.

## Output

- `edit/edl.json`
- updated `edit/run_state.md`
