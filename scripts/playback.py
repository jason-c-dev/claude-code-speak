"""FIFO audio queue per session.

`enqueue(session_id, path)` appends to the session's queue and (if no
player is currently running) launches a small Python child that drains
the queue by calling `afplay` for each file. The child's PID is recorded
in the session state so `clear_and_kill` can interrupt it.

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

AFPLAY_TIMEOUT_SECONDS = 60


def enqueue(session_id: str, audio_path: Path) -> None:
    """Append audio_path to session's queue; spawn a player if none running."""
    log = get_logger()
    try:
        s = state_mod.load(session_id)
        s.queue.append(str(audio_path))
        state_mod.save(s)
    except Exception as e:
        log.warning("playback.enqueue failed for %s: %s", session_id, e)
        return

    if s.current_pid is not None:
        log.info("player already running for %s (pid=%d); queued %s",
                 session_id, s.current_pid, audio_path.name)
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
    """Drain the session queue. Runs in a child process. Never raises."""
    log = get_logger()
    try:
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

            next_path = s.queue.pop(0)
            try:
                state_mod.save(s)
            except Exception:
                pass

            try:
                subprocess.run(
                    ["afplay", next_path],
                    check=False,
                    capture_output=True,
                    timeout=AFPLAY_TIMEOUT_SECONDS,
                )
            except FileNotFoundError:
                log.warning("afplay missing; aborting player_loop for %s", session_id)
                _drain_state(session_id)
                return
            except subprocess.TimeoutExpired:
                log.warning("afplay timed out on %s", next_path)
            except Exception as e:
                log.warning("afplay error on %s: %s", next_path, e)
            finally:
                try:
                    Path(next_path).unlink(missing_ok=True)
                except Exception:
                    pass
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
