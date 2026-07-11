# Manual Test Stint - Focused Checklist

> Lives in `docs/active/<branch>/stints/` during dev and is archived to `docs/closed/<branch>/stints/` at merge.
>
> Scope rule (hard): this checklist covers ONLY what changed in code since the last validation — the active/closed task(s) and their direct surface. Do not include already-consolidated flows that were not touched (re-testing untouched, passing flows is wasted effort). A full product regression happens only in the pre-release stint, once all queued tasks are closed.
>
> Interactive UAT (complex user-facing features): run one check at a time, wait for the user, log their response verbatim under Result, and tag severity — **Blocker** (crash/error) · **Major** (wrong/missing) · **Minor** (slow/awkward) · **Cosmetic** (visual). Each failed check becomes a known-issue → correction task.

### 1 - <flow title>

- [ ] <test item>
- [ ] <test item>

#### Result:

> Notes, observations, screenshot links, attention points.

### 2 - <flow title>

- [ ] <test item>
- [ ] <test item>

#### Result:

> ...

## General Notes

> Round-level observations.
