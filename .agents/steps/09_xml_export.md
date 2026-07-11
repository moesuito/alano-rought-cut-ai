# Step 09 - XML Export

Goal: create the Premiere-compatible XML timeline.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Run the readiness gate validation:

```powershell
.venv\Scripts\python.exe helpers\verify_edit_ready.py <edit_dir>\edl.json --transcripts <edit_dir>\transcripts
```

Ensure the gate passes (exit code 0). If it flags reviews or errors, resolve them before proceeding.

2. Run:

```powershell
.venv\Scripts\python.exe helpers\edl_to_fcpxml.py <edit_dir>\edl.json -o <edit_dir>\timeline.xml
```

3. Ensure `edit/edl.json` has `metadata.timeline_name` using the format from Step 06.
   - If missing, pass an explicit override:

```powershell
.venv\Scripts\python.exe helpers\edl_to_fcpxml.py <edit_dir>\edl.json -o <edit_dir>\timeline.xml --timeline-name "reels 35_cadastro_alano-cut"
```

4. Ensure the XML project and sequence names are context-specific, not a generic product/customer name.
5. Ensure the XML is Premiere-compatible Final Cut Pro 7 XML / XMEML.
6. Ensure the XML points to original media.
7. Use a single stereo audio track mapping when supported by current code.
8. Do not create a final high-quality MP4.
9. Verify `edit/timeline.xml` exists and inspect its `<sequence><name>` value when practical.
10. If validating an XML corrected by a human/editor, reverse it into EDL for comparison:

```powershell
.venv\Scripts\python.exe helpers\fcpxml_to_edl.py <edit_dir>\timeline_fix.xml -o <edit_dir>\timeline_fix_from_xml.edl.json --media-root <videos_dir>
```

11. Update `edit/run_state.md` with XML path, timeline name, export status, and any round-trip comparison notes.
12. Record meaningful human corrections in `edit/project.md` during Step 10. The round-trip helper enables comparison; do not claim it learns automatically.

## Output

- `edit/timeline.xml`
- updated `edit/run_state.md`
