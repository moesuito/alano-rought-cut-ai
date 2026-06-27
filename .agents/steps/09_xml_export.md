# Step 09 - XML Export

Goal: create the Premiere-compatible XML timeline.

## Load

- `AGENTS.md`
- `.agents/core/invariants.md`
- `edit/run_state.md`
- this file only

## Tasks

1. Run:

```powershell
.venv\Scripts\python.exe helpers\edl_to_fcpxml.py <edit_dir>\edl.json -o <edit_dir>\timeline.xml
```

2. Ensure the XML is Premiere-compatible Final Cut Pro 7 XML / XMEML.
3. Ensure the XML points to original media.
4. Use a single stereo audio track mapping when supported by current code.
5. Do not create a final high-quality MP4.
6. Verify `edit/timeline.xml` exists.
7. If validating an XML corrected by a human/editor, reverse it into EDL for comparison:

```powershell
.venv\Scripts\python.exe helpers\fcpxml_to_edl.py <edit_dir>\timeline_fix.xml -o <edit_dir>\timeline_fix_from_xml.edl.json --media-root <videos_dir>
```

8. Update `edit/run_state.md` with XML path, export status, and any round-trip comparison notes.

## Output

- `edit/timeline.xml`
- updated `edit/run_state.md`
