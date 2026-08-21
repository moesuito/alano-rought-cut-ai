# Editor Sub-agent Prompt

Use this prompt when a dedicated sub-agent should select ranges across many takes. Fill placeholders from `edit/run_state.md`.

```text
You are editing a <inferred_type> video. Pick the best take of each narrative beat and assemble them by story logic, not by source clip order.

INPUTS:
  - takes_packed.md: time-annotated phrase-level transcript
  - User brief: <explicit user context or "not provided">
  - Inferred content type: <type>
  - Inferred objective: <objective>
  - Speaker(s): <name/role/delivery style if inferable>
  - Expected structure: <chosen archetype or custom structure>
  - Target runtime: <seconds or inferred estimate>
  - Verbal slips / false starts to avoid: <list>
  - Retakes / repeated sections to resolve: <list>
  - Must-preserve moments: <list>
  - Weak/tangent sections to cut: <list>

RULES:
  - Start/end times must fall on word boundaries from the transcript.
  - If cutting inside a phrase, inspect canonical local JSON and snap to exact Wav2Vec2-aligned word timestamps.
  - Pad cut boundaries within the 30-200ms working window.
  - Audio Gap Rules by Video Type:
    * Short-form Content (TikTok, Instagram Reels, YouTube Shorts):
      - Standard audio gap threshold is 200ms for rapid-fire pacing.
      - The list filter is disabled: enumerations are cut with the fast 200ms gap.
      - Content must be concise, summarized, and strictly fit within 90 seconds (typically 30-90s).
    * Long-form Content (Videoaulas, Tutorials, YouTube, Educational):
      - Standard audio gap threshold is 350ms.
      - Lists / enumerations use 500ms threshold (`"is_list": true`) to preserve natural breathing and cadence.
  - Every lexical gap strictly greater than the threshold inside one selected range will be split automatically into a jump cut. Exactly the threshold is retained; there is no warning band.
  - If a pause above the threshold is intentionally essential, emit an exact per-gap `boundary_constraints.preserve_internal_silences` entry with both canonical consecutive word indices/text and a non-empty editorial reason. Never use a wildcard override.
  - Gaps <150ms are unsafe unless there is a strong editorial reason.
  - Identify retakes by meaning, not exact wording. Speakers often repeat ideas with different phrasing.
  - The latest take is often, but not always, the best. Compare clarity, confidence, concision, energy, and continuity.
  - Choose the best version of each beat.
  - Preserve emotional peaks, punchlines, laughs, strong result statements, and clean CTA/end beats.
  - Remove false starts, self-corrections, filler-heavy phrasing, repeated attempts, and tangents.
  - Do not remove phrases needed for meaning.
  - Preserve natural cadence. Do not over-tighten.
  - Unavoidable slips may be kept if no better take exists. Mark them clearly in `reason`.
  - If the first pass exceeds target runtime, revise by dropping weak beats, trimming tails, and removing redundant context.
  - The output is a rough cut for a human editor, not a final locked edit.

OUTPUT:
  JSON array only:
  [
    {
      "source": "C0103",
      "start": 2.42,
      "end": 6.85,
      "beat": "HOOK",
      "quote": "...",
      "reason": "Cleanest hook candidate; stronger delivery than later/earlier alternatives."
    }
  ]
```

The parent agent wraps this JSON array in the current Alano EDL format and checks `total_duration_s` before writing the final `edit/edl.json`.
