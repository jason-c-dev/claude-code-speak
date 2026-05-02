"""FIFO speech queue per session.

`enqueue(session_id, text)` appends a text chunk to the session's queue and
(if no player is currently running) launches a small Python child that drains
the queue by calling `tts.synthesize_and_play` for each chunk. The child's
PID is recorded in session state so `clear_and_kill` can interrupt it; because
the child is started with `start_new_session=True`, SIGTERMing the process
group also kills any ffplay/afplay it spawned.

All public functions never raise — voice is a UX layer, never load-bearing.
"""
from __future__ import annotations
import os
import signal
import subprocess
import sys
from pathlib import Path

# Bootstrap: when invoked directly as a child process, plugin root isn't on
# sys.path yet. Inject it so `from scripts import ...` works.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from scripts.log import get_logger
from scripts import state as state_mod


def enqueue(session_id: str, text: str) -> None:
    """Append a text chunk to the session's queue; spawn a player if none running."""
    log = get_logger()
    try:
        s = state_mod.load(session_id)
        s.queue.append(text)
        state_mod.save(s)
    except Exception as e:
        log.warning("playback.enqueue failed for %s: %s", session_id, e)
        return

    if s.current_pid is not None:
        log.info("player already running for %s (pid=%d); queued %d-char chunk",
                 session_id, s.current_pid, len(text))
        return

    # Spawn a fresh player process pointed at this session.
    # Invoke the script directly (not -m) so it works regardless of PYTHONPATH;
    # playback.py bootstraps its own sys.path at module-top.
    try:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--player", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warning("playback.enqueue could not spawn player: %s", e)
        return

    try:
        s = state_mod.load(session_id)
        s.current_pid = child.pid
        state_mod.save(s)
        log.info("started player pid=%d for %s", child.pid, session_id)
    except Exception as e:
        log.warning("could not record player pid=%d for %s: %s", child.pid, session_id, e)


def clear_and_kill(session_id: str) -> None:
    """SIGTERM the current player (if any) and clear the queue. Never raises."""
    log = get_logger()
    try:
        s = state_mod.load(session_id)
    except Exception as e:
        log.warning("clear_and_kill load failed for %s: %s", session_id, e)
        return

    if s.current_pid:
        # The player was spawned with start_new_session=True so it's in its own
        # process group. SIGTERM the whole group so afplay (spawned by the
        # player) dies too — otherwise orphan afplays keep playing across turns.
        try:
            pgid = os.getpgid(s.current_pid)
            os.killpg(pgid, signal.SIGTERM)
            log.info("killed player pgid=%d (pid=%d) for %s", pgid, s.current_pid, session_id)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Fall back to single-pid kill if we somehow don't own the group.
            try:
                os.kill(s.current_pid, signal.SIGTERM)
            except Exception:
                pass
        except Exception as e:
            log.warning("failed to kill pid=%d: %s", s.current_pid, e)

    try:
        s.queue = []
        s.current_pid = None
        state_mod.save(s)
    except Exception as e:
        log.warning("clear_and_kill save failed for %s: %s", session_id, e)


def player_loop(session_id: str) -> None:
    """Drain the session queue through one streaming session for gapless audio.

    Runs in a child process. Never raises. The streaming session pulls text
    chunks via a callback, so any chunks that arrive *during* playback (a
    rare race when enqueue runs concurrently with the loop) get picked up
    automatically without restarting ffplay.
    """
    log = get_logger()
    from scripts import tts

    def _on_player_pid(pid: int) -> None:
        log.info("playback subprocess pid=%d for %s", pid, session_id)

    def _get_next_text() -> "str | None":
        try:
            s = state_mod.load(session_id)
        except Exception as e:
            log.warning("player_loop load failed for %s: %s", session_id, e)
            return None
        if not s.queue:
            return None
        text = s.queue.pop(0)
        try:
            state_mod.save(s)
        except Exception:
            pass
        return text

    try:
        # Outer loop catches the rare case where new chunks arrive AFTER the
        # streaming session decided the queue was empty and closed ffplay.
        while True:
            try:
                s = state_mod.load(session_id)
            except Exception as e:
                log.warning("player_loop load failed for %s: %s", session_id, e)
                return
            if not s.queue:
                s.current_pid = None
                try:
                    state_mod.save(s)
                except Exception:
                    pass
                return

            try:
                tts.synthesize_and_play_session(
                    _get_next_text, on_player_pid=_on_player_pid,
                )
            except Exception as e:
                log.warning("synthesize_and_play_session raised for %s: %s",
                            session_id, e)
    except Exception as e:
        log.warning("player_loop crashed for %s: %s", session_id, e)


def _drain_state(session_id: str) -> None:
    try:
        s = state_mod.load(session_id)
        s.queue = []
        s.current_pid = None
        state_mod.save(s)
    except Exception:
        pass


def _main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--player":
        player_loop(argv[2])
        return 0
    print("playback.py is invoked internally; nothing to do here.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
