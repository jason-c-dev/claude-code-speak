from scripts.extract import strip_for_voice


def test_drops_fenced_code_blocks():
    text = "Here we go.\n\n```python\nprint('hi')\n```\n\nDone."
    assert strip_for_voice(text) == "Here we go. Done."


def test_drops_inline_code():
    text = "Run `pytest -v` to verify."
    assert strip_for_voice(text) == "Run to verify."


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
