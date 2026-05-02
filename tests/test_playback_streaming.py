"""Tests for the text-based player_loop that drives synthesize_and_play."""
from __future__ import annotations


def test_player_loop_pops_text_and_calls_synthesize_and_play(voice_home, monkeypatch):
    """Items in the queue are now text strings, not file paths.

    player_loop pops each in FIFO order and hands it to tts.synthesize_and_play."""
    from scripts import playback, state, tts

    s = state.load("sess-stream-1")
    s.queue = ["first sentence", "second sentence"]
    state.save(s)

    played: list[str] = []
    def fake_play(text, *, on_player_pid):
        played.append(text)
        on_player_pid(99100 + len(played))
        return True
    monkeypatch.setattr(tts, "synthesize_and_play", fake_play)

    playback.player_loop("sess-stream-1")

    assert played == ["first sentence", "second sentence"]
    s2 = state.load("sess-stream-1")
    assert s2.queue == []
    assert s2.current_pid is None


def test_player_loop_continues_after_synthesis_failure(voice_home, monkeypatch):
    """If synthesize_and_play returns False for one item, the loop keeps draining."""
    from scripts import playback, state, tts

    s = state.load("sess-stream-2")
    s.queue = ["bad chunk", "good chunk"]
    state.save(s)

    calls: list[str] = []
    def fake_play(text, *, on_player_pid):
        calls.append(text)
        return text == "good chunk"
    monkeypatch.setattr(tts, "synthesize_and_play", fake_play)

    playback.player_loop("sess-stream-2")

    assert calls == ["bad chunk", "good chunk"]
    s2 = state.load("sess-stream-2")
    assert s2.queue == []


def test_enqueue_text_appends_and_starts_player(voice_home, monkeypatch):
    """enqueue takes a text chunk and spawns a player_loop child if none running."""
    from scripts import playback, state

    started: list[list[str]] = []
    class FakeProc:
        pid = 70001
    def fake_popen(args, **kw):
        started.append(args)
        return FakeProc()
    monkeypatch.setattr(playback.subprocess, "Popen", fake_popen)

    playback.enqueue("sess-stream-3", "hello world")
    playback.enqueue("sess-stream-3", "second chunk")

    s = state.load("sess-stream-3")
    # First chunk may have been "popped" conceptually, but since we mocked Popen
    # the player_loop never actually ran. Both should still be in the queue.
    assert "hello world" in s.queue
    assert "second chunk" in s.queue
    # Only one player spawned for the burst (second enqueue reuses).
    assert len(started) == 1
    assert s.current_pid == 70001
