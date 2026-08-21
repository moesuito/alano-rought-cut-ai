"""Unit tests for helpers/orchestrator.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from helpers.orchestrator import scan_inventory, probe_video_file


def test_scan_inventory_raises_when_no_videos(tmp_path):
    empty_dir = tmp_path / "empty_raw"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="No video files"):
        scan_inventory(empty_dir)


def test_scan_inventory_discovers_video_files(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "clip1.mov").write_bytes(b"mock video 1")
    (raw_dir / "clip2.mp4").write_bytes(b"mock video 2")
    (raw_dir / "notes.txt").write_text("not a video")

    with patch("helpers.orchestrator.probe_video_file", return_value={"fps": "30000/1001", "width": 1920, "height": 1080, "duration": 15.0}):
        items = scan_inventory(raw_dir)
        assert len(items) == 2
        assert items[0]["source_id"].startswith("SRC_")
        assert items[1]["source_id"].startswith("SRC_")
        assert items[0]["source_id"] != items[1]["source_id"]
        assert items[0]["original_source_id"] == "clip1"
        assert items[1]["original_source_id"] == "clip2"
        assert items[0]["meta"]["duration"] == 15.0


def test_scan_inventory_rejects_duplicate_stems(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "clip.mov").write_bytes(b"one")
    (raw_dir / "CLIP.mp4").write_bytes(b"two")

    with pytest.raises(ValueError, match="Duplicate media stems"):
        scan_inventory(raw_dir)


def test_scan_inventory_source_ids_are_deterministic(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    media = raw_dir / "camera-a.mov"
    media.write_bytes(b"first")

    with patch("helpers.orchestrator.probe_video_file", return_value={"fps": "30/1", "width": 1920, "height": 1080, "duration": 2.0}):
        first = scan_inventory(raw_dir)[0]["source_id"]
        media.write_bytes(b"different bytes")
        second = scan_inventory(raw_dir)[0]["source_id"]

    assert first == second


def test_public_orchestrator_has_no_legacy_editor_fallback():
    source = Path("helpers/orchestrator.py").read_text(encoding="utf-8")

    assert "run_agentic_editorial_loop" not in source
    assert "generate_editorial_plan" not in source
    assert "one-shot" not in source
