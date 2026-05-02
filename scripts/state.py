"""Per-session state file at ~/.claude/voice/state/<session_id>.json."""
from __future__ import annotations
import json
import os
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
    spoken_offsets: dict[str, int] = field(default_factory=dict)
    current_pid: int | None = None
    queue: list[str] = field(default_factory=list)  # paths of pending audio files


def _path(session_id: str) -> Path:
    return state_dir() / f"{session_id}.json"


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
        spoken_offsets=raw.get("spoken_offsets") or {},
        current_pid=raw.get("current_pid"),
        queue=raw.get("queue") or [],
    )


def save(state: SessionState) -> None:
    path = _path(state.session_id)
    path.write_text(json.dumps(asdict(state)))


def remove(session_id: str) -> None:
    path = _path(session_id)
    if path.exists():
        path.unlink()


def clean_stale(max_age_seconds: int = 24 * 3600) -> None:
    cutoff = time.time() - max_age_seconds
    for f in state_dir().glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except FileNotFoundError:
            pass
