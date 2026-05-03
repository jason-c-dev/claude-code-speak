import json
from pathlib import Path

import pytest


def test_mode_a_registers_stop_and_operational(plugin_root):
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="A")
    events = {h["matcher"] if isinstance(h, dict) and "matcher" in h else h for h in out["hooks"].keys()}
    assert "Stop" in out["hooks"]
    assert "UserPromptSubmit" in out["hooks"]
    assert "SessionStart" in out["hooks"]
    assert "SessionEnd" in out["hooks"]
    assert "PreToolUse" not in out["hooks"]
    assert "PostToolUse" not in out["hooks"]
    assert "Notification" not in out["hooks"]


def test_mode_b_registers_pre_tool_use(plugin_root):
    """Mode B registers PreToolUse for pre-tool narration cues."""
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="B")
    assert "Stop" in out["hooks"]
    assert "PreToolUse" in out["hooks"]
    assert "PostToolUse" not in out["hooks"]
    assert "Notification" not in out["hooks"]
    # Verify same command shape as other events
    block = out["hooks"]["PreToolUse"][0]
    assert block["matcher"] == "*"
    assert block["hooks"][0]["type"] == "command"
    assert "speak.py" in block["hooks"][0]["command"]


def test_mode_c_registers_pre_tool_use_and_notification(plugin_root):
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="C")
    assert "Notification" in out["hooks"]
    assert "PreToolUse" in out["hooks"]


def test_each_hook_has_command_pointing_to_speak_py(plugin_root):
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="A")
    for event, definitions in out["hooks"].items():
        assert isinstance(definitions, list) and definitions
        for entry in definitions:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                assert "scripts/speak.py" in hook["command"]


def test_write_creates_file(plugin_root, tmp_path):
    from scripts import hooks_gen

    out_path = plugin_root / "hooks" / "hooks.json"
    hooks_gen.write(mode="B", out_path=out_path)
    assert out_path.exists()
    parsed = json.loads(out_path.read_text())
    assert "Stop" in parsed["hooks"]
