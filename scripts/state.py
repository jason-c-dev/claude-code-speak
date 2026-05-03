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


def _active_pointer_path() -> Path:
    return voice_home() / "active_session"


def set_active_session(session_id: str) -> None:
    """Mark `session_id` as the single session whose Stop/Notification hooks
    should produce speech. Prevents two simultaneously-open Claude Code windows
    from both speaking — the most-recently-prompted one wins."""
    try:
        _active_pointer_path().write_text(session_id)
    except OSError as e:
        get_logger().warning("set_active_session failed: %s", e)


def get_active_session() -> str | None:
    try:
        return _active_pointer_path().read_text().strip() or None
    except OSError:
        return None


def is_active_session(session_id: str) -> bool:
    """A session is active if it's the most-recently-prompted one. If no
    pointer exists yet (first prompt of an install), default to True so
    the very first turn still speaks."""
    active = get_active_session()
    return active is None or active == session_id


@dataclass
class SessionState:
    session_id: str
    current_pid: int | None = None
    queue: list[str] = field(default_factory=list)  # paths of pending audio files
    # True only after UserPromptSubmit fired for this session — the signal that
    # a real human is driving this session interactively. Subagents and other
    # programmatic Claude Code instances never get UserPromptSubmit, so this
    # gate prevents speech from leaking out of every parallel agent on the box.
    interactive: bool = False
    # Message id of the most-recent assistant turn we already spoke. Used by
    # the Stop handler to wait for a *new* assistant message before speaking,
    # since the JSONL flush sometimes lags Stop hook fire by more than the
    # transcript settle window.
    last_spoken_msg_id: str | None = None
    # Phrase enqueued for the most-recent PreToolUse cue in this turn. Used to
    # dedup consecutive identical cues — three Edit calls in a row should
    # speak "making an edit" once, not three times. Reset at UserPromptSubmit
    # so each new turn always hears its first cue.
    last_pre_tool_phrase: str | None = None


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
        interactive=bool(raw.get("interactive", False)),
        last_spoken_msg_id=raw.get("last_spoken_msg_id") or None,
        last_pre_tool_phrase=raw.get("last_pre_tool_phrase") or None,
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
