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
