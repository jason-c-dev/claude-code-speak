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
    settle_ms: int = 800,
) -> "tuple[str, str] | None":
    """Poll the transcript for the FINAL textful assistant message of the
    current turn (i.e. the one the user most likely wants to hear).

    Two phases:

    1. **Find phase**: poll until `last_assistant_text` returns a message
       whose id differs from `last_spoken_id` AND has non-empty text.
    2. **Settle phase**: keep polling. If a *newer* textful message id
       appears, switch to it and reset the settle clock. When the latest
       textful id has been stable for `settle_ms`, return it.

    Returns `(msg_id, text)` for the settled final message, or `None` if no
    textful new message appears within `max_ms`.

    **Why two phases:**

    - **Find** ensures we only speak text we haven't already spoken (stability
      checks alone are unreliable because Claude Code sometimes writes the
      new message to JSONL *after* the Stop hook fires).
    - **Settle** handles the case where multiple text-bearing messages flush
      in close succession. Without it, the function returns the FIRST
      textful new message — but a multi-step turn ("Tests pass. Committing:"
      → tool calls → "Fix is in...") writes several textful messages, and
      the user wants the *final* summary, not the intermediate progress
      note. The settle window lets later messages overtake earlier ones
      before we commit to speaking.
    - Requiring non-empty text in both phases skips interim
      thinking/tool_use-only messages that have no text content yet.

    The fall-through case is a turn that legitimately ends with no text
    (tools-only). After `max_ms`, return `None` and skip — speaking
    nothing in that case is the right behavior.
    """
    if transcript_path is None:
        return None
    deadline = time.monotonic() + max_ms / 1000.0
    poll_seconds = poll_ms / 1000.0
    settle_seconds = settle_ms / 1000.0

    # Phase 1: find the first textful new message.
    seen: tuple[str, str] | None = None
    while True:
        candidate = last_assistant_text(transcript_path)
        if (
            candidate is not None
            and candidate[0] != last_spoken_id
            and candidate[1]
        ):
            seen = candidate
            break
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)

    # Phase 2: keep polling; if a newer textful message appears, switch to
    # it and reset the settle clock. Return when the latest has been stable
    # for settle_ms.
    last_change = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        current = last_assistant_text(transcript_path)
        if current is None or not current[1]:
            # textless interim — ignore, keep waiting
            continue
        if current[0] != seen[0]:
            seen = current
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= settle_seconds:
            return seen
    return seen


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


