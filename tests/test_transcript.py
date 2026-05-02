import json
from pathlib import Path

import pytest


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_last_assistant_text_returns_concatenated_text_blocks(tmp_path):
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant",
         "message": {
             "id": "msg_1",
             "content": [
                 {"type": "text", "text": "Hello there."},
                 {"type": "tool_use", "name": "Read"},
                 {"type": "text", "text": "Done!"},
             ]
         }},
    ])
    assert last_assistant_text(p) == ("msg_1", "Hello there. Done!")


def test_last_assistant_text_returns_none_when_no_assistant(tmp_path):
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}])
    assert last_assistant_text(p) is None


def test_text_after_offset(tmp_path):
    from scripts.transcript import current_assistant_text_after

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {
             "id": "msg_2",
             "content": [
                 {"type": "text", "text": "Looking at the file."},
                 {"type": "tool_use", "name": "Read"},
                 {"type": "text", "text": "Found the bug here."},
             ]
         }},
    ])
    assert current_assistant_text_after(p, "msg_2", offset=0) == "Looking at the file. Found the bug here."
    full = "Looking at the file. Found the bug here."
    # If we already spoke through char 20, we should get only the rest.
    assert current_assistant_text_after(p, "msg_2", offset=20) == full[20:]


def test_text_after_offset_unknown_id_returns_full(tmp_path):
    from scripts.transcript import current_assistant_text_after

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {
             "id": "msg_3",
             "content": [{"type": "text", "text": "Some text."}],
         }},
    ])
    # Asking about a different id returns the full last-message text from offset 0.
    assert current_assistant_text_after(p, "msg_OTHER", offset=0) == "Some text."
