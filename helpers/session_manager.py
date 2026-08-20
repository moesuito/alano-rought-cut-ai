"""Session and Global AppData Cache Manager for Alano Rough Cut AI.

Keeps user video folders 100% clean by storing all intermediate assets,
audio extractions, transcripts, EDLs, and preview renders in:
    %LOCALAPPDATA%\\AlanoCut\\

Only the final deliverable (timeline.xml) is exported to the user's directory.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def get_alanocut_appdata_dir() -> Path:
    """Return root AppData directory for AlanoCut: %LOCALAPPDATA%/AlanoCut."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "AlanoCut"
    else:
        base = Path.home() / ".alanocut"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_global_audio_cache_dir() -> Path:
    """Return path to shared audio extractions and denoised files."""
    d = get_alanocut_appdata_dir() / "cache" / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_global_transcripts_cache_dir() -> Path:
    """Return path to shared transcripts cache."""
    d = get_alanocut_appdata_dir() / "cache" / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_sessions_root_dir() -> Path:
    """Return path to all saved session projects."""
    d = get_alanocut_appdata_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_file_fingerprint(file_path: Path) -> str:
    """Generate lightweight deterministic fingerprint (name + size + mtime) for caching."""
    try:
        st = file_path.stat()
        raw = f"{file_path.name}_{st.st_size}_{st.st_mtime}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(file_path.name).encode("utf-8")).hexdigest()[:16]


@dataclass
class SessionContext:
    session_id: str
    created_at: str
    working_dir: str
    session_dir: str
    video_type: str = "aula"
    brief: str = ""
    source_files: list[dict[str, Any]] = field(default_factory=list)
    cuts_count: int = 0
    total_duration_s: float = 0.0
    timeline_xml_path: str = ""
    status: str = "initialized"

    @property
    def dir(self) -> Path:
        return Path(self.session_dir)

    @property
    def edit_dir(self) -> Path:
        return self.dir / "edit"

    @property
    def transcripts_dir(self) -> Path:
        return self.edit_dir / "transcripts"

    @property
    def takes_packed_file(self) -> Path:
        return self.edit_dir / "takes_packed.md"

    @property
    def edl_file(self) -> Path:
        return self.edit_dir / "edl.json"

    @property
    def preview_wav_file(self) -> Path:
        return self.edit_dir / "preview.wav"

    @property
    def qc_report_file(self) -> Path:
        return self.edit_dir / "preview_audio_qc.json"

    @property
    def session_xml_file(self) -> Path:
        return self.edit_dir / "timeline.xml"

    @property
    def session_log_file(self) -> Path:
        return self.dir / "session.log"

    @property
    def audit_txt_file(self) -> Path:
        return self.dir / "editorial_audit.txt"

    def log(self, message: str) -> None:
        """Write timestamped log line to session.log."""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            with self.session_log_file.open("a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass

    def save(self) -> None:
        """Persist session metadata to session.json."""
        self.edit_dir.mkdir(parents=True, exist_ok=True)
        meta_file = self.dir / "session.json"
        meta_file.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def create_new_session(working_dir: Path, video_type: str = "aula", brief: str = "") -> SessionContext:
    """Create a new isolated session folder in AppData/AlanoCut/sessions."""
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:6]
    folder_name = working_dir.name.replace(" ", "_")
    sanitized_folder = "".join(c if c.isalnum() or c in "_-" else "_" for c in folder_name)
    session_id = f"session_{timestamp_str}_{sanitized_folder}_{short_uuid}"

    session_dir = get_sessions_root_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    ctx = SessionContext(
        session_id=session_id,
        created_at=now.isoformat(),
        working_dir=str(working_dir.resolve()),
        session_dir=str(session_dir.resolve()),
        video_type=video_type,
        brief=brief,
    )
    ctx.transcripts_dir.mkdir(parents=True, exist_ok=True)
    ctx.save()
    return ctx


def export_deliverable(
    session: SessionContext,
    target_dir: Path,
    output_filename: str = "timeline.xml"
) -> Path:
    """Copy the final timeline XML from AppData session cache to user's working directory."""
    src_xml = session.session_xml_file
    if not src_xml.exists():
        raise FileNotFoundError(f"Session timeline.xml was not found at {src_xml}")

    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_xml = target_dir / output_filename
    shutil.copy2(src_xml, dest_xml)

    session.timeline_xml_path = str(dest_xml)
    session.status = "completed"
    session.save()
    return dest_xml


def list_sessions() -> list[dict[str, Any]]:
    """List all previous sessions stored in AppData."""
    root = get_sessions_root_dir()
    sessions = []
    if not root.exists():
        return sessions

    for d in sorted(root.iterdir(), reverse=True):
        if d.is_dir():
            meta_file = d / "session.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    sessions.append(data)
                except Exception:
                    pass
    return sessions


def clean_appdata_cache(include_sessions: bool = False) -> dict[str, int]:
    """Clean global audio cache and optionally old sessions to free disk space."""
    audio_dir = get_global_audio_cache_dir()
    transcripts_dir = get_global_transcripts_cache_dir()
    sessions_dir = get_sessions_root_dir()

    deleted_files = 0
    deleted_dirs = 0

    for c_dir in [audio_dir, transcripts_dir]:
        if c_dir.exists():
            for item in c_dir.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)
                    deleted_files += 1
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    deleted_dirs += 1

    if include_sessions and sessions_dir.exists():
        for s_dir in sessions_dir.iterdir():
            if s_dir.is_dir():
                shutil.rmtree(s_dir, ignore_errors=True)
                deleted_dirs += 1

    return {"deleted_files": deleted_files, "deleted_dirs": deleted_dirs}
