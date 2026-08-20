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
        assert items[0]["source_id"] == "clip1"
        assert items[1]["source_id"] == "clip2"
        assert items[0]["meta"]["duration"] == 15.0
