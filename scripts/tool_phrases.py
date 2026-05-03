"""Tool name → narration phrase map for the PreToolUse hook (modes B and C).

The phrase a user hears before a tool fires is a pure function of the tool
name and the user's optional override map. No model interaction, no
transcript reading — just a dict lookup.
"""
from __future__ import annotations
from types import MappingProxyType
from typing import Mapping


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
