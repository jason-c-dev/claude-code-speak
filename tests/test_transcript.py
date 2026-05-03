import json
import threading
import time
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


def test_aggregates_text_across_multi_entry_message(tmp_path):
    """Claude Code writes one assistant message as N JSONL entries — one per
    content block. Aggregate text blocks across all entries sharing a msg id."""
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant",
         "message": {"id": "msg_X", "content": [{"type": "thinking", "text": "thinking..."}]}},
        {"type": "assistant",
         "message": {"id": "msg_X", "content": [{"type": "text", "text": "First line."}]}},
        {"type": "assistant",
         "message": {"id": "msg_X", "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        {"type": "assistant",
         "message": {"id": "msg_X", "content": [{"type": "text", "text": "Second line."}]}},
        {"type": "assistant",
         "message": {"id": "msg_X", "content": [{"type": "tool_use", "name": "Bash"}]}},
    ])
    assert last_assistant_text(p) == ("msg_X", "First line. Second line.")


def test_only_uses_text_from_most_recent_message_id(tmp_path):
    """Text from prior messages must not bleed into the current message's text."""
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_OLD", "content": [{"type": "text", "text": "old prose"}]}},
        {"type": "user", "message": {"content": "next"}},
        {"type": "assistant",
         "message": {"id": "msg_NEW", "content": [{"type": "text", "text": "new prose"}]}},
        {"type": "assistant",
         "message": {"id": "msg_NEW", "content": [{"type": "tool_use", "name": "Read"}]}},
    ])
    assert last_assistant_text(p) == ("msg_NEW", "new prose")


def test_wait_for_new_message_returns_immediately_when_already_new(tmp_path):
    """Common case: by the time Stop runs, the new message is already in
    JSONL with an id different from last_spoken_id. Should return after the
    settle window (no newer messages → it's stable)."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_NEW", "content": [{"type": "text", "text": "hello"}]}},
    ])
    started = time.monotonic()
    res = wait_for_new_message(p, last_spoken_id="msg_OLD",
                               max_ms=2000, poll_ms=50, settle_ms=200)
    elapsed = time.monotonic() - started
    assert res == ("msg_NEW", "hello")
    # Should return after roughly settle_ms once stable, not blow through max_ms
    assert 0.18 < elapsed < 0.6, \
        f"should return shortly after settle window, took {elapsed:.2f}s"


def test_wait_for_new_message_settles_on_latest_when_multiple_textful_messages_arrive(tmp_path):
    """Production bug: a multi-step turn writes several textful messages
    in sequence ('Tests pass.' → tools → 'Fix is in...'). The function
    must return the LATEST one, not the first textful one it sees."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_PREV", "content": [{"type": "text", "text": "previous turn"}]}},
        {"type": "assistant",
         "message": {"id": "msg_INTERIM", "content": [{"type": "text", "text": "Tests pass."}]}},
    ])

    def _writer():
        time.sleep(0.20)
        with open(p, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"id": "msg_FINAL",
                            "content": [{"type": "text", "text": "Fix is in. Done."}]},
            }) + "\n")

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    res = wait_for_new_message(p, last_spoken_id="msg_PREV",
                               max_ms=3000, poll_ms=50, settle_ms=300)
    t.join()
    assert res == ("msg_FINAL", "Fix is in. Done."), \
        f"should settle on the LAST textful message, not the interim one; got {res}"


def test_wait_for_new_message_waits_for_late_write(tmp_path):
    """Failure case we hit in production: the new assistant message gets
    written to JSONL AFTER Stop fires. Polling for a new id catches it."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_OLD", "content": [{"type": "text", "text": "previous turn"}]}},
    ])

    def _late_writer():
        time.sleep(0.30)
        with open(p, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"id": "msg_NEW", "content": [{"type": "text", "text": "this turn"}]},
            }) + "\n")

    t = threading.Thread(target=_late_writer, daemon=True)
    started = time.monotonic()
    t.start()
    res = wait_for_new_message(p, last_spoken_id="msg_OLD",
                               max_ms=2000, poll_ms=50)
    elapsed = time.monotonic() - started
    t.join()
    assert res == ("msg_NEW", "this turn")
    assert elapsed >= 0.25, f"should have waited for the late write, took {elapsed:.2f}s"


def test_wait_for_new_message_returns_none_if_nothing_new_within_budget(tmp_path):
    """If the new message never arrives, return None — caller skips speaking
    rather than re-speaking the previous turn."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_SAME", "content": [{"type": "text", "text": "previous"}]}},
    ])
    started = time.monotonic()
    res = wait_for_new_message(p, last_spoken_id="msg_SAME",
                               max_ms=200, poll_ms=30)
    elapsed = time.monotonic() - started
    assert res is None
    assert 0.18 < elapsed < 0.5, f"should respect max_ms budget, took {elapsed:.2f}s"


def test_wait_for_new_message_fresh_session_speaks_first_message(tmp_path):
    """When last_spoken_id is None (fresh session), any assistant message is
    'new' — return it immediately."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_FIRST", "content": [{"type": "text", "text": "hi"}]}},
    ])
    res = wait_for_new_message(p, last_spoken_id=None, max_ms=1000, poll_ms=50)
    assert res == ("msg_FIRST", "hi")


def test_wait_for_new_message_skips_textless_new_message(tmp_path):
    """Bug we hit in production: Stop fires while the latest assistant entry
    is a tool_use-only message (no text yet). Without this guard, the caller
    treats it as "new" and silently skips because stripped text is empty —
    leaving the user with no audio. wait_for_new_message must keep polling
    until a textful message arrives, or return None on timeout."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_OLD", "content": [{"type": "text", "text": "previous"}]}},
        {"type": "assistant",
         "message": {"id": "msg_TOOL_ONLY",
                     "content": [{"type": "tool_use", "name": "Bash"}]}},
    ])

    def _later_writer():
        time.sleep(0.20)
        with open(p, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"id": "msg_NEW",
                            "content": [{"type": "text", "text": "actual response"}]},
            }) + "\n")

    t = threading.Thread(target=_later_writer, daemon=True)
    t.start()
    started = time.monotonic()
    res = wait_for_new_message(p, last_spoken_id="msg_OLD",
                               max_ms=2000, poll_ms=50)
    elapsed = time.monotonic() - started
    t.join()

    assert res == ("msg_NEW", "actual response"), \
        f"should skip the tool-only message and wait for the textful one; got {res}"
    assert elapsed >= 0.18, \
        f"should have waited for the late textful write; only {elapsed:.2f}s"


def test_wait_for_new_message_returns_none_when_only_textless_messages(tmp_path):
    """If the only "new" assistant messages are tool-use-only and never
    a textful one arrives, wait_for_new_message returns None within budget."""
    from scripts.transcript import wait_for_new_message

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {"id": "msg_OLD", "content": [{"type": "text", "text": "previous"}]}},
        {"type": "assistant",
         "message": {"id": "msg_TOOLS",
                     "content": [{"type": "tool_use", "name": "Bash"}]}},
    ])
    started = time.monotonic()
    res = wait_for_new_message(p, last_spoken_id="msg_OLD",
                               max_ms=300, poll_ms=50)
    elapsed = time.monotonic() - started
    assert res is None
    assert 0.28 < elapsed < 0.6, \
        f"should poll until budget then return None; took {elapsed:.2f}s"
