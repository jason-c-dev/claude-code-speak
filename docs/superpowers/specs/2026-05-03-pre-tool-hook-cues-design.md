# Pre-Tool Hook Cues — Design Spec

**Date:** 2026-05-03
**Status:** Designed, not yet implemented
**Owner:** Jason

## Goal

Make Claude's pre-tool narration in mode B reliable and deterministic by moving it from model-driven (`speak_cli` calls the model remembers to make) to hook-driven (`PreToolUse` fires for every tool call, looks up a phrase from a tool→phrase map, and enqueues it). Mode A stays silent for tool work; mode C inherits the same behavior as B plus its existing notification path.

## Why

The current mode B narration loop has two problems:

1. **It's best-effort.** The model has to remember to invoke `speak_cli` before every tool call. When the model forgets, there's silence — exactly the opposite of "feels alive."
2. **Hooks can't see the model's pre-tool prose.** The transcript JSONL flush lags hook-fire time (verified empirically while building the Stop msg-id anchor), so the natural alternative — read the assistant's just-emitted text in `PreToolUse` and speak it — returns the previous turn's prose, not the current pre-tool narration.

A hook fires on time, every time, and has the tool name in its payload. A static map from tool name to short phrase is enough to give the user reliable feedback that "something is happening" without depending on the model's mid-turn discipline. Conversational variety is sacrificed for reliability — the user can switch to mode A if even mapped phrases feel chatty.

## Non-goals

- Per-tool-argument phrasing. `Bash` always says "running this," not "running a long pipeline" or "checking the build." Argument-aware phrasing can come later.
- Debouncing/coalescing. If the model does five `Edit`s in a row, the user hears five "editing" cues. Mode A is the opt-out.
- Random phrase variants per tool. v1 is deterministic — single phrase per tool name.
- Cross-mode toggling. Tool cues are tied to mode B/C registration in `hooks_gen.py`. There's no separate `tool_cues: false` flag.
- Replacing `speak_cli` for non-tool moments. Interjections like "hmm, that's odd" between tool calls still go through `speak_cli`. The hook only handles pre-tool narration.

## High-level architecture

```
PreToolUse event
       │ (payload includes tool_name, session_id)
       ▼
 speak.py: _handle_pre_tool_use
       │
       ├── gate: cfg.enabled, cfg.mode in {B, C}, session interactive,
       │         session is the active session
       │
       ▼
 tool_phrases.lookup(tool_name, cfg.tool_phrases) → str
       │
       │ (defaults dict + user-config overrides; falls back to
       │  "calling {clean_mcp_name(tool_name)}" for unknown tools)
       ▼
 playback.enqueue(session_id, phrase)
       │
       ▼
 player worker → Deepgram → ffplay   (existing playback path)
```

No transcript reading. No model interaction. The cue is a pure function of the tool name and the user's config.

## Components

### `scripts/tool_phrases.py` (new)

```python
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
    """mcp__claude_ai_Gmail__search_threads → 'claude ai Gmail search threads'."""
    if not tool_name.startswith("mcp__"):
        return tool_name
    return tool_name[len("mcp__"):].replace("__", " ").replace("_", " ")

def lookup(tool_name: str, overrides: Mapping[str, str] | None = None) -> str:
    """Look up a phrase. Overrides win over defaults; unknown tools fall back
    to 'calling {clean_mcp_name}'."""
    merged = dict(DEFAULTS)
    if overrides:
        merged.update({k: v for k, v in overrides.items()
                       if isinstance(k, str) and isinstance(v, str)})
    if tool_name in merged:
        return merged[tool_name]
    return f"calling {clean_mcp_name(tool_name)}"
```

The map is intentionally narrow — only the tools we actually expect. Anything unmapped falls through to the cleaned tool name.

### `scripts/config.py`

Add an optional `tool_phrases: dict[str, str]` field to `Config`. Validation mirrors `say_voice_map`: must be a dict, ignore non-string entries, default to empty mapping. The tool_phrases map is read on every `PreToolUse` (config is loaded fresh per hook fire, so no reload concerns).

### `scripts/speak.py`

Add `_handle_pre_tool_use(payload: dict)`:

