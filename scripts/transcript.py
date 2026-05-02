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


def wait_for_new_message(
    transcript_path: Path,
    last_spoken_id: str | None,
    *,
    max_ms: int = 5000,
    poll_ms: int = 100,
) -> "tuple[str, str] | None":
    """Poll the transcript until the latest assistant message id differs from
    `last_spoken_id`, or until `max_ms` elapses.

    Returns (msg_id, text) for the new message, or None if no new message
    appears within the budget. None for `last_spoken_id` matches "speak the
    first message we see" — useful on a fresh session.

    File-size-stability is not a reliable signal: Claude Code sometimes
    writes the new assistant message to JSONL *after* the Stop hook has
    run, so a stability check at hook-fire time sees a quiescent file
    because nothing has happened yet. Anchoring on "have we seen a NEW
    msg id" is the correct semantic — we only speak text we haven't
    already spoken, and we wait for it to actually appear.
    """
    if transcript_path is None:
        return None
    deadline = time.monotonic() + max_ms / 1000.0
    poll_seconds = poll_ms / 1000.0
    last_seen: tuple[str, str] | None = None
    while True:
        last_seen = last_assistant_text(transcript_path)
        if last_seen is not None and last_seen[0] != last_spoken_id:
            return last_seen
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)


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


