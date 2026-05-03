"""Tool name → narration phrase map for the PreToolUse hook (modes B and C).

The phrase a user hears before a tool fires is a pure function of the tool
name, optionally the tool's input arguments, and the user's optional
override map. No model interaction, no transcript reading.

Two layers of phrase data:

- `DEFAULTS` is the static fallback per tool ("making an edit"). Used when
  no input is available, no extractor exists, or the extractor can't pull a
  usable target from the input.
- `PARAMETERIZED` holds templates with a `{target}` placeholder ("editing
  {target}") for tools where speaking the relevant argument adds useful
  context — file basename for editor tools, first word for Bash, hostname
  for WebFetch, first few words for WebSearch, etc. Each entry pairs with
  an extractor in `_EXTRACTORS` that knows where to find the target in the
  tool's input dict.

User overrides (from `config.json`) are single strings. They may contain
`{target}` to opt into per-call substitution, or be plain to override
unconditionally.
"""
from __future__ import annotations
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.parse import urlparse


DEFAULTS: Mapping[str, str] = MappingProxyType({
    "Bash": "running a command",
    "Read": "reading the file",
    "Edit": "making an edit",
    "Write": "writing a file",
    "Grep": "searching the code",
    "Glob": "finding the files",
    "WebFetch": "fetching the page",
    "WebSearch": "looking that up",
    "TodoWrite": "updating the tasks",
    "Task": "starting an agent",
    "Agent": "starting an agent",
    "Skill": "loading a skill",
    "ToolSearch": "loading more tools",
    "ScheduleWakeup": "scheduling a wakeup",
    "ExitPlanMode": "finalizing the plan",
    "BashOutput": "checking the output",
    "KillShell": "stopping the process",
    "NotebookEdit": "editing the notebook",
})


PARAMETERIZED: Mapping[str, str] = MappingProxyType({
    "Bash": "running {target}",
    "Read": "reading {target}",
    "Edit": "editing {target}",
    "Write": "writing {target}",
    "Grep": "searching for {target}",
    "Glob": "finding {target}",
    "WebFetch": "fetching {target}",
    "WebSearch": "searching {target}",
    "Skill": "loading skill {target}",
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
    return body.replace("__", " ").replace("_", " ")


def _basename(value: object) -> str | None:
    """Strip directory components — only the leaf filename is spoken."""
    if not isinstance(value, str) or not value.strip():
        return None
    name = Path(value).name
    return name or None


def _first_word(value: object) -> str | None:
    """Bash commands are noisy; just say the program name."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().split()[0] or None


def _hostname(value: object) -> str | None:
    """URLs may carry auth tokens or long paths; just say the hostname."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        host = urlparse(value).hostname
    except Exception:
        return None
    return host or None


def _first_words(value: object, n: int) -> str | None:
    """Cap a phrase at N words so search queries stay short."""
    if not isinstance(value, str) or not value.strip():
        return None
    words = value.strip().split()
    if not words:
        return None
    return " ".join(words[:n])


def _last_skill_segment(value: object) -> str | None:
    """`superpowers:brainstorming` → `'brainstorming'`."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().split(":")[-1] or None


_EXTRACTORS: Mapping[str, Callable[[Mapping], str | None]] = MappingProxyType({
    "Bash": lambda inp: _first_word(inp.get("command")),
    "Read": lambda inp: _basename(inp.get("file_path")),
    "Edit": lambda inp: _basename(inp.get("file_path")),
    "Write": lambda inp: _basename(inp.get("file_path")),
    "Grep": lambda inp: _first_words(inp.get("pattern"), n=4),
    "Glob": lambda inp: _first_words(inp.get("pattern"), n=4),
    "WebFetch": lambda inp: _hostname(inp.get("url")),
    "WebSearch": lambda inp: _first_words(inp.get("query"), n=5),
    "Skill": lambda inp: _last_skill_segment(inp.get("skill")),
})


def _extract_target(tool_name: str, tool_input: Mapping | None) -> str | None:
    if tool_input is None:
        return None
    extractor = _EXTRACTORS.get(tool_name)
    if extractor is None:
        return None
    try:
        return extractor(tool_input)
    except Exception:
        return None


def _render(template: str, tool_name: str, tool_input: Mapping | None,
            static_fallback: str) -> str:
    """If `template` contains `{target}`, substitute the per-tool extracted
    target. If extraction fails or no extractor exists, fall back to the
    static phrase rather than producing a clipped sentence like 'editing '."""
    if "{target}" not in template:
        return template
    target = _extract_target(tool_name, tool_input)
    if not target:
        return static_fallback
    return template.replace("{target}", target)


def lookup(
    tool_name: str,
    tool_input: Mapping | None = None,
    overrides: Mapping[str, str] | None = None,
) -> str:
    """Return the cue phrase for `tool_name`.

    Resolution order:
      1. user override (if `overrides` provides a string entry for `tool_name`).
         May contain `{target}` to opt into per-call substitution.
      2. parameterized template (PARAMETERIZED) with `{target}` substituted
         from `tool_input` via the registered extractor.
      3. static default (DEFAULTS).
      4. fallback: `'calling {clean_mcp_name(tool_name)}'`.

    Steps 1 and 2 fall back to the static phrase from step 3 when their
    `{target}` extractor returns nothing — so cues never become "editing "
    or other clipped strings.

    Non-string override entries are silently ignored so a typo'd config
    can't crash the hook (voice is a UX layer, never load-bearing).
    """
    static = DEFAULTS.get(tool_name) or f"calling {clean_mcp_name(tool_name)}"

    if overrides:
        v = overrides.get(tool_name)
        if isinstance(v, str):
            return _render(v, tool_name, tool_input, static)

    template = PARAMETERIZED.get(tool_name)
    if template:
        return _render(template, tool_name, tool_input, static)

    return static
