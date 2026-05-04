from scripts.extract import strip_for_voice


def test_drops_fenced_code_blocks():
    text = "Here we go.\n\n```python\nprint('hi')\n```\n\nDone."
    assert strip_for_voice(text) == "Here we go. Done."


def test_unwraps_speakable_inline_code():
    """Single words and short phrases inside backticks are unwrapped and
    spoken — they're often common English words ('say', 'Edit', 'Bash')
    that would leave glaring gaps if dropped."""
    assert strip_for_voice("Run `pytest -v` to verify the build works") \
        == "Run pytest -v to verify the build works"
    assert strip_for_voice("Either `say` or piper handles fallback locally") \
        == "Either say or piper handles fallback locally"
    assert strip_for_voice("The `Edit` tool is fired most often") \
        == "The Edit tool is fired most often"


def test_drops_path_like_inline_code():
    """Paths inside backticks are noise — never speakable."""
    assert strip_for_voice("See `~/piper-voices/en_US-amy-medium.onnx` for the model") \
        == "See for the model"
    assert strip_for_voice("The plugin lives at `${CLAUDE_PLUGIN_ROOT}` for now") \
        == "The plugin lives at for now"


def test_drops_dunder_identifiers():
    """Dunder-style identifiers (Python __methods__, MCP tool names) sound
    awful read aloud."""
    assert strip_for_voice("Calling `mcp__claude_ai_Gmail__search_threads` for that") \
        == "Calling for that"
    assert strip_for_voice("The `__init__` method runs at construction time") \
        == "The method runs at construction time"


def test_unwraps_snake_case_as_spoken_words():
    """Snake_case identifiers (single underscores, no dunders) get unwrapped
    with underscores replaced by spaces so they read as natural words."""
    assert strip_for_voice("Use `speak_cli` for between-tool interjections only") \
        == "Use speak cli for between-tool interjections only"
    assert strip_for_voice("The `pre_tool_use` hook fires automatically now") \
        == "The pre tool use hook fires automatically now"


def test_unwraps_git_refs_as_spoken_words():
    """Git refs like `origin/main` and `feat/auth-rewrite` are speakable —
    they're namespaces, not file paths. Slashes get replaced with spaces
    so TTS reads them as plain words instead of spelling 'slash'."""
    assert strip_for_voice("Pushed the change to `origin/main` just now") \
        == "Pushed the change to origin main just now"
    assert strip_for_voice("Switched to branch `feat/auth-rewrite` for work") \
        == "Switched to branch feat auth-rewrite for work"
    # A ref with multiple segments still reads cleanly.
    assert strip_for_voice("Resetting `refs/heads/main` to upstream now") \
        == "Resetting refs heads main to upstream now"


def test_still_drops_path_with_slash_and_dot():
    """Real file paths (slash + extension dot, or leading ~/) keep getting
    dropped. The new git-ref rule must not weaken the path filter."""
    assert strip_for_voice("Edit `src/main.py` then run the test suite") \
        == "Edit then run the test suite"
    assert strip_for_voice("Open `~/foo/bar` and read it carefully") \
        == "Open and read it carefully"
    assert strip_for_voice("Run `./scripts/build.sh` in your terminal now") \
        == "Run in your terminal now"
    # Multi-segment absolute paths still drop.
    assert strip_for_voice("Check `/etc/hosts` for the entry now") \
        == "Check for the entry now"


def test_unwraps_slash_commands_as_spoken_words():
    """Slash commands like `/reload-plugins` and `/voice` look path-ish
    (leading /) but are commands the user runs in chat. They must be
    spoken — single-segment tokens after a leading slash are commands,
    not absolute paths."""
    assert strip_for_voice("Run `/reload-plugins` to pick up the change now") \
        == "Run reload-plugins to pick up the change now"
    assert strip_for_voice("The command `/voice` is built into the harness") \
        == "The command voice is built into the harness"
    assert strip_for_voice("Use `/help` to see the available commands today") \
        == "Use help to see the available commands today"