```python
def _handle_pre_tool_use(payload: dict) -> None:
    cfg = load_config()
    if cfg.mode not in ("B", "C"):
        return  # mode A stays silent for tool work
    session_id = payload.get("session_id") or "default"
    s = state_mod.load(session_id)
    if not s.interactive or not state_mod.is_active_session(session_id):
        return
    tool_name = payload.get("tool_name") or ""
    if not tool_name:
        return
    phrase = tool_phrases.lookup(tool_name, cfg.tool_phrases)
    log.info("PreToolUse: tool=%s phrase=%r", tool_name, phrase)
    playback.enqueue(session_id, phrase)
```

Register in `_DISPATCH` under key `"PreToolUse"`.

The phrase goes through `playback.enqueue` directly — it does NOT pass through `extract.voicify` or `extract.strip_for_voice`. The phrase is already short, intentional, and doesn't need a Haiku rewrite.

### `scripts/hooks_gen.py`

Update `MODE_EVENTS`:

```python
MODE_EVENTS = {
    "A": ("Stop",),
    "B": ("Stop", "PreToolUse"),
    "C": ("Stop", "PreToolUse", "Notification"),
}
```

The setup skill regenerates `hooks/hooks.json` whenever the user changes mode, so this propagates without extra work.

### Mode B SessionStart instruction (`speak.py:_mode_b_narration_instructions`)

Currently this string tells the model to call `speak_cli` before every tool call. With the hook in place, the instruction changes to: tool cues are automatic; only call `speak_cli` for interjections between tool calls (e.g. "hmm, that's odd" or "that didn't work, let me try something else"), never before a tool. Concrete wording is in implementation; the spec just locks the contract: the model is told NOT to narrate tool calls.

## Data flow / contracts

- **Hook payload**: `{"hook_event_name": "PreToolUse", "tool_name": str, "session_id": str, ...}`. We rely only on `tool_name` and `session_id`. Other fields (tool_input, transcript_path) are ignored — keeps coupling minimal.
- **Phrase contract**: lookup always returns a non-empty `str`. Per-tool silencing is not supported in v1 — if cues feel chatty for any tool, the user switches to mode A. Override entries replace defaults but cannot suppress them.
- **Playback contract**: cue text is enqueued exactly as returned. The player worker handles synthesis via the existing streaming pipeline.

## Error handling

- Unknown `tool_name` → falls back to "calling {clean_mcp_name(name)}". Never raises.
- Missing `tool_name` in payload → silent no-op (log at INFO).
- `cfg.tool_phrases` malformed → ignored entries logged at WARNING; lookup proceeds with defaults.
- All exceptions in `_handle_pre_tool_use` caught at the existing `run()` boundary in `speak.py`. Voice is a UX layer, never load-bearing.

## Testing

- **`tests/test_tool_phrases.py`**: covers `lookup` precedence (overrides beat defaults), MCP name cleaning (`mcp__foo__bar_baz` → "calling foo bar baz"), unknown-tool fallback, mismatched-type override entries are ignored.
- **`tests/test_speak_pre_tool_use.py`**: covers gating (mode A returns silently, non-interactive session returns silently, non-active session returns silently), payload parsing (missing `tool_name` is a no-op), enqueue path (correct text reaches `playback.enqueue` for known + unknown tools).
- **`tests/test_hooks_gen.py`**: extend to assert `PreToolUse` is registered for B and C, NOT for A.
- **`tests/test_config.py`**: extend to cover `tool_phrases` parsing — valid dict, missing key, malformed types.

## Out of scope (deferred to v2+)

- **Debouncing**: if `Edit` fires three times in 200ms, hear "editing" three times. Mode A is the v1 mute switch.
- **Argument-aware phrasing**: `Bash` always says "running this" regardless of whether it's `ls` or a 30-minute build. Adding tool_input awareness to the lookup would let us say "running the build" vs "checking output."
- **Phrase variants per tool**: a list with random pick would feel more natural over time. v1 is deterministic.
- **Per-mode tool cue toggle**: a separate `tool_cues: false` config flag could decouple tool cues from mode B/C. v1 ties them together; if anyone actually wants mode B with no tool cues, that's the v2 toggle.

## Acceptance criteria

1. With `mode: "B"` and the hook installed, every tool call produces audible narration (tested with at least Bash, Read, Edit, WebFetch).
2. With `mode: "A"`, tool calls produce zero audio (tested with same tool set).
3. User-overridden phrases in `config.json` take precedence over defaults (verify with a custom override).
4. Unknown tool names produce a "calling {cleaned-name}" cue without errors.
5. `speak_cli` continues to work for non-tool interjections in mode B (regression check).
6. All new tests pass; existing test suite continues to pass.
