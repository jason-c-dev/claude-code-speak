# Pre-Tool Hook Cues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mode B's pre-tool narration deterministic by adding a `PreToolUse` hook that maps tool names to short phrases and enqueues them through the existing playback pipeline.

**Architecture:** New module `scripts/tool_phrases.py` holds the default tool→phrase map plus a `lookup()` helper that merges user overrides from `config.json`. `speak.py` gains a `_handle_pre_tool_use` handler that gates on mode/interactive/active, looks up the phrase, and enqueues it. `hooks_gen.py` registers `PreToolUse` for modes B and C (mode A stays silent for tools). The existing model-driven `speak_cli` path stays for non-tool interjections; the mode B SessionStart instruction is updated to tell the model not to narrate tool calls.

**Tech Stack:** Python 3.10+, pytest, dataclasses, MappingProxyType.

**Spec:** `docs/superpowers/specs/2026-05-03-pre-tool-hook-cues-design.md`

---

## File Structure

- **Create** `scripts/tool_phrases.py` — `DEFAULTS` map, `clean_mcp_name()`, `lookup()`. Pure-function module, no I/O, no state.
- **Create** `tests/test_tool_phrases.py` — covers all three public surfaces.
- **Modify** `scripts/config.py` — add `tool_phrases: Mapping[str, str]` field, `_coerce_tool_phrases` helper, wire into `load()`.
- **Modify** `tests/test_config.py` — extend with `tool_phrases` parsing cases.
- **Modify** `scripts/speak.py` — import `tool_phrases`, add `_handle_pre_tool_use`, register in `_DISPATCH`, rewrite `_mode_b_narration_instructions` to forbid tool narration.
- **Create** `tests/test_speak_pre_tool_use.py` — gating + payload + enqueue tests.
- **Modify** `scripts/hooks_gen.py` — extend `MODE_EVENTS` to register `PreToolUse` for B and C.
- **Modify** `tests/test_hooks_gen.py` — assert PreToolUse registration matrix.
- **Regenerate** `hooks/hooks.json` — run the regen one-liner so the local install picks up the new event registration.

---

## Task 1: tool_phrases module

**Files:**
- Create: `scripts/tool_phrases.py`
- Test: `tests/test_tool_phrases.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_phrases.py`:

```python
"""Tests for scripts/tool_phrases.py — tool→phrase map for PreToolUse cues."""


def test_lookup_returns_default_for_known_tool():
    from scripts.tool_phrases import lookup
    assert lookup("Bash") == "running this"
    assert lookup("Read") == "reading"


def test_lookup_falls_back_to_calling_for_unknown_tool():
    from scripts.tool_phrases import lookup
    assert lookup("FrobnicateXYZ") == "calling FrobnicateXYZ"


def test_lookup_overrides_take_precedence_over_defaults():
    from scripts.tool_phrases import lookup
    assert lookup("Bash", overrides={"Bash": "executing"}) == "executing"


def test_lookup_overrides_can_add_new_tools():
    from scripts.tool_phrases import lookup
    assert lookup("CustomTool", overrides={"CustomTool": "doing the thing"}) \
        == "doing the thing"


def test_lookup_ignores_non_string_override_entries():
    from scripts.tool_phrases import lookup
    bad = {"Bash": 123, "Read": None, "Write": "scribbling"}
    # Bash and Read overrides are dropped; defaults used. Write override applied.
    assert lookup("Bash", overrides=bad) == "running this"
    assert lookup("Read", overrides=bad) == "reading"
    assert lookup("Write", overrides=bad) == "scribbling"


def test_lookup_handles_none_overrides():
    from scripts.tool_phrases import lookup
    assert lookup("Bash", overrides=None) == "running this"


def test_clean_mcp_name_strips_prefix_and_separators():
    from scripts.tool_phrases import clean_mcp_name
    assert clean_mcp_name("mcp__claude_ai_Gmail__search_threads") \
        == "claude ai Gmail search threads"


def test_clean_mcp_name_passes_through_non_mcp():
    from scripts.tool_phrases import clean_mcp_name
    assert clean_mcp_name("Bash") == "Bash"


def test_lookup_uses_clean_mcp_name_in_fallback():
    from scripts.tool_phrases import lookup
    assert lookup("mcp__claude_ai_Gmail__search_threads") \
        == "calling claude ai Gmail search threads"


def test_defaults_are_immutable():
    """DEFAULTS must be a read-only mapping so callers can't accidentally
    mutate the global default phrase set."""
    from scripts.tool_phrases import DEFAULTS
    import pytest
    with pytest.raises(TypeError):
        DEFAULTS["Bash"] = "stomping"  # type: ignore[index]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tool_phrases.py -v`
