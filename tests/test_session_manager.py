"""Unit tests for helpers/session_manager.py."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from helpers.session_manager import (
    SessionContext,
    clean_appdata_cache,
    compute_file_fingerprint,
    create_new_session,
    export_deliverable,
    get_alanocut_appdata_dir,
)


@pytest.fixture(autouse=True)
def isolated_local_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-appdata"))


def test_create_new_session_creates_isolated_structure(tmp_path):
    working_dir = tmp_path / "my_raw_videos"
    working_dir.mkdir()

    session = create_new_session(working_dir=working_dir, video_type="aula", brief="My brief")
    assert session.session_id.startswith("session_")
    assert "my_raw_videos" in session.session_id
    assert session.dir.exists()
    assert session.edit_dir.exists()
    assert session.transcripts_dir.exists()

    meta_file = session.dir / "session.json"
    assert meta_file.exists()
    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_data["video_type"] == "aula"
    assert meta_data["brief"] == "My brief"


def test_export_deliverable_copies_xml_to_user_directory(tmp_path):
    working_dir = tmp_path / "user_project"
    working_dir.mkdir()

    session = create_new_session(working_dir=working_dir, video_type="aula")
    # Simulate generated timeline.xml inside session cache
    fake_xml_content = "<?xml version='1.0'?><xmeml></xmeml>"
    session.session_xml_file.write_text(fake_xml_content, encoding="utf-8")

    exported_file = export_deliverable(session, working_dir, output_filename="timeline.xml")

    assert exported_file.exists()
    assert exported_file == working_dir / "timeline.xml"
    assert exported_file.read_text(encoding="utf-8") == fake_xml_content
    assert session.status == "completed"


def test_export_deliverable_rejects_noncanonical_filename(tmp_path):
    working_dir = tmp_path / "user_project"
    working_dir.mkdir()
    session = create_new_session(working_dir=working_dir, video_type="aula")
    session.session_xml_file.write_text("<xmeml/>", encoding="utf-8")

    with pytest.raises(ValueError, match="fixed to timeline.xml"):
        export_deliverable(session, working_dir, output_filename="other.xml")

    assert not (working_dir / "other.xml").exists()


def test_export_deliverable_preserves_existing_timeline_on_copy_failure(
    tmp_path, monkeypatch
):
    working_dir = tmp_path / "user_project"
    working_dir.mkdir()
    existing = working_dir / "timeline.xml"
    existing.write_text("old", encoding="utf-8")
    session = create_new_session(working_dir=working_dir, video_type="aula")
    session.session_xml_file.write_text("new", encoding="utf-8")

    def fail_copy(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        export_deliverable(session, working_dir)

    assert existing.read_text(encoding="utf-8") == "old"
    assert list(working_dir.glob(".timeline-*.tmp")) == []
