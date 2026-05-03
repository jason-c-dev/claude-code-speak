"""Generate hooks/hooks.json from the current mode."""
from __future__ import annotations
import json
from pathlib import Path

OPERATIONAL_EVENTS = ("UserPromptSubmit", "SessionStart", "SessionEnd")
MODE_EVENTS = {
    "A": ("Stop",),
    "B": ("Stop", "PreToolUse"),
    "C": ("Stop", "PreToolUse", "Notification"),
}

# Hook command: invoke our entrypoint via the plugin-root-relative path.
# ${CLAUDE_PLUGIN_ROOT} is substituted by Claude Code at hook-fire time.
_COMMAND = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py"'


def _hook_block() -> dict:
    return {
        "matcher": "*",
        "hooks": [{"type": "command", "command": _COMMAND}],
    }


def generate(*, mode: str) -> dict:
    if mode not in MODE_EVENTS:
        mode = "A"
    events = list(MODE_EVENTS[mode]) + list(OPERATIONAL_EVENTS)
    out: dict[str, list[dict]] = {}
    for ev in events:
        out[ev] = [_hook_block()]
    return {"hooks": out}


def write(*, mode: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(generate(mode=mode), indent=2) + "\n")