Expected: All FAIL — `ModuleNotFoundError: No module named 'scripts.tool_phrases'`

- [ ] **Step 3: Implement the module**

Create `scripts/tool_phrases.py`:

```python
"""Tool name → narration phrase map for the PreToolUse hook (modes B and C).

The phrase a user hears before a tool fires is a pure function of the tool
name and the user's optional override map. No model interaction, no
transcript reading — just a dict lookup.
"""
from __future__ import annotations
from types import MappingProxyType
from typing import Mapping


DEFAULTS: Mapping[str, str] = MappingProxyType({
    "Bash": "running this",
    "Read": "reading",
    "Edit": "editing",
    "Write": "writing",
    "Grep": "searching",
    "Glob": "finding files",
    "WebFetch": "fetching",
    "WebSearch": "looking that up",
    "TodoWrite": "updating tasks",
    "Task": "starting an agent",
    "Agent": "starting an agent",
    "Skill": "loading a skill",
    "ToolSearch": "loading tools",
    "ScheduleWakeup": "scheduling",
    "ExitPlanMode": "wrapping up the plan",
    "BashOutput": "checking output",
    "KillShell": "stopping that",
    "NotebookEdit": "editing the notebook",
})


def clean_mcp_name(tool_name: str) -> str:
    """`mcp__claude_ai_Gmail__search_threads` → `'claude ai Gmail search threads'`.

    MCP tool names are long and read aloud awfully (`m-c-p-underscore-underscore-...`).
    Strip the `mcp__` prefix and replace remaining separators with spaces so the
    fallback phrase ('calling X') sounds natural.
    """
    if not tool_name.startswith("mcp__"):
        return tool_name
    body = tool_name[len("mcp__"):]
    # `__` first so "claude_ai__Gmail" becomes "claude_ai Gmail" (preserve word grouping),
    # then single `_` for the rest.
    return body.replace("__", " ").replace("_", " ")


def lookup(tool_name: str, overrides: Mapping[str, str] | None = None) -> str:
    """Return the cue phrase for `tool_name`.

    Resolution order:
      1. user override (if `overrides` provides a string entry for `tool_name`)
      2. built-in default (DEFAULTS)
      3. fallback: `'calling {clean_mcp_name(tool_name)}'`

    Non-string override entries are silently ignored so a typo'd config
    can't crash the hook (voice is a UX layer, never load-bearing).
    """
    merged = dict(DEFAULTS)
    if overrides:
        for k, v in overrides.items():
            if isinstance(k, str) and isinstance(v, str):
                merged[k] = v
    if tool_name in merged:
        return merged[tool_name]
    return f"calling {clean_mcp_name(tool_name)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tool_phrases.py -v`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/tool_phrases.py tests/test_tool_phrases.py
git commit -m "feat(tool_phrases): add tool→phrase lookup module for PreToolUse cues"
```

---

## Task 2: Wire `tool_phrases` into Config

**Files:**
- Modify: `scripts/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Read the current Config class**

Run: `head -50 scripts/config.py`
Locate the `@dataclass class Config` and the `load()` function. Note the existing `_coerce_voice_map` helper — we'll mirror its style.

