"""Hook entrypoint. Reads hook event JSON on stdin, dispatches by event."""
from __future__ import annotations
import sys
from pathlib import Path

# Bootstrap: when invoked directly by Claude Code as a hook script, the
# plugin root isn't on sys.path. Inject it so `from scripts import ...` works.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

import json
from scripts import extract, playback, state as state_mod
from scripts.config import load as load_config, plugin_root
from scripts.log import get_logger
from scripts.transcript import last_assistant_text, wait_for_new_message


def _mode_b_narration_instructions() -> str:
    """Instructions Claude needs to narrate via the speak CLI in mode B."""
    cli = plugin_root() / "scripts" / "speak_cli.py"
    return (
        "Claude Voice mode B is active. Before issuing a tool call that may "
        "take more than a moment (web fetches, searches, file reads on "
        "unfamiliar code, MCP calls, etc.), narrate a short cue out loud by "
        "running this Bash command IMMEDIATELY before the tool call:\n\n"
        f"    python3 \"{cli}\" \"<short cue>\"\n\n"
        "The cue should be 2-6 conversational words like \"Looking that up\", "
        "\"Pulling it up\", \"Checking now\", \"On it\". Keep it varied and "
        "natural. Only narrate when the wait would be noticeable; don't "
        "narrate trivial reads or quick edits. The CLI returns immediately; "
        "audio plays in the background and won't slow you down. Final-response "
        "speech at end of turn is automatic — you don't need to call the CLI "
        "for that."
    )


def _handle_stop(payload: dict) -> None:
    cfg = load_config()
    log = get_logger()
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    # Gate 1: only speak for sessions a human is actually driving. Subagents
    # never receive UserPromptSubmit, so this filters out parallel agents.
    s = state_mod.load(session_id)
    if not s.interactive:
        log.info("Stop: session %s not interactive; skipping", session_id)
        return

    # Gate 2: only speak for the most-recently-prompted session. Two Claude
    # Code windows could both be flagged interactive; we want only the one
    # the user just typed into to speak.
    if not state_mod.is_active_session(session_id):
        log.info("Stop: session %s is not the active session; skipping", session_id)
        return

    # The JSONL flush sometimes lags Stop hook fire by a second or more
    # (most reliably reproducible after a Skill tool call). Anchor on
    # "have we seen a NEW msg id since the last one we spoke" rather than
    # blindly reading whatever's in the file — otherwise we re-speak the
    # previous turn's message because the new one isn't written yet.
    res = wait_for_new_message(transcript, s.last_spoken_msg_id)
    if res is None:
        log.info(
            "Stop: no new assistant message after wait (last_spoken=%r); skipping",
            s.last_spoken_msg_id,
        )
        return
    msg_id, full_text = res

    stripped = extract.strip_for_voice(full_text, min_words=cfg.min_words)
    if not stripped:
        log.info("Stop: stripped text empty; full_text=%r", full_text[:80])
        return

    log.info("Stop: speaking %d chars: %r", len(stripped), stripped[:120])

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        log.info("Stop: voicify returned empty; skipping")
        return

    # Chunk into sentence-sized pieces and queue them as text. The player
    # subprocess streams each chunk through Deepgram → ffplay so the first
    # audio bytes start playing within ~one Deepgram-TTFB rather than waiting
    # for the full mp3 to be synthesized and written to disk.
    chunks = extract.split_into_chunks(voiced)
    log.info("Stop: queuing %d chunk(s) for streaming playback", len(chunks))
    for chunk in chunks:
        playback.enqueue(session_id, chunk)

    # Record this msg id as spoken so the next Stop will wait for a NEW one
    # rather than re-speaking this turn.
    try:
        s = state_mod.load(session_id)
        s.last_spoken_msg_id = msg_id
        state_mod.save(s)
    except Exception as e:
        log.warning("Stop: failed to record last_spoken_msg_id=%r: %s", msg_id, e)


