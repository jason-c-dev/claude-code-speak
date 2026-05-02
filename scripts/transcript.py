"""Read assistant text out of a Claude Code transcript JSONL file."""
from __future__ import annotations
import json
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


def last_assistant_text(transcript_path: Path) -> tuple[str, str] | None:
    """Return (message_id, concatenated_text) for the last assistant message,
    or None if no assistant message exists."""
    last = None
    for entry in _iter_entries(transcript_path):
        if entry.get("type") == "assistant":
            last = entry
    if last is None:
        return None
    msg = last.get("message") or {}
    msg_id = msg.get("id") or ""
    parts = _assistant_text_blocks(last)
    if not parts:
        return (msg_id, "")
    return (msg_id, " ".join(parts))


def current_assistant_text_after(
    transcript_path: Path, message_id: str, offset: int
) -> str:
    """Return the concatenated text of the last assistant message past `offset`.

    If `message_id` doesn't match the last message's id, returns the full text
    starting from offset 0 (treat as a new message)."""
    res = last_assistant_text(transcript_path)
    if res is None:
        return ""
    last_id, text = res
    if last_id != message_id:
        return text
    if offset <= 0:
        return text
    if offset >= len(text):
        return ""
    return text[offset:]