- [ ] **Step 2: Write failing tests for `tool_phrases` parsing**

Add to `tests/test_config.py` (don't replace — append):

```python
def test_tool_phrases_defaults_to_empty(tmp_path, monkeypatch):
    """When config.json omits tool_phrases, cfg.tool_phrases is an empty mapping."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    (tmp_path / "config.json").write_text('{"enabled": true, "mode": "B"}')
    from scripts.config import load
    cfg = load()
    assert dict(cfg.tool_phrases) == {}


def test_tool_phrases_parses_valid_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    (tmp_path / "config.json").write_text(
        '{"enabled": true, "mode": "B", "tool_phrases": {"Bash": "executing"}}'
    )
    from scripts.config import load
    cfg = load()
    assert cfg.tool_phrases["Bash"] == "executing"


def test_tool_phrases_ignores_non_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    (tmp_path / "config.json").write_text(
        '{"enabled": true, "mode": "B", "tool_phrases": "not a dict"}'
    )
    from scripts.config import load
    cfg = load()
    assert dict(cfg.tool_phrases) == {}


def test_tool_phrases_drops_non_string_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    (tmp_path / "config.json").write_text(
        '{"enabled": true, "mode": "B", '
        '"tool_phrases": {"Bash": "executing", "Read": 123, "Write": null}}'
    )
    from scripts.config import load
    cfg = load()
    assert cfg.tool_phrases["Bash"] == "executing"
    assert "Read" not in cfg.tool_phrases
    assert "Write" not in cfg.tool_phrases
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py -v -k tool_phrases`
Expected: All four FAIL — `AttributeError: 'Config' object has no attribute 'tool_phrases'`.

- [ ] **Step 4: Add the field to Config**

In `scripts/config.py`, find the `@dataclass(frozen=True) class Config:` block and add a new field after `say_voice_map`:

```python
    tool_phrases: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
```

- [ ] **Step 5: Add the coercion helper**

In `scripts/config.py`, add this helper next to `_coerce_voice_map`:

```python
def _coerce_tool_phrases(value, log) -> Mapping[str, str]:
    """Accept dict[str, str] only; ignore garbage. Empty mapping by default."""
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, dict):
        log.warning("config.tool_phrases: expected object, got %r; ignoring",
                    type(value).__name__)
        return MappingProxyType({})
    out: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
        else:
            log.warning("config.tool_phrases: ignoring non-string entry %r=%r", k, v)
    return MappingProxyType(out)
```

- [ ] **Step 6: Wire it into `load()`**

In `scripts/config.py`, find the `return Config(...)` block inside `load()` and add this argument (alphabetical-ish placement next to `say_voice_map=...`):

```python
            tool_phrases=_coerce_tool_phrases(raw.get("tool_phrases"), log),
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: All config tests PASS, including the four new `tool_phrases` cases.

- [ ] **Step 8: Commit**

```bash
git add scripts/config.py tests/test_config.py
git commit -m "feat(config): add tool_phrases override map"
```

---

## Task 3: PreToolUse hook handler

**Files:**
- Modify: `scripts/speak.py`
- Create: `tests/test_speak_pre_tool_use.py`

- [ ] **Step 1: Inspect existing handlers and dispatch**

Run: `grep -n "_handle\|_DISPATCH" scripts/speak.py`
Note where `_DISPATCH` is defined and how existing handlers (e.g. `_handle_notification`) are structured. The new handler will mirror that style.

- [ ] **Step 2: Write failing tests**

Create `tests/test_speak_pre_tool_use.py`:

```python
"""Tests for the PreToolUse hook handler in scripts/speak.py."""
import json


def _mark_interactive(sid: str):
    from scripts import state as state_mod
    s = state_mod.load(sid)
    s.interactive = True
    state_mod.save(s)


def test_pre_tool_use_speaks_known_tool_in_mode_b(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "running this")]


