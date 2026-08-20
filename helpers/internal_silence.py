"""Deterministic lexical internal-silence contract shared by cut/QC gates."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any


INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS = Decimal("0.350")
INTERNAL_SILENCE_LIST_SPLIT_THRESHOLD_SECONDS = Decimal("0.500")
INTERNAL_SILENCE_SPLIT_POLICY = "lexical_gap_strictly_gt_350ms_v1"
INTERNAL_SILENCE_OVERRIDE_FIELD = "preserve_internal_silences"


def decimal_timestamp(value: Any, label: str) -> Decimal:
    """Parse a JSON timestamp without binary-float threshold drift."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite numeric timestamp")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite numeric timestamp") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite numeric timestamp")
    return parsed


def gap_exceeds_internal_silence_threshold(
    left_end: Any,
    right_start: Any,
    *,
    threshold_seconds: Decimal = INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS,
) -> bool:
    """Compare a lexical gap exactly, preserving the strict threshold boundary."""
    if threshold_seconds < 0:
        raise ValueError("internal silence threshold must be non-negative")
    return (
        decimal_timestamp(right_start, "right word start")
        - decimal_timestamp(left_end, "left word end")
        > threshold_seconds
    )


def _word_identity(value: Any) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").strip().casefold())


def compute_internal_silence_event_id(
    source_id: Any,
    original_selection: Any,
    gaps: Any,
    occurrence: Any,
) -> str:
    """Return a reproducible, collision-safe ID for one selected-span audit."""
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("internal-silence event source is invalid")
    if not isinstance(original_selection, dict):
        raise ValueError("internal-silence original_selection must be an object")
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 0:
        raise ValueError("internal-silence event occurrence must be a non-negative integer")
    if not isinstance(gaps, list) or not gaps:
        raise ValueError("internal-silence event gaps must be a non-empty list")

    canonical_gaps = []
    for position, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            raise ValueError(f"internal-silence event gap {position} is invalid")
        left_index = gap.get("left_word_index")
        right_index = gap.get("right_word_index")
        preserve_override = gap.get("preserve_override")
        override_reason = gap.get("override_reason")
        if (
            not isinstance(left_index, int)
            or isinstance(left_index, bool)
            or not isinstance(right_index, int)
            or isinstance(right_index, bool)
            or right_index != left_index + 1
            or not isinstance(preserve_override, bool)
            or (preserve_override and (not isinstance(override_reason, str) or not override_reason.strip()))
            or (not preserve_override and override_reason is not None)
        ):
            raise ValueError(f"internal-silence event gap {position} identity is invalid")
        left_word_end = decimal_timestamp(gap.get("left_word_end"), "left word end")
        right_word_start = decimal_timestamp(
            gap.get("right_word_start"), "right word start"
        )
        gap_seconds = right_word_start - left_word_end
        if gap_seconds <= INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS:
            raise ValueError(
                f"internal-silence event gap {position} is not strictly over {int(INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS * 1000)} ms"
            )
        declared_gap_seconds = decimal_timestamp(
            gap.get("gap_seconds"), "declared gap seconds"
        )
        declared_gap_ms = decimal_timestamp(gap.get("gap_ms"), "declared gap ms")
        # The persisted JSON contract stores these derived durations as
        # binary floats.  Validate against that exact producer projection,
        # while keeping the authoritative threshold comparison on Decimal
        # endpoint timestamps above.
        projected_gap_seconds = Decimal(str(float(gap_seconds)))
        projected_gap_ms = Decimal(str(float(gap_seconds * 1000)))
        if (
            declared_gap_seconds != projected_gap_seconds
            or declared_gap_ms != projected_gap_ms
        ):
            raise ValueError(
                f"internal-silence event gap {position} derived duration is stale"
            )
        canonical_gaps.append({
            "left_word_index": left_index,
            "left_word": str(gap.get("left_word") or ""),
            "left_word_end": str(left_word_end),
            "right_word_index": right_index,
            "right_word": str(gap.get("right_word") or ""),
            "right_word_start": str(right_word_start),
            "gap_seconds": str(gap_seconds),
            "gap_ms": str(gap_seconds * 1000),
            "preserve_override": preserve_override,
            "override_reason": override_reason.strip() if preserve_override else None,
        })

    payload = {
        "policy": INTERNAL_SILENCE_SPLIT_POLICY,
        "source": source_id,
        "original_selection": {
            "start": str(
                decimal_timestamp(original_selection.get("start"), "selection start")
            ),
            "end": str(
                decimal_timestamp(original_selection.get("end"), "selection end")
            ),
        },
        "gaps": canonical_gaps,
        "occurrence": occurrence,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def find_internal_silence_gaps(
    words: list[dict[str, Any]],
    first_word_index: int,
    last_word_index: int,
    *,
    threshold_seconds: Decimal = INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS,
) -> list[dict[str, Any]]:
    """Return canonical consecutive-word gaps strictly above the threshold."""
    if (
        not isinstance(first_word_index, int)
        or isinstance(first_word_index, bool)
        or not isinstance(last_word_index, int)
        or isinstance(last_word_index, bool)
        or first_word_index < 0
        or last_word_index < first_word_index
        or last_word_index >= len(words)
    ):
        raise ValueError("lexical anchor indices are invalid")
    if threshold_seconds < 0:
        raise ValueError("internal silence threshold must be non-negative")

    gaps: list[dict[str, Any]] = []
    for left_index in range(first_word_index, last_word_index):
        right_index = left_index + 1
        left_word = words[left_index]
        right_word = words[right_index]
        left_end = decimal_timestamp(left_word.get("end"), "left word end")
        right_start = decimal_timestamp(right_word.get("start"), "right word start")
        gap = right_start - left_end
        if not gap_exceeds_internal_silence_threshold(
            left_end,
            right_start,
            threshold_seconds=threshold_seconds,
        ):
            continue
        gaps.append({
            "left_word_index": left_index,
            "left_word": left_word.get("text"),
            "left_word_end": float(left_end),
            "right_word_index": right_index,
            "right_word": right_word.get("text"),
            "right_word_start": float(right_start),
            "gap_seconds": float(gap),
            "gap_ms": float(gap * 1000),
        })
    return gaps


def evaluate_internal_silence_contract(
    range_data: dict[str, Any],
    words: list[dict[str, Any]],
    first_word_index: int,
    last_word_index: int,
) -> list[dict[str, Any]]:
    """Evaluate gaps plus precise, reasoned per-gap preservation overrides."""
    threshold = INTERNAL_SILENCE_SPLIT_THRESHOLD_SECONDS
    if (
        range_data.get("is_list")
        or range_data.get("is_enumeration")
        or (isinstance(range_data.get("boundary_constraints"), dict) and range_data["boundary_constraints"].get("is_list"))
    ):
        threshold = INTERNAL_SILENCE_LIST_SPLIT_THRESHOLD_SECONDS

    custom_max = range_data.get("max_gap_seconds")
    if custom_max is None and isinstance(range_data.get("boundary_constraints"), dict):
        custom_max = range_data["boundary_constraints"].get("max_gap_seconds")
    if custom_max is not None:
        threshold = decimal_timestamp(custom_max, "max_gap_seconds")

    gaps = find_internal_silence_gaps(words, first_word_index, last_word_index, threshold_seconds=threshold)
    constraints = range_data.get("boundary_constraints")
    if constraints is None:
        constraints = {}
    if not isinstance(constraints, dict):
        raise ValueError("boundary_constraints must be an object when present")
    if "preserve_internal_silence" in constraints:
        raise ValueError(
            "boundary_constraints.preserve_internal_silence is an unsafe wildcard; "
            f"use {INTERNAL_SILENCE_OVERRIDE_FIELD} with one reasoned entry per gap"
        )
    overrides = constraints.get(INTERNAL_SILENCE_OVERRIDE_FIELD, [])
    if not isinstance(overrides, list):
        raise ValueError(
            f"boundary_constraints.{INTERNAL_SILENCE_OVERRIDE_FIELD} must be a list"
        )

    gap_by_pair = {
        (gap["left_word_index"], gap["right_word_index"]): gap for gap in gaps
    }
    override_by_pair: dict[tuple[int, int], str] = {}
    for position, override in enumerate(overrides):
        label = f"{INTERNAL_SILENCE_OVERRIDE_FIELD}[{position}]"
        if not isinstance(override, dict):
            raise ValueError(f"{label} must be an object")
        left_index = override.get("left_word_index")
        right_index = override.get("right_word_index")
        reason = override.get("reason")
        if (
            not isinstance(left_index, int)
            or isinstance(left_index, bool)
            or not isinstance(right_index, int)
            or isinstance(right_index, bool)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ValueError(f"{label} requires integer word indices and a non-empty reason")
        pair = (left_index, right_index)
        gap = gap_by_pair.get(pair)
        if gap is None:
            raise ValueError(f"{label} does not identify a selected gap strictly over 300 ms")
        if pair in override_by_pair:
            raise ValueError(f"{label} duplicates an earlier override")
        if _word_identity(override.get("left_word")) != _word_identity(gap["left_word"]):
            raise ValueError(f"{label}.left_word does not match the canonical transcript")
        if _word_identity(override.get("right_word")) != _word_identity(gap["right_word"]):
            raise ValueError(f"{label}.right_word does not match the canonical transcript")
        override_by_pair[pair] = reason.strip()

    evaluated = []
    for gap in gaps:
        pair = (gap["left_word_index"], gap["right_word_index"])
        reason = override_by_pair.get(pair)
        evaluated.append(gap | {
            "preserve_override": reason is not None,
            "override_reason": reason,
        })
    return evaluated
