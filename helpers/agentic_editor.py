"""Autonomous Agentic Editorial Loop Engine (Alano Rough Cut AI v0.6.0).

Executes a dynamic multi-turn cognitive editing loop:
1. Strategy Phase: Ingests all transcripts, resolves retakes, maps structure.
2. Assembly Phase: Translates strategy into initial EDL cut ranges.
3. Reflection & Critique Loop: Inspects draft EDL, corrects defects, and iterates
   until the Editor approves the cut or reaches max iterations.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from helpers.llm_client import clean_json_response, get_llm_config, send_chat_completion
from helpers.prompts.agentic_prompts import (
    build_phase1_strategy_prompt,
    build_phase2_assembly_prompt,
    build_phase3_reflection_prompt,
    get_agentic_system_prompt,
)

logger = logging.getLogger(__name__)


def validate_and_normalize_cuts(raw_cuts: list[Any]) -> list[dict[str, Any]]:
    """Validate and filter cut range dictionaries from LLM output."""
    validated = []
    for i, cut in enumerate(raw_cuts):
        if not isinstance(cut, dict):
            continue
        source = cut.get("source")
        start = cut.get("start")
        end = cut.get("end")
        if not source or start is None or end is None:
            continue
        try:
            start_f = float(start)
            end_f = float(end)
            if end_f <= start_f:
                continue
        except (ValueError, TypeError):
            continue

        validated.append({
            "source": str(source).strip(),
            "start": round(start_f, 3),
            "end": round(end_f, 3),
            "beat": str(cut.get("beat") or f"BEAT_{i+1}").strip(),
            "quote": str(cut.get("quote") or "").strip(),
            "reason": str(cut.get("reason") or "Take selecionado").strip(),
            "is_list": bool(cut.get("is_list", False)),
        })
    return validated


def run_agentic_editorial_loop(
    brief: str,
    takes_packed_content: str,
    video_type: str = "aula",
    config: dict[str, str] | None = None,
    max_reflection_loops: int = 4,
    progress_callback: Callable[[str, str], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute the multi-turn agentic editorial loop from strategy to reflection approval."""
    if config is None:
        config = get_llm_config()

    def _log(msg: str) -> None:
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    def _progress(step: str, detail: str) -> None:
        if progress_callback:
            progress_callback(step, detail)

    _log(">>> Starting Agentic Editorial Engine (Multi-Turn Cognitive Loop) <<<")

    system_prompt = get_agentic_system_prompt(video_type=video_type)
    conversation: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]

    # -------------------------------------------------------------
    # Phase 1: Diagnosis & Editorial Strategy
    # -------------------------------------------------------------
    _progress("4.1", "🧠 Agente Fase 1: Diagnóstico e Estratégia Editorial...")
    _log("[Phase 1] Formulating Global Editorial Strategy and Retake Mapping...")

    p1_user_msg = build_phase1_strategy_prompt(
        brief=brief,
        takes_packed_content=takes_packed_content,
    )
    conversation.append({"role": "user", "content": p1_user_msg})

    p1_response = send_chat_completion(
        messages=conversation,
        config=config,
        temperature=0.1,
        max_tokens=3000,
    )
    conversation.append({"role": "assistant", "content": p1_response})

    cleaned_strategy_json = clean_json_response(p1_response)
    try:
        strategy_data = json.loads(cleaned_strategy_json)
        _log(f"[Phase 1] Strategy resolved: Type={strategy_data.get('content_type')}, Objective='{strategy_data.get('narrative_objective')}'")
    except Exception as e:
        _log(f"[Phase 1 Warning] Strategy returned non-strict JSON ({e}). Raw: {p1_response[:200]}...")
        strategy_data = {"raw_strategy": p1_response}

    # -------------------------------------------------------------
    # Phase 2: Sequence Assembly & Draft EDL
    # -------------------------------------------------------------
    _progress("4.2", "✂️ Agente Fase 2: Decupagem e Montagem da Timeline Preliminar...")
    _log("[Phase 2] Translating Strategy into Initial EDL Cut Ranges...")

    p2_user_msg = build_phase2_assembly_prompt(
        strategy_json_str=json.dumps(strategy_data, indent=2, ensure_ascii=False)
    )
    conversation.append({"role": "user", "content": p2_user_msg})

    p2_response = send_chat_completion(
        messages=conversation,
        config=config,
        temperature=0.1,
        max_tokens=3500,
    )
    conversation.append({"role": "assistant", "content": p2_response})

    cleaned_p2_json = clean_json_response(p2_response)
    try:
        raw_draft_cuts = json.loads(cleaned_p2_json)
    except Exception as e:
        raise ValueError(f"Phase 2 assembly failed to return valid JSON list: {e}\nRaw: {p2_response}") from e

    current_edl_ranges = validate_and_normalize_cuts(raw_draft_cuts)
    _log(f"[Phase 2] Preliminary EDL assembled: {len(current_edl_ranges)} cuts.")

    # -------------------------------------------------------------
    # Phase 3: Autonomous Reflection & Refinement Loop
    # -------------------------------------------------------------
    reflection_history: list[dict[str, Any]] = []
    loop_count = 0
    final_status = "PENDING"

    while loop_count < max_reflection_loops:
        loop_count += 1
        _progress("4.3", f"🔍 Agente Fase 3: Auto-Reflexão e Auditoria Crítica (Ciclo #{loop_count})...")
        _log(f"[Phase 3 - Loop #{loop_count}] Inspecting current EDL ({len(current_edl_ranges)} cuts) for defects or cacos...")

        edl_summary_json = json.dumps(current_edl_ranges, indent=2, ensure_ascii=False)
        p3_user_msg = build_phase3_reflection_prompt(
            current_edl_json_str=edl_summary_json,
            iteration=loop_count,
        )
        conversation.append({"role": "user", "content": p3_user_msg})

        p3_response = send_chat_completion(
            messages=conversation,
            config=config,
            temperature=0.1,
            max_tokens=3500,
        )
        conversation.append({"role": "assistant", "content": p3_response})

        cleaned_p3_json = clean_json_response(p3_response)
        try:
            critique_data = json.loads(cleaned_p3_json)
        except Exception as e:
            _log(f"[Phase 3 Warning] Critique JSON parse error ({e}). Assuming approval.")
            critique_data = {"status": "APPROVED", "critique_notes": ["Parse fallback - approved."]}

        status = str(critique_data.get("status", "APPROVED")).upper().strip()
        notes = critique_data.get("critique_notes", [])
        _log(f"[Phase 3 - Loop #{loop_count}] Verdict: {status} | Notes: {notes}")

        reflection_history.append({
            "loop": loop_count,
            "status": status,
            "notes": notes,
        })

        if status == "APPROVED":
            final_status = "APPROVED"
            _log(f"[Phase 3] Cut APPROVED by Chief Editor Agent at Loop #{loop_count}.")
            break
        elif status == "REFINED" and critique_data.get("refined_ranges"):
            refined = validate_and_normalize_cuts(critique_data["refined_ranges"])
            if refined:
                _log(f"[Phase 3] Applied Refinements: Cut count updated from {len(current_edl_ranges)} to {len(refined)}.")
                current_edl_ranges = refined
            else:
                _log("[Phase 3] Refinement list was empty, preserving current cuts.")
        else:
            _log(f"[Phase 3] Loop #{loop_count} concluded.")

    if final_status != "APPROVED":
        final_status = "MAX_LOOPS_REACHED"
        _log(f"[Phase 3] Reached maximum reflection loops ({max_reflection_loops}). Proceeding with current best cut.")

    _log(f">>> Agentic Editorial Loop Completed: {len(current_edl_ranges)} cuts finalized in {loop_count} reflection cycle(s) <<<")

    return {
        "status": final_status,
        "iterations_count": loop_count,
        "strategy": strategy_data,
        "edl_ranges": current_edl_ranges,
        "reflection_history": reflection_history,
        "total_cuts": len(current_edl_ranges),
    }
