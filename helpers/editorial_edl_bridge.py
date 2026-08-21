"""Bridge immutable editorial EDLs to the deterministic technical working copy."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from helpers.agent_artifacts import ArtifactError, ArtifactRecord, ArtifactStore


class EditorialEdlBridge:
    """Publish host-approved editorial state and create a path-bearing projection."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        edit_root: Path,
        source_paths: Mapping[str, str | os.PathLike[str]],
    ) -> None:
        self.store = store
        self.edit_root = Path(edit_root)
        self.source_paths = {key: str(value) for key, value in source_paths.items()}
        self.edit_root.mkdir(parents=True, exist_ok=True)
        _reject_reparse(self.edit_root)

    def publish_and_project(self) -> tuple[ArtifactRecord, Path]:
        """Publish the immutable editorial EDL, then atomically project ``edit/edl.json``."""
        editorial = self.store.publish_approved_edl()
        technical = self._technical_projection(editorial.content)
        destination = self.edit_root / "edl.json"
        _atomic_json_replace(destination, technical)
        return editorial, destination

    def _technical_projection(self, editorial: Mapping[str, Any]) -> dict[str, Any]:
        sources = editorial.get("sources")
        if not isinstance(sources, dict) or set(sources) != set(self.source_paths):
            raise ArtifactError("STATE_CONFLICT", "Technical source map differs from editorial EDL")
        for source_id, logical_ref in sources.items():
            if logical_ref != f"source:{source_id}":
                raise ArtifactError("INTEGRITY_FAILURE", "Editorial source reference is invalid")
        projected = deepcopy(dict(editorial))
        projected["sources"] = dict(self.source_paths)
        metadata = projected["metadata"]
        fps = Fraction(metadata["sequence_fps"])
        projected["sequence_name"] = metadata["timeline_name"]
        projected["fps"] = float(fps)
        projected["timebase"] = round(float(fps))
        for item in projected["ranges"]:
            item["beat"] = item["beat_id"]
        return projected


def _reject_reparse(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ArtifactError("NOT_FOUND", "Technical EDL directory is unavailable") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(info.st_mode) or attributes & flag:
        raise ArtifactError("OUTSIDE_ALLOWED_ROOT", "Technical EDL directory is a link")


def _atomic_json_replace(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError("INVALID_JSON", "Technical EDL is not strict JSON") from exc
    _reject_reparse(path.parent)
    if path.exists() or path.is_symlink():
        _reject_reparse(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".edl-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise ArtifactError("STATE_CONFLICT", "Technical EDL could not be published") from exc
    finally:
        temp_path.unlink(missing_ok=True)