def _handle_user_prompt_submit(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    playback.clear_and_kill(session_id)
    # Mark this session as human-interactive so Stop and Notification will
    # speak for it. Subagents never reach this hook.
    s = state_mod.load(session_id)
    s.interactive = True

    # Mark whatever assistant message currently sits at the end of the
    # transcript as "already spoken." On the next Stop, wait_for_new_message
    # will then poll until a NEW msg id appears (this turn's response),
    # rather than re-speaking the previous turn's text. Without this, the
    # field starts as None on first deploy and the very next Stop would
    # treat the previous turn's response as "new."
    transcript = Path(payload.get("transcript_path") or "")
    res = last_assistant_text(transcript)
    if res is not None:
        s.last_spoken_msg_id = res[0]

    state_mod.save(s)
    # Take over as the active speaking session — any previously-active session
    # (e.g. a second Claude Code window) goes silent until the user prompts
    # in it again.
    state_mod.set_active_session(session_id)
    # Also kill any audio that other sessions had queued or were playing,
    # so we don't get cross-talk from a window that just lost active status.
    _kill_other_sessions(session_id)


def _kill_other_sessions(active_session_id: str) -> None:
    """Stop and clear playback for every session other than the newly-active one."""
    log = get_logger()
    try:
        for f in state_mod.state_dir().glob("*.json"):
            sid = f.stem
            if sid == active_session_id:
                continue
            try:
                playback.clear_and_kill(sid)
            except Exception as e:
                log.warning("clear_and_kill failed for %s: %s", sid, e)
    except OSError as e:
        log.warning("could not enumerate state dir: %s", e)


def _handle_session_start(payload: dict) -> None:
    state_mod.clean_stale(max_age_seconds=24 * 3600)


def _handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    state_mod.remove(session_id)
    from scripts.log import voice_home
    tmp = voice_home() / "tmp"
    if tmp.exists():
        for f in tmp.iterdir():
            try:
                f.unlink()
            except Exception:
                pass


def _handle_notification(payload: dict) -> None:
    cfg = load_config()
    if cfg.mode != "C":
        return
    session_id = payload.get("session_id") or "default"
    s = state_mod.load(session_id)
    if not s.interactive:
        get_logger().info("Notification: session %s not interactive; skipping", session_id)
        return
    if not state_mod.is_active_session(session_id):
        get_logger().info("Notification: session %s is not the active session; skipping", session_id)
        return
    msg = payload.get("message") or ""
    if not msg.strip():
        return
    voiced = extract.voicify(
        msg,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        return
    playback.enqueue(session_id, voiced)


_DISPATCH = {
    "Stop": _handle_stop,
    "Notification": _handle_notification,
    "UserPromptSubmit": _handle_user_prompt_submit,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
}


def run(stdin_text: str) -> int:
    log = get_logger()
    cfg = load_config()
    if not cfg.enabled:
        return 0

    # Hard short-circuit for internal SDK invocations (e.g. voicify's Haiku
    # rewrite subagent). The env var is set by extract._voicify_async so the
    # child claude subprocess inherits it. Without this, every Haiku rewrite
    # spawns a sub-Claude session that fires its own UserPromptSubmit + Stop
    # hooks and speaks its own response in parallel.
    import os
    if os.environ.get("CLAUDE_VOICE_INTERNAL") == "1":
        log.info("speak: CLAUDE_VOICE_INTERNAL set; skipping (SDK-internal session)")
        return 0

    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError as e:
        log.warning("speak: invalid hook payload: %s", e)
        return 0

    event = payload.get("hook_event_name") or ""
    handler = _DISPATCH.get(event)
    if handler is None:
        log.info("speak: no handler for event %r; skipping", event)
        return 0

    try:
        handler(payload)
    except Exception as e:
        log.warning("speak: handler %r raised: %s", event, e)
    return 0


def emit_session_start_context_if_applicable(payload: dict) -> bool:
    """If payload is SessionStart and config is mode B, write the narration-
    instruction JSON to stdout. Returns True if anything was written."""
    if payload.get("hook_event_name") != "SessionStart":
        return False
    try:
        cfg = load_config()
    except Exception as e:
        get_logger().warning("speak: SessionStart config load failed: %s", e)
        return False
    if not (cfg.enabled and cfg.mode == "B"):
        return False
    try:
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _mode_b_narration_instructions(),
            }
        }))
        sys.stdout.flush()
        return True
    except Exception as e:
        get_logger().warning("speak: SessionStart additionalContext emit failed: %s", e)
        return False


def main() -> int:
    """Hook entrypoint.

    By default we hand the slow work (TTS, Haiku rewrite, audio enqueue) to a
    detached background worker so the hook returns to Claude Code in <100ms
    rather than blocking the session for several seconds. The worker is the
    same script reinvoked with --worker; it reads the original payload on stdin.

    Special case: on SessionStart in mode B, the parent process emits an
    `additionalContext` JSON to stdout (synchronously, since detached workers
    can't talk back to Claude Code) instructing the assistant how to narrate
    via the speak CLI. The worker still runs for state cleanup.

    A hook can opt out of detachment with --inline (used by tests).
    """
    payload_bytes = sys.stdin.buffer.read()
    payload_text = payload_bytes.decode("utf-8", errors="replace")

    if "--worker" in sys.argv or "--inline" in sys.argv:
        return run(payload_text)

    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        payload = {}

    emit_session_start_context_if_applicable(payload)

    import subprocess
    try:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(payload_bytes)
            proc.stdin.close()
        return 0
    except Exception as e:
        get_logger().warning("speak: worker spawn failed (%s); processing inline", e)
        return run(payload_text)


if __name__ == "__main__":
    sys.exit(main())
