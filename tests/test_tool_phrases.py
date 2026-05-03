"""Tests for scripts/tool_phrases.py — tool→phrase map for PreToolUse cues."""


def test_lookup_returns_default_for_known_tool():
    from scripts.tool_phrases import lookup
    assert lookup("Bash") == "running a command"
    assert lookup("Read") == "reading the file"


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
    assert lookup("Bash", overrides=bad) == "running a command"
    assert lookup("Read", overrides=bad) == "reading the file"
    assert lookup("Write", overrides=bad) == "scribbling"


def test_lookup_handles_none_overrides():
    from scripts.tool_phrases import lookup
    assert lookup("Bash", overrides=None) == "running a command"


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


# --- Parameterized templates: substitute extracted target into the cue ---

def test_lookup_inserts_basename_for_edit():
    from scripts.tool_phrases import lookup
    assert lookup("Edit", tool_input={"file_path": "/Users/jason/dev/claude-chat/scripts/speak.py"}) \
        == "editing speak.py"


def test_lookup_inserts_basename_for_read_and_write():
    from scripts.tool_phrases import lookup
    assert lookup("Read", tool_input={"file_path": "/tmp/foo.json"}) == "reading foo.json"
    assert lookup("Write", tool_input={"file_path": "/x/y/z.md"}) == "writing z.md"


def test_lookup_inserts_first_word_for_bash():
    from scripts.tool_phrases import lookup
    assert lookup("Bash", tool_input={"command": "git status --short"}) == "running git"
    assert lookup("Bash", tool_input={"command": "python3 -m pytest -q"}) == "running python3"


def test_lookup_inserts_hostname_for_webfetch():
    from scripts.tool_phrases import lookup
    assert lookup("WebFetch", tool_input={"url": "https://example.com/some/path?token=secret"}) \
        == "fetching example.com"


def test_lookup_inserts_truncated_query_for_websearch():
    from scripts.tool_phrases import lookup
    assert lookup("WebSearch", tool_input={"query": "current weather in San Jose California today"}) \
        == "searching current weather in San Jose"


def test_lookup_strips_skill_namespace():
    from scripts.tool_phrases import lookup
    assert lookup("Skill", tool_input={"skill": "superpowers:brainstorming"}) \
        == "loading skill brainstorming"


def test_lookup_falls_back_to_static_when_extractor_returns_none():
    """Empty/missing input should produce the static phrase, not 'editing '."""
    from scripts.tool_phrases import lookup
    assert lookup("Edit", tool_input={"file_path": ""}) == "making an edit"
    assert lookup("Edit", tool_input={}) == "making an edit"
    assert lookup("Bash", tool_input={"command": "   "}) == "running a command"


def test_lookup_static_when_no_tool_input_provided():
    """Calling lookup without tool_input still works — static phrase."""
    from scripts.tool_phrases import lookup
    assert lookup("Edit") == "making an edit"
    assert lookup("Bash") == "running a command"


def test_lookup_overrides_with_template_substitute_target():
    """A user override containing {target} opts into the same substitution
    flow, using the same per-tool extractor."""
    from scripts.tool_phrases import lookup
    out = lookup("Edit",
                 tool_input={"file_path": "/x/notes.md"},
                 overrides={"Edit": "tweaking {target}"})
    assert out == "tweaking notes.md"


def test_lookup_overrides_without_template_are_static():
    """Plain-string user overrides don't depend on tool_input."""
    from scripts.tool_phrases import lookup
    out = lookup("Edit",
                 tool_input={"file_path": "/x/notes.md"},
                 overrides={"Edit": "modifying"})
    assert out == "modifying"


def test_lookup_override_with_template_falls_back_when_extraction_fails():
    """A user override with {target} but no extractable input should fall
    back to the static default, not produce a clipped sentence."""
    from scripts.tool_phrases import lookup
    out = lookup("Edit",
                 tool_input={},
                 overrides={"Edit": "tweaking {target}"})
    assert out == "making an edit"


def test_lookup_unknown_tool_with_input_still_uses_fallback():
    from scripts.tool_phrases import lookup
    assert lookup("Mystery", tool_input={"x": "y"}) == "calling Mystery"


def test_lookup_handles_non_dict_tool_input_gracefully():
    """Defensive: extractors only accept Mappings; pass through to static."""
    from scripts.tool_phrases import lookup
    # tool_input as a list (malformed payload) — should not crash
    assert lookup("Edit", tool_input=None) == "making an edit"