def test_pre_tool_use_uses_fallback_for_unknown_tool(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S",
               "tool_name": "NewMysteryTool"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "calling NewMysteryTool")]


def test_pre_tool_use_respects_user_overrides(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({
        "enabled": True, "mode": "B",
        "tool_phrases": {"Bash": "executing", "FrobTool": "frobbing"},
    })
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload_bash = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    payload_frob = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "FrobTool"}
    speak.run(json.dumps(payload_bash))
    speak.run(json.dumps(payload_frob))
    assert enqueued == [("S", "executing"), ("S", "frobbing")]


def test_pre_tool_use_skipped_in_mode_a(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "A"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("must not enqueue in mode A")))
    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Bash"}
    speak.run(json.dumps(payload))


def test_pre_tool_use_skipped_when_session_not_interactive(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Subagent sessions never get UserPromptSubmit, so they remain
    non-interactive. Their tool calls must not produce audio."""
    write_config({"enabled": True, "mode": "B"})
    # Note: no _mark_interactive — subagent default state.
    from scripts import speak, playback
    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("must not enqueue for non-interactive")))
    payload = {"hook_event_name": "PreToolUse",
               "session_id": "subagent-X", "tool_name": "Bash"}
    speak.run(json.dumps(payload))


def test_pre_tool_use_skipped_when_session_not_active(
    voice_home, plugin_root, write_config, monkeypatch
):
    """Two interactive sessions can exist; only the most-recently-prompted one
    speaks. The other's PreToolUse must be silent."""
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("OTHER")
    _mark_interactive("ACTIVE")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("ACTIVE")  # OTHER is not active

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("must not enqueue for non-active")))
    payload = {"hook_event_name": "PreToolUse",
               "session_id": "OTHER", "tool_name": "Bash"}
    speak.run(json.dumps(payload))


def test_pre_tool_use_no_op_on_missing_tool_name(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "B"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    monkeypatch.setattr(playback, "enqueue",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("must not enqueue without tool_name")))
    payload = {"hook_event_name": "PreToolUse", "session_id": "S"}
    speak.run(json.dumps(payload))


