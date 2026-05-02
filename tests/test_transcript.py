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


def test_wait_for_settle_returns_after_min_wait_when_stable(tmp_path):
    """If the file isn't growing, wait_for_settle should return shortly after
    the unconditional min_wait, not block until max_ms."""
    from scripts.transcript import wait_for_settle

    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"assistant","message":{"id":"X","content":[]}}\n')

    started = time.monotonic()
    wait_for_settle(p, min_wait_ms=100, settle_ms=50, max_ms=5000, poll_ms=20)
    elapsed = time.monotonic() - started
    assert 0.09 < elapsed < 0.5, \
        f"stable file should return shortly after min_wait, took {elapsed:.2f}s"


def test_wait_for_settle_catches_late_write_within_min_wait(tmp_path):
    """The whole point: a write that lands AFTER hook fire but DURING min_wait
    must be picked up by the time the function returns."""
    from scripts.transcript import wait_for_settle

    p = tmp_path / "t.jsonl"
    p.write_text("seed\n")

    def _writer():
        time.sleep(0.20)  # simulate the JSONL flush lag
        with open(p, "a") as f:
            f.write("late text\n")

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    started = time.monotonic()
    wait_for_settle(p, min_wait_ms=300, settle_ms=100, max_ms=2000, poll_ms=20)
    elapsed = time.monotonic() - started
    t.join()

    assert "late text" in p.read_text(), \
        "the late write must have happened by the time wait_for_settle returned"
    assert elapsed >= 0.30, \
        f"should not return before min_wait completes; took {elapsed:.2f}s"
    assert elapsed < 1.0, \
        f"should not block past settle window; took {elapsed:.2f}s"


def test_wait_for_settle_blocks_until_writes_stop(tmp_path):
    """While the file keeps growing past min_wait, wait_for_settle must NOT
    return until it stops growing."""
    from scripts.transcript import wait_for_settle

    p = tmp_path / "t.jsonl"
    p.write_text("seed\n")

    # Writes keep streaming for ~500ms, well past min_wait_ms=200.
    def _writer():
        for i in range(10):
            time.sleep(0.05)
            with open(p, "a") as f:
                f.write(f"chunk {i}\n")

    t = threading.Thread(target=_writer, daemon=True)
    started = time.monotonic()
    t.start()
    wait_for_settle(p, min_wait_ms=200, settle_ms=150, max_ms=3000, poll_ms=20)
    elapsed = time.monotonic() - started
    t.join()

    assert elapsed >= 0.50, \
        f"should wait through ongoing writes (~500ms total); only {elapsed:.2f}s"
    assert elapsed < 1.5, \
        f"should not block past settle window; took {elapsed:.2f}s"


def test_wait_for_settle_caps_at_max_ms(tmp_path):
    """If the file never settles, wait_for_settle still returns by max_ms."""
    from scripts.transcript import wait_for_settle

    p = tmp_path / "t.jsonl"
    p.write_text("seed\n")
    stop_writing = threading.Event()

    def _writer():
        i = 0
        while not stop_writing.is_set():
            with open(p, "a") as f:
                f.write(f"x{i}\n")
            i += 1
            time.sleep(0.02)

    t = threading.Thread(target=_writer, daemon=True)
    t.start()
    started = time.monotonic()
    wait_for_settle(p, min_wait_ms=100, settle_ms=300, max_ms=500, poll_ms=20)
    elapsed = time.monotonic() - started
    stop_writing.set()
    t.join()

    assert elapsed < 0.8, \
        f"should cap at max_ms (~500ms); took {elapsed:.2f}s"


def test_wait_for_settle_handles_missing_file(tmp_path):
    """Must not raise on a non-existent path."""
    from scripts.transcript import wait_for_settle
    wait_for_settle(tmp_path / "does-not-exist.jsonl",
                    min_wait_ms=10, settle_ms=20, max_ms=100, poll_ms=10)
