"""Speak CLI — invoke from Bash to make Claude narrate text mid-turn (mode B).

Usage:
    python3 speak_cli.py "Looking that up..."

Resolves the current voice session from the most-recently-modified state file
and pipes the text through voicify -> synthesize -> playback.enqueue. Silent
no-op outside mode B so accidental invocations in mode A stay quiet.

Detaches a background worker by default so the calling Bash tool returns
in <100ms instead of blocking for several seconds while audio is synthesized.

Never raises — voice is a UX layer, never load-bearing.
"""
from __future__ import annotations
import sys
from pathlib import Path

# Bootstrap: when invoked directly via Bash, plugin root isn't on sys.path.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from scripts import extract, playback, tts
from scripts.config import load as load_config
from scripts.log import get_logger, voice_home


def _latest_session_id() -> str:
    """Most-recently-modified state file's session_id, or 'default'."""
    state_dir = voice_home() / "state"
    try:
        files = sorted(
            state_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return "default"
    return files[0].stem if files else "default"


def _run(text: str) -> int:
    log = get_logger()
    cfg = load_config()

    if not cfg.enabled:
        log.info("speak_cli: disabled in config; skipping")
        return 0

    if cfg.mode != "B":
        log.info("speak_cli: mode is %s, skipping (only mode B narrates)", cfg.mode)
        return 0

    if not text.strip():
        log.info("speak_cli: empty text")
        return 0

    stripped = extract.strip_for_voice(text, min_words=cfg.min_words)
    if not stripped:
        log.info("speak_cli: stripped text empty; text=%r", text[:80])
        return 0

    log.info("speak_cli: speaking %d chars: %r", len(stripped), stripped[:120])

    # CLI cues are short and intentional (the assistant chose the exact words);
    # always bypass Haiku here so the cue is spoken verbatim and stays low-latency.
    # cfg.rewrite governs the Stop hook's final-response polish only.
    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=False,
    )
    if not voiced:
        log.info("speak_cli: voicify returned empty; skipping")
        return 0

    audio = tts.synthesize(voiced)
    if audio is None:
        log.warning("speak_cli: TTS produced no audio; skipping")
        return 0

    playback.enqueue(_latest_session_id(), audio)
    return 0


def main(argv: list[str]) -> int:
    """CLI entrypoint.

    By default, detaches a background worker so the caller returns fast.
    `--inline` runs synchronously (used by tests).
    """
    inline = "--inline" in argv
    worker = "--worker" in argv
    text_args = [a for a in argv[1:] if a not in ("--inline", "--worker")]
    text = " ".join(text_args)

    if worker or inline:
        return _run(text)

    import subprocess
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", *text_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0
    except Exception as e:
        get_logger().warning("speak_cli: worker spawn failed (%s); inline fallback", e)
        return _run(text)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:
        try:
            get_logger().warning("speak_cli: top-level exception: %s", e)
        except Exception:
            pass
        sys.exit(0)
