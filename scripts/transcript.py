"""Read assistant text out of a Claude Code transcript JSONL file."""
from __future__ import annotations
import json
import time
from pathlib import Path


def _iter_entries(path: Path):
    """Yield JSON entries from a JSONL file. Tolerates missing file and bad lines."""
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _assistant_text_blocks(entry: dict) -> list[str]:
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text") or ""
            if t:
                out.append(t)
    return out


def wait_for_settle(
    path: Path,
    *,
    min_wait_ms: int = 300,
    settle_ms: int = 150,
    max_ms: int = 1500,
    poll_ms: int = 50,
) -> None:
    """Wait `min_wait_ms` unconditionally, then block until the transcript file
    size has been stable for `settle_ms` (or `max_ms` total elapses).

    Stop sometimes fires before the model's final assistant text block has
    been flushed to the JSONL — most reliably reproducible after a Skill tool
    call, where the model resumes generating immediately and the new text
    block is still in flight when the hook runs. Reading too early returns
    the *previous* assistant message, which is then spoken in place of what
    the user actually wanted to hear.

    A pure stability check is not enough: at hook-fire time the file appears
    quiescent because no writes have *yet* happened. The unconditional grace
    window covers the typical flush lag; the settle phase then catches longer
    bursts that exceed `min_wait_ms`.

    The Stop worker is detached, so this delay does not block Claude Code's
    hook return path. Bounded so a never-settling transcript still progresses.
    """
    if path is None:
        return
    try:
        if not path.exists():
            return
    except OSError:
        return

    time.sleep(min_wait_ms / 1000.0)

    try:
        last_size = path.stat().st_size
    except OSError:
        return
    deadline = time.monotonic() + max(max_ms - min_wait_ms, 0) / 1000.0
    last_change = time.monotonic()
    settle_seconds = settle_ms / 1000.0
    poll_seconds = poll_ms / 1000.0
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size != last_size:
            last_size = size
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= settle_seconds:
            return


def last_assistant_text(transcript_path: Path) -> tuple[str, str] | None:
    """Return (message_id, concatenated_text) for the last assistant message,
    or None if no assistant message exists.

    Claude Code writes a single assistant message as multiple JSONL entries —
    one per content block (text, thinking, tool_use). Entries that share a
    message id are the same logical message, so we aggregate text blocks
    across ALL entries with the most-recent message id."""
    last_id: str | None = None
    parts_by_id: dict[str, list[str]] = {}
    for entry in _iter_entries(transcript_path):
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message") or {}
        mid = msg.get("id") or ""
        if not mid:
            # Fallback for entries missing an id: keep them in a sentinel bucket.
            mid = "__no_id__"
        last_id = mid
        parts_by_id.setdefault(mid, []).extend(_assistant_text_blocks(entry))
    if last_id is None:
        return None
    parts = parts_by_id.get(last_id, [])
    if not parts:
        return (last_id, "")
    return (last_id, " ".join(parts))


