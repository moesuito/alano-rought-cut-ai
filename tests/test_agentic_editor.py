"""Unit tests for the Agentic Editorial Loop Engine."""

import json
from unittest.mock import patch

import pytest

from helpers.agentic_editor import (
    run_agentic_editorial_loop,
    validate_and_normalize_cuts,
)
from helpers.prompts.agentic_prompts import (
    build_phase1_strategy_prompt,
    build_phase2_assembly_prompt,
    build_phase3_reflection_prompt,
    get_agentic_system_prompt,
)


def test_validate_and_normalize_cuts():
    raw_cuts = [
        {"source": "C001", "start": 1.2341, "end": 5.6789, "beat": "INTRO", "quote": "Hello world", "reason": "Good", "is_list": False},
        {"source": "C002", "start": 10.0, "end": 8.0},  # Invalid: end <= start
        {"source": "", "start": 1.0, "end": 3.0},  # Invalid: empty source
        {"source": "C003", "start": "12.5", "end": "15.0", "is_list": True},
    ]

    res = validate_and_normalize_cuts(raw_cuts)
    assert len(res) == 2
    assert res[0]["source"] == "C001"
    assert res[0]["start"] == 1.234
    assert res[0]["end"] == 5.679
    assert res[0]["is_list"] is False

    assert res[1]["source"] == "C003"
    assert res[1]["start"] == 12.5
    assert res[1]["end"] == 15.0
    assert res[1]["is_list"] is True


def test_agentic_prompts_generation():
    short_prompt = get_agentic_system_prompt("reels")
    assert "System Prompt do Agente Editorial Multi-Turno" in short_prompt
    assert "<= 90 segundos" in short_prompt

    long_prompt = get_agentic_system_prompt("aula")
    assert "500ms" in long_prompt

    p1 = build_phase1_strategy_prompt("Test brief", "Sample transcripts")
    assert "TAREFA 1" in p1
    assert "Test brief" in p1

    p2 = build_phase2_assembly_prompt('{"content_type": "aula"}')
    assert "TAREFA 2" in p2

    p3 = build_phase3_reflection_prompt('[{"source": "C001"}]', iteration=1)
    assert "TAREFA 3" in p3
    assert "Loop #1" in p3


@patch("helpers.agentic_editor.send_chat_completion")
def test_agentic_loop_happy_path_approved(mock_send):
    # Mock Phase 1 (Strategy), Phase 2 (Assembly), Phase 3 (Approved)
    p1_response = json.dumps({
        "content_type": "aula",
        "narrative_objective": "Ensinar sobre a plataforma",
        "recording_style": "fragmented_pickups",
        "retake_resolutions": [],
        "elimination_list": ["cacos"],
        "narrative_blocks": [{"beat": "HOOK_INTRO", "source": "C008", "content_summary": "Intro"}],
    })
    p2_response = json.dumps([
        {"source": "C008", "start": 28.0, "end": 42.0, "beat": "HOOK_INTRO", "quote": "Ola a todos", "reason": "Best pickup", "is_list": False}
    ])
    p3_response = json.dumps({
        "status": "APPROVED",
        "critique_notes": ["Cortes limpos e sem defeitos"],
        "refined_ranges": [],
    })

    mock_send.side_effect = [p1_response, p2_response, p3_response]

    res = run_agentic_editorial_loop(
        brief="Briefing de teste",
        takes_packed_content="Takes packed",
        video_type="aula",
        config={"api_key": "fake", "base_url": "http://fake", "model": "fake"},
    )

    assert res["status"] == "APPROVED"
    assert res["iterations_count"] == 1
    assert len(res["edl_ranges"]) == 1
    assert res["edl_ranges"][0]["source"] == "C008"
    assert mock_send.call_count == 3


@patch("helpers.agentic_editor.send_chat_completion")
def test_agentic_loop_with_refinement(mock_send):
    # Mock Phase 1, Phase 2, Phase 3 Loop 1 (Refined), Phase 3 Loop 2 (Approved)
    p1_response = json.dumps({"content_type": "aula"})
    p2_response = json.dumps([
        {"source": "C006", "start": 118.96, "end": 139.0, "beat": "PONTOS_CHAVE", "quote": "Beleza. A plataforma...", "reason": "Draft"}
    ])
    p3_loop1 = json.dumps({
        "status": "REFINED",
        "critique_notes": ["Removeu o caco 'Beleza' do início e iniciou aos 127.58s"],
        "refined_ranges": [
            {"source": "C006", "start": 127.58, "end": 139.0, "beat": "PONTOS_CHAVE", "quote": "A plataforma...", "reason": "Refinado"}
        ],
    })
    p3_loop2 = json.dumps({
        "status": "APPROVED",
        "critique_notes": ["Perfeito agora"],
        "refined_ranges": [],
    })

    mock_send.side_effect = [p1_response, p2_response, p3_loop1, p3_loop2]

    res = run_agentic_editorial_loop(
        brief="",
        takes_packed_content="Takes packed",
        video_type="aula",
        config={"api_key": "fake", "base_url": "http://fake", "model": "fake"},
    )

    assert res["status"] == "APPROVED"
    assert res["iterations_count"] == 2
    assert len(res["edl_ranges"]) == 1
    assert res["edl_ranges"][0]["start"] == 127.58
    assert mock_send.call_count == 4
