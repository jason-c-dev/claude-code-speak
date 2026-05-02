"""Per-session state file at ~/.claude/voice/state/<session_id>.json."""
from __future__ import annotations
import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from scripts.log import get_logger, voice_home


def state_dir() -> Path:
    d = voice_home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class SessionState:
    session_id: str
    current_pid: int | None = None
    queue: list[str] = field(default_factory=list)  # paths of pending audio files


def _path(session_id: str) -> Path:
    """Return the state file path for session_id, pinned inside state_dir().

    Defensive: replace path separators and reject ids that would escape the
    state directory. Claude Code session ids are opaque in practice; this
    is belt-and-suspenders.
    """
    safe = session_id.replace("/", "_").replace("\\", "_").replace("\x00", "_")
    if safe in (".", "..") or not safe:
        safe = "_invalid_"
    sd = state_dir().resolve()
    candidate = (sd / f"{safe}.json").resolve()
    # Containment check: candidate must be a direct child of sd.
    if candidate.parent != sd:
        return sd / "_unsafe_session_id_.json"
    return candidate


def load(session_id: str) -> SessionState:
    path = _path(session_id)
    if not path.exists():
        return SessionState(session_id=session_id)

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        get_logger().warning("state file %s unreadable; resetting (%s)", path, e)
        return SessionState(session_id=session_id)

    return SessionState(
        session_id=raw.get("session_id", session_id),
        current_pid=raw.get("current_pid"),
        queue=raw.get("queue") or [],
    )


def save(state: SessionState) -> None:
    """Persist state atomically. Never raises — voice is never load-bearing."""
    log = get_logger()
    try:
        path = _path(state.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling tmp file then atomic rename so concurrent readers
        # never observe a half-written JSON document.
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(path.parent),
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(asdict(state), tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    except (OSError, TypeError, ValueError) as e:
        log.warning("state save failed for %s: %s", state.session_id, e)


def remove(session_id: str) -> None:
    path = _path(session_id)
    if path.exists():
        path.unlink()


def clean_stale(max_age_seconds: int = 24 * 3600) -> None:
    cutoff = time.time() - max_age_seconds
    try:
        files = list(state_dir().glob("*.json"))
    except OSError:
        return
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            # Tolerate FileNotFoundError, PermissionError, IsADirectoryError, etc.
            # clean_stale is best-effort housekeeping.
            continue