def test_drops_sha_like_inline_code():
    """Git SHAs in backticks (e.g. 'commit `f43f52a`') sound terrible
    spelled out letter-by-letter. Revision ranges like 'a..b' or 'a...b'
    that show up in commit-pushed messages also count."""
    assert strip_for_voice("Pushed in commit `f43f52a` earlier today") \
        == "Pushed in commit earlier today"
    assert strip_for_voice("The `0d6b4ae` hash is the current head right now") \
        == "The hash is the current head right now"
    # Two-dot range from `git push` output
    assert strip_for_voice("Pushed `41d1d3c..8e3bf54` to origin main earlier") \
        == "Pushed to origin main earlier"
    # Three-dot range (symmetric difference)
    assert strip_for_voice("Diff `abc1234...def5678` shows the change clearly") \
        == "Diff shows the change clearly"


def test_drops_function_call_inline_code():
    """Anything with parens / equals / semicolons reads as code."""
    assert strip_for_voice("The `lookup(name)` returns a phrase from the map") \
        == "The returns a phrase from the map"
    assert strip_for_voice("Set `enabled = true` in your config to enable") \
        == "Set in your config to enable"


def test_drops_overly_long_inline_code():
    """Very long backtick spans are almost certainly code, not speech."""
    long = "x" * 35
    assert "x" not in strip_for_voice(f"the value is `{long}` if you check it")


def test_drops_file_line_refs():
    text = "Check scripts/extract.py:42 for the bug."
    assert strip_for_voice(text) == "Check for the bug."


def test_drops_bare_urls():
    text = "See https://example.com for details."
    assert strip_for_voice(text) == "See for details."


def test_flattens_markdown_emphasis():
    assert strip_for_voice("**bold** and *italic* text") == "bold and italic text"
    assert strip_for_voice("_underscore_ words remain visible") == "underscore words remain visible"


def test_drops_header_only_lines():
    text = "# Title\n\nReal sentence.\n\n## Sub\n\nMore prose."
    assert strip_for_voice(text) == "Real sentence. More prose."


def test_drops_lone_bullet_markers():
    # An empty bullet line with nothing meaningful should disappear.
    text = "Intro line\n\n-\n\nOutro line"
    assert strip_for_voice(text) == "Intro line Outro line"


def test_keeps_bullet_content_as_prose():
    text = "Intro\n\n- alpha\n- beta\n\nOutro"
    out = strip_for_voice(text)
    assert "alpha" in out and "beta" in out and "Intro" in out and "Outro" in out


def test_strips_emoji():
    text = "Done! 🎉 Great work 👍 here."
    assert strip_for_voice(text) == "Done! Great work here."


def test_collapses_whitespace():
    assert strip_for_voice("a\n\n\nb     c") == "a b c"


def test_pure_code_response_returns_empty():
    text = "```python\nprint('x')\n```"
    assert strip_for_voice(text) == ""


def test_under_three_words_returns_empty():
    assert strip_for_voice("Yes.") == ""
    assert strip_for_voice("All done.") == ""
    assert strip_for_voice("Hi there.") == ""


def test_three_or_more_words_passes():
    assert strip_for_voice("All tests passed now.") == "All tests passed now."


def test_normalizes_temperature_units():
    """`13°C` must be spoken as 'degrees Celsius', not eaten by the emoji strip."""
    out = strip_for_voice("Houston is at 13°C and feels mild.")
    assert "degrees Celsius" in out
    assert "13C" not in out
    assert "13" in out


def test_normalizes_fahrenheit():
    out = strip_for_voice("It is 80°F outside today.")
    assert "degrees Fahrenheit" in out
    assert "80F" not in out


def test_normalizes_km_per_hour():
    out = strip_for_voice("Wind at 18 km/h from the south.")
    assert "kilometers per hour" in out
    assert "km/h" not in out


def test_normalizes_percent():
    out = strip_for_voice("Confidence is 95% on this estimate.")
    assert "percent" in out
    assert "95%" not in out


def test_normalizes_tilde_to_roughly():
    out = strip_for_voice("Took ~5 minutes to finish the job.")
    assert "roughly" in out
    assert "~5" not in out


def test_normalizes_ampersand_to_and():
    out = strip_for_voice("Pull request from foo & bar today.")
    assert " and " in out
    assert "&" not in out