def test_pre_tool_use_speaks_in_mode_c(
    voice_home, plugin_root, write_config, monkeypatch
):
    write_config({"enabled": True, "mode": "C"})
    _mark_interactive("S")
    from scripts import speak, playback
    from scripts import state as state_mod
    state_mod.set_active_session("S")

    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, t: enqueued.append((s, t)))

    payload = {"hook_event_name": "PreToolUse", "session_id": "S", "tool_name": "Read"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", "reading")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_speak_pre_tool_use.py -v`
Expected: All FAIL — speak.run dispatches `"PreToolUse"` to no handler, so the log says "no handler for event 'PreToolUse'; skipping" and `enqueued` stays empty (or for the negative tests, they accidentally pass — verify the positive ones fail clearly).

- [ ] **Step 4: Add the import**

In `scripts/speak.py`, find the existing imports and add:

```python
from scripts import tool_phrases
```

(Place it next to the other `from scripts import ...` imports near the top.)

- [ ] **Step 5: Add the handler**

In `scripts/speak.py`, add this function next to `_handle_notification`:

```python
def _handle_pre_tool_use(payload: dict) -> None:
    cfg = load_config()
    log = get_logger()

    # Mode A stays silent for tool work — that's the opt-out for users who
    # find the cues chatty.
    if cfg.mode not in ("B", "C"):
        return

    session_id = payload.get("session_id") or "default"

    s = state_mod.load(session_id)
    if not s.interactive:
        log.info("PreToolUse: session %s not interactive; skipping", session_id)
        return
    if not state_mod.is_active_session(session_id):
        log.info("PreToolUse: session %s is not the active session; skipping",
                 session_id)
        return

    tool_name = payload.get("tool_name") or ""
    if not tool_name:
        log.info("PreToolUse: payload missing tool_name; skipping")
        return

    phrase = tool_phrases.lookup(tool_name, cfg.tool_phrases)
    log.info("PreToolUse: tool=%s phrase=%r", tool_name, phrase)
    playback.enqueue(session_id, phrase)
```

- [ ] **Step 6: Register in `_DISPATCH`**

In `scripts/speak.py`, find `_DISPATCH = {` and add the new event:

```python
_DISPATCH = {
    "Stop": _handle_stop,
    "Notification": _handle_notification,
    "PreToolUse": _handle_pre_tool_use,
    "UserPromptSubmit": _handle_user_prompt_submit,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_speak_pre_tool_use.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 8: Run the full test suite to catch regressions**

Run: `python3 -m pytest --ignore=tests/test_playback.py -q`
Expected: All PASS (the `test_playback.py::test_clear_and_kill` failure is pre-existing and unrelated).

- [ ] **Step 9: Commit**

```bash
git add scripts/speak.py tests/test_speak_pre_tool_use.py
git commit -m "feat(speak): add PreToolUse handler for hook-driven tool cues"
```

---

## Task 4: Register PreToolUse in MODE_EVENTS

**Files:**
- Modify: `scripts/hooks_gen.py`
- Modify: `tests/test_hooks_gen.py`
- Regenerate: `hooks/hooks.json`

- [ ] **Step 1: Read existing tests to mirror style**

Run: `cat tests/test_hooks_gen.py`
Note the existing assertions and pattern (likely calling `generate(mode=...)` and checking the returned dict).

- [ ] **Step 2: Write failing tests**

Append to `tests/test_hooks_gen.py`:

```python
def test_mode_a_does_not_register_pre_tool_use():
    from scripts.hooks_gen import generate
    out = generate(mode="A")
    assert "PreToolUse" not in out["hooks"]


def test_mode_b_registers_pre_tool_use():
    from scripts.hooks_gen import generate
    out = generate(mode="B")
    assert "PreToolUse" in out["hooks"]
    # Same command shape as the other events
    block = out["hooks"]["PreToolUse"][0]
    assert block["matcher"] == "*"
    assert block["hooks"][0]["type"] == "command"
    assert "speak.py" in block["hooks"][0]["command"]


def test_mode_c_registers_pre_tool_use():
    from scripts.hooks_gen import generate
    out = generate(mode="C")
    assert "PreToolUse" in out["hooks"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_hooks_gen.py -v`
Expected: The two `mode_b/c_registers_pre_tool_use` tests FAIL (`KeyError` or `AssertionError`); the mode_a test passes incidentally because PreToolUse isn't there yet.

- [ ] **Step 4: Update MODE_EVENTS**

In `scripts/hooks_gen.py`, replace the `MODE_EVENTS` dict:

```python
MODE_EVENTS = {
    "A": ("Stop",),
    "B": ("Stop", "PreToolUse"),
    "C": ("Stop", "PreToolUse", "Notification"),
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_hooks_gen.py -v`
Expected: All PASS.

- [ ] **Step 6: Regenerate hooks.json for the local install**

The user's `config.json` is mode B; regenerate `hooks/hooks.json` so the new event is registered for runtime use:

```bash
cd ${CLAUDE_PLUGIN_ROOT}
python3 -c "
from scripts.hooks_gen import write
from pathlib import Path
import json
cfg = json.load(open('config.json'))
write(mode=cfg['mode'], out_path=Path('hooks/hooks.json'))
"
cat hooks/hooks.json
```

Expected: `hooks/hooks.json` now contains a `PreToolUse` entry alongside `Stop`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`.

- [ ] **Step 7: Commit**

```bash
git add scripts/hooks_gen.py tests/test_hooks_gen.py hooks/hooks.json
git commit -m "feat(hooks_gen): register PreToolUse in modes B and C"
```

---

## Task 5: Update mode B SessionStart instruction

The model's pre-tool narration via `speak_cli` is now redundant with the hook — and worse, it would double-speak the same tool call (hook says "running this," model says "Looking that up"). Update the SessionStart instruction so the model knows to use `speak_cli` only for non-tool interjections.

**Files:**
- Modify: `scripts/speak.py` (just `_mode_b_narration_instructions`)

- [ ] **Step 1: Find the existing instruction**

Run: `grep -n "_mode_b_narration_instructions" scripts/speak.py`
Read the function — it returns a multi-line string telling the model to call speak_cli before tool calls.

- [ ] **Step 2: Write a quick assertion-style test**

Add to `tests/test_speak_other_events.py`:

```python
def test_mode_b_session_start_instruction_forbids_tool_narration():
    """The hook handles tool cues now. The model must not double-narrate."""
    from scripts.speak import _mode_b_narration_instructions
    text = _mode_b_narration_instructions()
    text_lower = text.lower()
    # Must explicitly tell the model NOT to narrate tool calls.
    assert "tool" in text_lower
    assert any(kw in text_lower for kw in ("not narrate", "don't narrate", "do not narrate",
                                           "automatic", "handled automatically"))
    # Must still mention interjections / non-tool moments as the speak_cli use case.
    assert any(kw in text_lower for kw in ("interjection", "between tool", "non-tool",
                                           "comment", "remark"))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_speak_other_events.py::test_mode_b_session_start_instruction_forbids_tool_narration -v`
Expected: FAIL — the current instruction tells the model to narrate before tools, not to avoid it.

- [ ] **Step 4: Rewrite `_mode_b_narration_instructions`**

In `scripts/speak.py`, replace the function body:

```python
def _mode_b_narration_instructions() -> str:
    """Instructions Claude needs for non-tool interjections in mode B.

    Tool cues are handled automatically by the PreToolUse hook — the model
    must NOT narrate tool calls itself, or the user hears double-speak (hook
    says 'running this,' model says 'Looking that up'). speak_cli is reserved
    for interjections between tool calls."""
    cli = plugin_root() / "scripts" / "speak_cli.py"
    return (
        "Claude Voice mode B is active. Tool cues are handled automatically "
        "by a PreToolUse hook — DO NOT narrate before tool calls yourself. "
        "Doing so causes the user to hear two phrases for one tool.\n\n"
        "speak_cli is reserved for short interjections BETWEEN tool calls "
        "(not before them) — moments like 'hmm, that's odd,' 'that didn't "
        "work, let me try something else,' or 'interesting.' Invoke via Bash:\n\n"
        f"    python3 \"{cli}\" \"<short remark>\"\n\n"
        "Keep it varied (2-6 words) and use it sparingly — only when the "
        "remark adds something the user wouldn't get from the audible tool "
        "cue plus the visual transcript. The CLI returns immediately; audio "
        "plays in the background. Final-response speech at end of turn is "
        "automatic — don't call the CLI for that either."
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_speak_other_events.py::test_mode_b_session_start_instruction_forbids_tool_narration -v`
Expected: PASS.

- [ ] **Step 6: Run the full speak suite for regressions**

Run: `python3 -m pytest tests/test_speak_other_events.py tests/test_speak_stop.py tests/test_speak_cli.py tests/test_speak_pre_tool_use.py -q`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/speak.py tests/test_speak_other_events.py
git commit -m "feat(speak): forbid model tool narration in mode B (hook handles it)"
```

---

## Task 6: End-to-end smoke test

A live run-through against the user's actual session, since Mode B's full UX is hard to assert in unit tests.

**Files:**
- (No code changes — verification only)

- [ ] **Step 1: Confirm the dev plugin is loaded**

The user is running Claude Code with `claude --plugin-dir ~/dev/claude-chat`. Confirm by checking:

```bash
tail -3 ~/.claude/voice/voice.log
```

Look for log lines pointing to `${CLAUDE_PLUGIN_ROOT}/...` rather than the cache path. If still pointing at cache, ask the user to restart with `--plugin-dir`.

- [ ] **Step 2: Run /reload-plugins so the new hooks.json takes effect**

Ask the user to run `/reload-plugins` in their Claude Code session. This re-reads `hooks/hooks.json` so PreToolUse is now registered.

- [ ] **Step 3: Trigger a known-mapped tool**

In the conversation, perform a Bash call (e.g. ask the user to confirm the smoke test is running, then run `git log --oneline -1`). Watch the voice log:

```bash
tail -5 ~/.claude/voice/voice.log
```

Expected log line: `INFO claude_voice: PreToolUse: tool=Bash phrase='running this'`. The user should hear "running this" before any text response audio.

- [ ] **Step 4: Trigger an unmapped (MCP-style) tool**

If feasible, trigger a tool with a fallback name (e.g. invoke any MCP tool the user has connected, or trigger `WebFetch` if not currently in the map). Verify the log shows the cleaned-name fallback and the user hears it.

- [ ] **Step 5: Confirm mode A is silent**

Run the voice-setup skill (or have the user manually) to switch to mode A: `config.json` mode → "A", regenerate `hooks/hooks.json`, `/reload-plugins`. Trigger a Bash call. Voice log should NOT contain a PreToolUse line, and the user should hear silence before tool calls (they still hear end-of-turn speech).

Switch back to mode B: edit config.json mode → "B", regenerate `hooks/hooks.json`, `/reload-plugins`.

- [ ] **Step 6: Confirm a user override works**

Edit `config.json` to add:

```json
{
  "...": "...",
  "tool_phrases": {"Bash": "executing the command"}
}
```

`/reload-plugins`. Trigger a Bash call. Log should show `phrase='executing the command'` and the user should hear that exact phrase.

Restore `config.json` to remove the override before finishing.

- [ ] **Step 7: Done**

If all five smoke checks pass, the feature ships. No further commit — the plan is complete.

---

## Self-Review

**Spec coverage:**
- Goal — making mode B narration deterministic via PreToolUse hook → Tasks 1, 3, 4 ✓
- Mode A stays silent for tools → Task 4 (MODE_EVENTS), Task 3 gate, Task 6 smoke check ✓
- Mode C inherits B's behavior → Task 4 ✓
- New `scripts/tool_phrases.py` with DEFAULTS, clean_mcp_name, lookup → Task 1 ✓
- `config.json` `tool_phrases` overrides → Task 2, Task 6 verification ✓
- Hook payload only uses `tool_name` and `session_id` → Task 3 handler ✓
- Phrase contract: lookup always returns non-empty `str` → Task 1 tests + lookup body ✓
- `playback.enqueue` directly (no voicify) → Task 3 handler ✓
- Mode B SessionStart instruction updated → Task 5 ✓
- Test coverage matches spec's testing section — `test_tool_phrases.py`, `test_speak_pre_tool_use.py`, hooks_gen extension, config extension → Tasks 1–4 ✓
- Out-of-scope items (debouncing, argument-aware phrasing, variants) — not implemented; no tasks for them ✓
- Acceptance criteria — Task 6 smoke test exercises all six criteria ✓

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later" markers.
- No "similar to Task N" references — each code block is self-contained.
- Every code-changing step has a complete code block.

**Type consistency:**
- `lookup(tool_name: str, overrides: Mapping[str, str] | None = None) -> str` — used consistently in Task 1 implementation, Task 3 handler call (`tool_phrases.lookup(tool_name, cfg.tool_phrases)`), and tests.
- `cfg.tool_phrases` returns `Mapping[str, str]` — matches the `Config` field added in Task 2 and the consumer in Task 3.
- `clean_mcp_name(tool_name: str) -> str` — single signature, no drift.
- `_handle_pre_tool_use(payload: dict) -> None` — matches `_DISPATCH` registration and existing handler signatures.

No issues to fix. Plan is complete.
