"""Unit tests for helpers/llm_client.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from helpers.llm_client import (
    clean_json_response,
    build_editorial_system_prompt,
    generate_editorial_plan,
    get_llm_config,
)


def test_clean_json_response_handles_markdown_and_think_tags():
    raw_with_think = """<think>
Some internal reasoning here.
</think>
```json
[
  {
    "source": "C001",
    "start": 1.0,
    "end": 5.0,
    "beat": "HOOK",
    "quote": "Hello world",
    "reason": "Best hook",
    "is_list": false
  }
]
```"""
    cleaned = clean_json_response(raw_with_think)
    data = json.loads(cleaned)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["source"] == "C001"
    assert data[0]["start"] == 1.0


def test_clean_json_response_handles_raw_array_with_surrounding_chatter():
    raw = 'Here is the cut plan you asked for:\n[{"source": "C002", "start": 2.5, "end": 8.0, "beat": "INTRO"}]\nHope this helps!'
    cleaned = clean_json_response(raw)
    data = json.loads(cleaned)
    assert len(data) == 1
    assert data[0]["source"] == "C002"


def test_build_editorial_system_prompt_short_vs_long_form():
    prompt_short = build_editorial_system_prompt("reels")
    assert "VÍDEO CURTO" in prompt_short or "SHORT-FORM" in prompt_short
    assert "<= 90 segundos" in prompt_short

    prompt_long = build_editorial_system_prompt("aula")
    assert "VÍDEO LONGO" in prompt_long or "LONG-FORM" in prompt_long
    assert "500ms" in prompt_long


def test_generate_editorial_plan_validates_structure():
    mock_response_json = json.dumps({
        "choices": [
            {
                "message": {
                    "content": json.dumps([
                        {
                            "source": "C001",
                            "start": 10.0,
                            "end": 20.0,
                            "beat": "HOOK",
                            "quote": "Intro quote",
                            "reason": "Clear delivery",
                            "is_list": False
                        },
                        {
                            # Invalid item with end <= start
                            "source": "C001",
                            "start": 30.0,
                            "end": 25.0,
                        },
                        {
                            "source": "C002",
                            "start": 5.0,
                            "end": 15.0,
                            "beat": "BODY",
                            "quote": "Body quote",
                            "reason": "Detailed",
                            "is_list": True
                        }
                    ])
                }
            }
        ]
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_response_json
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        cuts = generate_editorial_plan(
            brief="Test brief",
            takes_packed_content="Packed content",
            video_type="aula",
            config={"api_key": "mock-key", "base_url": "https://api.mock.com", "model": "mock-model"}
        )

        assert len(cuts) == 2
        assert cuts[0]["source"] == "C001"
        assert cuts[0]["start"] == 10.0
        assert cuts[0]["end"] == 20.0
        assert cuts[0]["is_list"] is False

        assert cuts[1]["source"] == "C002"
        assert cuts[1]["start"] == 5.0
        assert cuts[1]["end"] == 15.0
        assert cuts[1]["is_list"] is True
