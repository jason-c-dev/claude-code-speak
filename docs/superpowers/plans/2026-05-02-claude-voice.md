# Claude Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Claude Voice plugin per the design at `docs/superpowers/specs/2026-05-02-claude-voice-design.md` — a Claude Code plugin that speaks the natural-language portions of Claude's responses via Deepgram Aura-2, with macOS `say` as a local fallback, behind three configurable modes (A/B/C).

**Architecture:** A single Python entrypoint (`scripts/speak.py`) is invoked by Claude Code hooks with hook event JSON on stdin. It dispatches by event type, extracts speakable text from the transcript, runs it through a heuristic strip → Haiku rewrite → Deepgram TTS pipeline (with `say` fallback), and enqueues the audio for playback via `afplay`. Per-session state lives at `~/.claude/voice/state/`, secrets at `~/.claude/voice/.env`, plugin config in the repo at `config.json`.

**Tech Stack:** Python 3.10+ (stdlib only for HTTP/audio/state), `claude-agent-sdk` for Haiku, `pytest` for tests, `afplay` and `say` (macOS), Deepgram Aura-2 REST API.

---

## Working assumptions

- All work happens in `/Users/jason/dev/claude-chat` (the plugin repo, already `git init`ed with one commit).
- macOS only for v1.
- Engineer is comfortable with Python and pytest but new to Claude Code plugins, the Claude Agent SDK, and Deepgram.
- Use `pip` and the system Python by default; `uv` is fine if available but not required.
- Each task's "Commit" step uses a conventional commit message and ends with the same `Co-Authored-By` trailer used in earlier commits in this repo.

## Task 1: Project scaffolding and pytest baseline

**Files:**
- Create: `pyproject.toml`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`
- Create: `scripts/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
cd /Users/jason/dev/claude-chat
mkdir -p scripts tests skills/voice-setup hooks .claude-plugin
touch scripts/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "claude-voice"
version = "0.1.0"
description = "Give Claude a voice — Claude Code plugin"
requires-python = ">=3.10"
dependencies = [
    "claude-agent-sdk>=0.1.0",
]

[project.optional-dependencies]
test = ["pytest>=7.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures for claude-voice tests."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make `scripts/` importable as a top-level package in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def voice_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.claude/voice/ to a tmp dir for the duration of a test."""
    home = tmp_path / "voice_home"
    home.mkdir()
    (home / "state").mkdir()
    (home / "tmp").mkdir()
    monkeypatch.setenv("CLAUDE_VOICE_HOME", str(home))
    return home


@pytest.fixture
def plugin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ${CLAUDE_PLUGIN_ROOT} to a tmp dir."""
    root = tmp_path / "plugin"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    return root


@pytest.fixture
def write_config(plugin_root: Path):
    """Helper to write a config.json under the plugin root."""
    def _write(cfg: dict[str, Any]) -> Path:
        path = plugin_root / "config.json"
        path.write_text(json.dumps(cfg))
        return path
    return _write
```

- [ ] **Step 4: Write `tests/test_smoke.py`**

```python
def test_pytest_runs():
    assert 1 + 1 == 2
```

- [ ] **Step 5: Install pytest and the SDK**

```bash
cd /Users/jason/dev/claude-chat
python3 -m pip install -e '.[test]'
```

Expected: pip resolves and installs `pytest` and `claude-agent-sdk`.

- [ ] **Step 6: Run the smoke test**

```bash
cd /Users/jason/dev/claude-chat
python3 -m pytest tests/test_smoke.py -v
```

Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml scripts/__init__.py tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
chore: scaffold pyproject + pytest baseline

Project layout (scripts/ + tests/), pytest config, shared fixtures
for redirecting plugin root and voice home dirs to tmp paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Logging helper

**Files:**
- Create: `scripts/log.py`
- Create: `tests/test_log.py`

- [ ] **Step 1: Write the failing test**

`tests/test_log.py`:
```python
import logging
from pathlib import Path

import pytest


def test_get_logger_writes_to_voice_home(voice_home: Path, monkeypatch):
    from scripts import log

    log.reset_for_testing()
    logger = log.get_logger()
    logger.info("hello world")
    log.flush()

    log_file = voice_home / "voice.log"
    assert log_file.exists()
    contents = log_file.read_text()
    assert "hello world" in contents


def test_logger_is_idempotent(voice_home: Path):
    from scripts import log

    log.reset_for_testing()
    a = log.get_logger()
    b = log.get_logger()
    assert a is b
    # Logging twice must not double up handlers (only one line per call).
    a.info("once")
    log.flush()
    contents = (voice_home / "voice.log").read_text()
    assert contents.count("once") == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_log.py -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `scripts.log`.

- [ ] **Step 3: Implement `scripts/log.py`**

```python
"""Shared logger used by every script in this plugin.

Writes to ~/.claude/voice/voice.log (overridable via $CLAUDE_VOICE_HOME).
Idempotent: get_logger() always returns the same Logger with a single handler.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

_LOGGER: logging.Logger | None = None


def voice_home() -> Path:
    override = os.environ.get("CLAUDE_VOICE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "voice"


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    home = voice_home()
    home.mkdir(parents=True, exist_ok=True)
    log_path = home / "voice.log"

    logger = logging.getLogger("claude_voice")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    _LOGGER = logger
    return logger


def flush() -> None:
    if _LOGGER is None:
        return
    for h in _LOGGER.handlers:
        h.flush()


def reset_for_testing() -> None:
    """Drop the cached logger so the next get_logger() rebuilds with a fresh path."""
    global _LOGGER
    if _LOGGER is not None:
        for h in list(_LOGGER.handlers):
            h.close()
            _LOGGER.removeHandler(h)
    _LOGGER = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_log.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/log.py tests/test_log.py
git commit -m "$(cat <<'EOF'
feat(log): shared file logger at ~/.claude/voice/voice.log

Idempotent get_logger(); $CLAUDE_VOICE_HOME overrides the directory
for tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Config loader

**Files:**
- Create: `scripts/config.py`
- Create: `tests/test_config.py`
- Create: `config.example.json`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest


def test_loads_config_from_plugin_root(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "B", "voice": "aura-2-orion-en"})
    cfg = config.load()
    assert cfg.enabled is True
    assert cfg.mode == "B"
    assert cfg.voice == "aura-2-orion-en"


def test_defaults_apply_when_keys_missing(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "A"})
    cfg = config.load()
    assert cfg.voice == "aura-2-thalia-en"
    assert cfg.primary_tts == "deepgram"
    assert cfg.fallback_tts == "say"
    assert cfg.rewrite is True
    assert cfg.min_words == 3
    assert cfg.max_haiku_chars == 4000
    assert cfg.max_deepgram_chars == 2000
    assert cfg.speech_rate == pytest.approx(1.0)


def test_missing_file_returns_disabled_defaults(plugin_root: Path):
    from scripts import config

    cfg = config.load()
    assert cfg.enabled is False  # if the user hasn't configured anything, stay silent


def test_invalid_mode_falls_back_to_A(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True, "mode": "Z"})
    cfg = config.load()
    assert cfg.mode == "A"


def test_say_voice_map_default(plugin_root: Path, write_config):
    from scripts import config

    write_config({"enabled": True})
    cfg = config.load()
    assert cfg.say_voice_map["aura-2-thalia-en"] == "Samantha"
    assert cfg.say_voice_map["aura-2-orion-en"] == "Alex"
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: FAIL — `scripts.config` does not exist.

- [ ] **Step 3: Implement `scripts/config.py`**

```python
"""Load and validate config.json from the plugin root."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from scripts.log import get_logger

VALID_MODES = {"A", "B", "C"}

DEFAULT_SAY_VOICE_MAP = {
    "aura-2-thalia-en": "Samantha",
    "aura-2-orion-en": "Alex",
    "aura-2-luna-en": "Samantha",
    "aura-2-asteria-en": "Samantha",
    "aura-2-zeus-en": "Daniel",
    "aura-2-pandora-en": "Karen",
}


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    mode: str = "A"
    voice: str = "aura-2-thalia-en"
    primary_tts: str = "deepgram"
    fallback_tts: str = "say"
    say_voice_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SAY_VOICE_MAP))
    rewrite: bool = True
    haiku_model: str = "claude-haiku-4-5-20251001"
    min_words: int = 3
    max_haiku_chars: int = 4000
    max_deepgram_chars: int = 2000
    speech_rate: float = 1.0


def plugin_root() -> Path:
    override = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if override:
        return Path(override)
    # Fallback: assume we're being run from the plugin dir.
    return Path(__file__).resolve().parent.parent


def load() -> Config:
    log = get_logger()
    path = plugin_root() / "config.json"
    if not path.exists():
        log.info("config.json not found at %s; voice disabled", path)
        return Config(enabled=False)

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.error("config.json is not valid JSON: %s", e)
        return Config(enabled=False)

    mode = raw.get("mode", "A")
    if mode not in VALID_MODES:
        log.warning("config.json has invalid mode %r; falling back to 'A'", mode)
        mode = "A"

    merged_map = dict(DEFAULT_SAY_VOICE_MAP)
    merged_map.update(raw.get("say_voice_map") or {})

    return Config(
        enabled=bool(raw.get("enabled", False)),
        mode=mode,
        voice=str(raw.get("voice", "aura-2-thalia-en")),
        primary_tts=str(raw.get("primary_tts", "deepgram")),
        fallback_tts=str(raw.get("fallback_tts", "say")),
        say_voice_map=merged_map,
        rewrite=bool(raw.get("rewrite", True)),
        haiku_model=str(raw.get("haiku_model", "claude-haiku-4-5-20251001")),
        min_words=int(raw.get("min_words", 3)),
        max_haiku_chars=int(raw.get("max_haiku_chars", 4000)),
        max_deepgram_chars=int(raw.get("max_deepgram_chars", 2000)),
        speech_rate=float(raw.get("speech_rate", 1.0)),
    )
```

- [ ] **Step 4: Write `config.example.json`**

```json
{
  "enabled": true,
  "mode": "A",
  "voice": "aura-2-thalia-en",
  "primary_tts": "deepgram",
  "fallback_tts": "say",
  "rewrite": true,
  "haiku_model": "claude-haiku-4-5-20251001",
  "min_words": 3,
  "max_haiku_chars": 4000,
  "max_deepgram_chars": 2000,
  "speech_rate": 1.0,
  "say_voice_map": {
    "aura-2-thalia-en": "Samantha",
    "aura-2-orion-en": "Alex"
  }
}
```

- [ ] **Step 5: Run tests**

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py tests/test_config.py config.example.json
git commit -m "$(cat <<'EOF'
feat(config): typed config loader with safe defaults

Loads config.json from plugin root with frozen-dataclass result.
Missing file or invalid mode degrade to disabled/A respectively;
say_voice_map merges user overrides over defaults.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Per-session state file

**Files:**
- Create: `scripts/state.py`
- Create: `tests/test_state.py`

The state file tracks (a) the byte offset already spoken per assistant message id (so mode B/C don't repeat), and (b) the PID of the currently-playing `afplay` process (so UserPromptSubmit can kill it).

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
from pathlib import Path

import pytest


def test_create_and_read_state(voice_home: Path):
    from scripts import state

    s = state.load("session-abc")
    assert s.session_id == "session-abc"
    assert s.spoken_offsets == {}
    assert s.current_pid is None


def test_record_and_read_offset(voice_home: Path):
    from scripts import state

    s = state.load("session-abc")
    s.spoken_offsets["msg-1"] = 120
    state.save(s)

    s2 = state.load("session-abc")
    assert s2.spoken_offsets == {"msg-1": 120}


def test_set_and_clear_pid(voice_home: Path):
    from scripts import state

    s = state.load("session-xyz")
    s.current_pid = 12345
    state.save(s)

    assert state.load("session-xyz").current_pid == 12345

    s = state.load("session-xyz")
    s.current_pid = None
    state.save(s)
    assert state.load("session-xyz").current_pid is None


def test_remove_session(voice_home: Path):
    from scripts import state

    s = state.load("session-bye")
    s.spoken_offsets["m"] = 1
    state.save(s)
    assert (voice_home / "state" / "session-bye.json").exists()

    state.remove("session-bye")
    assert not (voice_home / "state" / "session-bye.json").exists()


def test_clean_stale(voice_home: Path, monkeypatch):
    """clean_stale removes state files older than the cutoff."""
    from scripts import state
    import os, time

    fresh = voice_home / "state" / "fresh.json"
    stale = voice_home / "state" / "stale.json"
    fresh.write_text("{}")
    stale.write_text("{}")

    # Backdate stale to 25 hours ago.
    old = time.time() - (25 * 3600)
    os.utime(stale, (old, old))

    state.clean_stale(max_age_seconds=24 * 3600)

    assert fresh.exists()
    assert not stale.exists()
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_state.py -v
```

Expected: FAIL — `scripts.state` does not exist.

- [ ] **Step 3: Implement `scripts/state.py`**

```python
"""Per-session state file at ~/.claude/voice/state/<session_id>.json."""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from scripts.log import get_logger, voice_home


def state_dir() -> Path:
    d = voice_home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class SessionState:
    session_id: str
    spoken_offsets: dict[str, int] = field(default_factory=dict)
    current_pid: int | None = None
    queue: list[str] = field(default_factory=list)  # paths of pending audio files


def _path(session_id: str) -> Path:
    return state_dir() / f"{session_id}.json"


def load(session_id: str) -> SessionState:
    path = _path(session_id)
    if not path.exists():
        return SessionState(session_id=session_id)

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        get_logger().warning("state file %s corrupt; resetting", path)
        return SessionState(session_id=session_id)

    return SessionState(
        session_id=raw.get("session_id", session_id),
        spoken_offsets=raw.get("spoken_offsets") or {},
        current_pid=raw.get("current_pid"),
        queue=raw.get("queue") or [],
    )


def save(state: SessionState) -> None:
    path = _path(state.session_id)
    path.write_text(json.dumps(asdict(state)))


def remove(session_id: str) -> None:
    path = _path(session_id)
    if path.exists():
        path.unlink()


def clean_stale(max_age_seconds: int = 24 * 3600) -> None:
    cutoff = time.time() - max_age_seconds
    for f in state_dir().glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_state.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(state): per-session state file with offsets and pid

JSON-backed SessionState stored at ~/.claude/voice/state/<id>.json.
Tracks per-message spoken byte offsets, current afplay PID, and the
playback queue. clean_stale() reaps files older than 24h.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Heuristic strip (`extract.strip_for_voice`)

**Files:**
- Create: `scripts/extract.py` (initial version with strip only)
- Create: `tests/test_extract_strip.py`
- Create: `tests/fixtures/__init__.py`

- [ ] **Step 1: Write the failing test**

`tests/test_extract_strip.py`:
```python
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
    assert strip_for_voice("_underscore_ words") == "underscore words"


def test_drops_header_only_lines():
    text = "# Title\n\nReal sentence.\n\n## Sub\n\nMore prose."
    assert strip_for_voice(text) == "Real sentence. More prose."


def test_drops_lone_bullet_markers():
    # An empty bullet line with nothing meaningful should disappear.
    text = "Intro\n\n-\n\nOutro"
    assert strip_for_voice(text) == "Intro Outro"


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
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_extract_strip.py -v
```

Expected: FAIL — `scripts.extract` does not exist.

- [ ] **Step 3: Implement `scripts/extract.py`**

```python
"""Speech extraction: strip non-prose, then rewrite via Haiku."""
from __future__ import annotations
import re
import unicodedata

# 1. Fenced code blocks.
_FENCED = re.compile(r"```.*?```", flags=re.DOTALL)
# 2. Inline code.
_INLINE = re.compile(r"`[^`]*`")
# 3. file:line[:col] refs — at least one slash or dot in the prefix to avoid eating "10:30am"
_FILE_LINE = re.compile(r"\b[\w./\-]*[/.][\w./\-]+:\d+(?::\d+)?\b")
# 4. URLs.
_URL = re.compile(r"https?://\S+")
# 5. Markdown emphasis.
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITAL_STAR = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_ITAL_UNDER = re.compile(r"(?<!_)_([^_]+)_(?!_)")
# 6. Lone marker lines (header / bullet / hr).
_HEADER_LINE = re.compile(r"^\s*#{1,6}\s.*$", flags=re.MULTILINE)
_LONE_MARKER_LINE = re.compile(r"^\s*[-*>]\s*$", flags=re.MULTILINE)
_HR_LINE = re.compile(r"^\s*[-=*]{3,}\s*$", flags=re.MULTILINE)
_BULLET_PREFIX = re.compile(r"^\s*[-*>]\s+", flags=re.MULTILINE)
# 7. Whitespace collapse.
_WHITESPACE = re.compile(r"\s+")

MIN_WORDS_DEFAULT = 3


def _strip_emoji(s: str) -> str:
    return "".join(ch for ch in s if not _is_emoji(ch))


def _is_emoji(ch: str) -> bool:
    # Heuristic: anything in the Unicode "Symbol, Other" or pictograph blocks.
    # We treat astral plane symbols (>U+2600 and emoji blocks) as emoji.
    cat = unicodedata.category(ch)
    if cat == "So":
        return True
    cp = ord(ch)
    if 0x1F300 <= cp <= 0x1FAFF:
        return True
    if 0x2600 <= cp <= 0x27BF:
        return True
    return False


def strip_for_voice(text: str, min_words: int = MIN_WORDS_DEFAULT) -> str:
    """Extract speakable prose. Returns '' if nothing worth saying."""
    s = text
    s = _FENCED.sub(" ", s)
    s = _INLINE.sub(" ", s)
    s = _FILE_LINE.sub(" ", s)
    s = _URL.sub(" ", s)
    s = _BOLD.sub(r"\1", s)
    s = _ITAL_STAR.sub(r"\1", s)
    s = _ITAL_UNDER.sub(r"\1", s)
    s = _HEADER_LINE.sub(" ", s)
    s = _LONE_MARKER_LINE.sub(" ", s)
    s = _HR_LINE.sub(" ", s)
    s = _BULLET_PREFIX.sub("", s)
    s = _strip_emoji(s)
    s = _WHITESPACE.sub(" ", s).strip()

    if not s:
        return ""
    if len(s.split()) < min_words:
        return ""
    return s
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_extract_strip.py -v
```

Expected: `13 passed`. If a regex bites a fixture, fix the regex (not the test) — the spec language defines the contract.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract.py tests/test_extract_strip.py
git commit -m "$(cat <<'EOF'
feat(extract): heuristic strip for vocal intent

strip_for_voice removes code blocks, inline code, file:line refs,
URLs, markdown emphasis, header/bullet/HR lines, and emoji; collapses
whitespace; returns empty string under min_words.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Haiku rewrite (`extract.voicify`)

**Files:**
- Modify: `scripts/extract.py` (add `voicify`)
- Create: `tests/test_extract_voicify.py`

- [ ] **Step 1: Write the failing test**

`tests/test_extract_voicify.py`:
```python
import pytest


def test_voicify_returns_haiku_output(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        assert model == "claude-haiku-4-5"
        assert "stripped input" in text
        return "Polished spoken sentence."

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("stripped input here", model="claude-haiku-4-5")
    assert out == "Polished spoken sentence."


def test_voicify_truncates_long_input(monkeypatch):
    from scripts import extract

    captured = {}

    async def fake_async(text, model):
        captured["text"] = text
        return "ok"

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    long_text = "X" * 10000
    extract.voicify(long_text, max_chars=4000)
    assert len(captured["text"]) == 4000
    # Truncate from the start (keep the most recent prose).
    assert captured["text"] == long_text[-4000:]


def test_voicify_falls_back_to_input_on_sdk_error(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        raise RuntimeError("auth failed")

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("plain stripped text", rewrite_enabled=True)
    assert out == "plain stripped text"  # raw fallback


def test_voicify_skips_when_disabled(monkeypatch):
    from scripts import extract

    called = {"n": 0}

    async def fake_async(text, model):
        called["n"] += 1
        return "should not be called"

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    out = extract.voicify("plain", rewrite_enabled=False)
    assert out == "plain"
    assert called["n"] == 0


def test_voicify_passes_empty_through(monkeypatch):
    from scripts import extract

    async def fake_async(text, model):
        raise AssertionError("should not be called for empty input")

    monkeypatch.setattr(extract, "_voicify_async", fake_async)
    assert extract.voicify("") == ""
    assert extract.voicify("   ") == ""
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_extract_voicify.py -v
```

Expected: FAIL — `voicify` and `_voicify_async` don't exist yet.

- [ ] **Step 3: Append `voicify` to `scripts/extract.py`**

Add at the end of `scripts/extract.py`:
```python
import asyncio

from scripts.log import get_logger

VOICIFY_SYSTEM_PROMPT = (
    "You are rewriting text so it sounds natural when spoken aloud. "
    "Take the input and return one or two natural spoken sentences, in the "
    "same first-person voice as the original. Skip technical references, "
    "code-like fragments, or anything that doesn't sound natural aloud. "
    "If the input has nothing worth saying aloud, return the empty string. "
    "Return ONLY the rewritten text — no preface, no quotation marks, no commentary."
)


async def _voicify_async(text: str, model: str) -> str:
    # Imported lazily so unit tests can run without the SDK installed.
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=VOICIFY_SYSTEM_PROMPT,
        allowed_tools=[],
    )
    parts: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(text)
        async for msg in client.receive_response():
            for block in getattr(msg, "content", []) or []:
                t = getattr(block, "text", None)
                if t:
                    parts.append(t)
    return "".join(parts).strip()


def voicify(
    text: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_chars: int = 4000,
    rewrite_enabled: bool = True,
) -> str:
    """Rewrite stripped text into natural spoken prose via Haiku.

    Falls back to returning `text` unchanged if rewrite is disabled or the
    SDK call fails. Returns '' for empty input.
    """
    if not text or not text.strip():
        return ""
    if not rewrite_enabled:
        return text
    if len(text) > max_chars:
        text = text[-max_chars:]
    try:
        return asyncio.run(_voicify_async(text, model))
    except Exception as e:  # SDK failure must never propagate
        get_logger().warning("voicify Haiku call failed: %s; using stripped text", e)
        return text
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_extract_voicify.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/extract.py tests/test_extract_voicify.py
git commit -m "$(cat <<'EOF'
feat(extract): voicify rewrite via Claude Agent SDK

voicify() polishes stripped text into 1-2 natural spoken sentences
using Haiku 4.5 over the user's Claude Code OAuth (no extra API key).
Falls back to raw input on SDK failure; truncates from start at
max_chars; skips when rewrite is disabled or input is empty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: TTS — Deepgram primary path

**Files:**
- Create: `scripts/tts.py`
- Create: `tests/test_tts_deepgram.py`

- [ ] **Step 1: Write the failing test**

`tests/test_tts_deepgram.py`:
```python
import io
import urllib.error
from pathlib import Path

import pytest


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_deepgram_writes_mp3_and_returns_path(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResponse(b"\x49\x44\x33fake-mp3-bytes")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)

    path = tts._synthesize_deepgram(
        text="hello world",
        voice="aura-2-thalia-en",
        api_key="dg_test",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is not None
    assert path.exists()
    assert path.suffix == ".mp3"
    assert path.read_bytes().startswith(b"\x49\x44\x33")
    assert "aura-2-thalia-en" in captured["url"]
    assert "encoding=mp3" in captured["url"]
    assert captured["headers"]["Authorization"] == "Token dg_test"
    assert b'"text": "hello world"' in captured["body"]


def test_deepgram_truncates_over_limit(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        return _FakeResponse(b"mp3")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    long_text = "y" * 5000
    tts._synthesize_deepgram(
        text=long_text,
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
    )
    # JSON-encoded body should contain at most ~2010 chars of "y" (2000 + JSON overhead).
    assert captured["body"].count(b"y") == 2000


def test_deepgram_returns_none_on_http_error(voice_home, monkeypatch):
    from scripts import tts

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauth", {}, io.BytesIO(b'{}'))

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    path = tts._synthesize_deepgram(
        text="hi there",
        voice="aura-2-thalia-en",
        api_key="bad",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is None


def test_deepgram_returns_none_on_timeout(voice_home, monkeypatch):
    from scripts import tts

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    path = tts._synthesize_deepgram(
        text="hi there",
        voice="aura-2-thalia-en",
        api_key="k",
        speech_rate=1.0,
        max_chars=2000,
    )
    assert path is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_tts_deepgram.py -v
```

Expected: FAIL — `scripts.tts` does not exist.

- [ ] **Step 3: Implement `scripts/tts.py` (Deepgram path only for now)**

```python
"""Text-to-speech with Deepgram primary and macOS `say` fallback."""
from __future__ import annotations
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from scripts.log import get_logger, voice_home

DEEPGRAM_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_TIMEOUT_SECONDS = 15


def _tmp_dir() -> Path:
    d = voice_home() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _synthesize_deepgram(
    *,
    text: str,
    voice: str,
    api_key: str,
    speech_rate: float,
    max_chars: int,
) -> Path | None:
    """Return path to written mp3 on success, or None on any failure."""
    log = get_logger()
    if not api_key:
        return None

    if len(text) > max_chars:
        log.info("deepgram input %d chars exceeds %d; truncating", len(text), max_chars)
        text = text[:max_chars]

    qs = urllib.parse.urlencode({
        "model": voice,
        "encoding": "mp3",
        "speed": f"{speech_rate:.2f}",
    })
    url = f"{DEEPGRAM_URL}?{qs}"
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=DEEPGRAM_TIMEOUT_SECONDS) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        log.warning("deepgram returned %d: %s", e.code, e.reason)
        return None
    except urllib.error.URLError as e:
        log.warning("deepgram network error: %s", e.reason)
        return None
    except Exception as e:
        log.warning("deepgram unexpected error: %s", e)
        return None

    out = _tmp_dir() / f"{uuid.uuid4().hex}.mp3"
    out.write_bytes(audio)
    return out
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_tts_deepgram.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/tts.py tests/test_tts_deepgram.py
git commit -m "$(cat <<'EOF'
feat(tts): Deepgram Aura-2 synthesis with truncation and error handling

POSTs to https://api.deepgram.com/v1/speak with Token auth, mp3
encoding, configurable speed. Truncates input over max_chars and
returns None on any HTTP/URL/timeout error so the caller can fall back.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: TTS — `say` fallback and full chain

**Files:**
- Modify: `scripts/tts.py` (add `_synthesize_say` and `synthesize` chain)
- Create: `tests/test_tts_chain.py`

- [ ] **Step 1: Write the failing test**

`tests/test_tts_chain.py`:
```python
from pathlib import Path

import pytest


def test_say_writes_aiff(voice_home, monkeypatch):
    from scripts import tts

    captured = {}

    def fake_run(args, check, capture_output, timeout):
        captured["args"] = args
        # Pretend `say` wrote the output file.
        out_idx = args.index("-o") + 1
        Path(args[out_idx]).write_bytes(b"FORM????AIFF")

        class R:
            returncode = 0
            stderr = b""
        return R()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    path = tts._synthesize_say(text="testing one two three", voice_name="Samantha")
    assert path is not None
    assert path.suffix == ".aiff"
    assert path.exists()
    assert "Samantha" in captured["args"]
    assert "-v" in captured["args"]


def test_say_returns_none_on_failure(voice_home, monkeypatch):
    from scripts import tts

    def fake_run(args, check, capture_output, timeout):
        raise FileNotFoundError("say not found")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    assert tts._synthesize_say(text="hello", voice_name="Samantha") is None


def test_chain_uses_deepgram_when_configured(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: Path("/tmp/dg.mp3"))
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: pytest.fail("should not fallback"))

    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/dg.mp3")


def test_chain_falls_back_to_say_when_deepgram_fails(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)

    captured = {}
    def fake_say(*, text, voice_name):
        captured["voice"] = voice_name
        return Path("/tmp/say.aiff")
    monkeypatch.setattr(tts, "_synthesize_say", fake_say)

    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/say.aiff")
    assert captured["voice"] == "Samantha"  # mapped from aura-2-thalia-en


def test_chain_uses_say_when_no_deepgram_key(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True, "voice": "aura-2-thalia-en", "primary_tts": "deepgram"})

    monkeypatch.setattr(tts, "_synthesize_deepgram",
                        lambda **kw: pytest.fail("should not call DG without key"))
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: Path("/tmp/say.aiff"))
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    out = tts.synthesize("hello there world")
    assert out == Path("/tmp/say.aiff")


def test_chain_returns_none_when_both_fail(voice_home, monkeypatch, write_config):
    from scripts import tts

    write_config({"enabled": True})
    monkeypatch.setattr(tts, "_synthesize_deepgram", lambda **kw: None)
    monkeypatch.setattr(tts, "_synthesize_say", lambda **kw: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")

    assert tts.synthesize("hello there world") is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_tts_chain.py -v
```

Expected: FAIL — `_synthesize_say` and `synthesize` don't exist yet.

- [ ] **Step 3: Append to `scripts/tts.py`**

Add at the end of `scripts/tts.py`:
```python
import os
from scripts.config import load as load_config

SAY_TIMEOUT_SECONDS = 15


def _synthesize_say(*, text: str, voice_name: str) -> Path | None:
    log = get_logger()
    out = _tmp_dir() / f"{uuid.uuid4().hex}.aiff"
    try:
        result = subprocess.run(
            ["say", "-v", voice_name, "-o", str(out), text],
            check=False,
            capture_output=True,
            timeout=SAY_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            log.warning("say failed: rc=%d stderr=%s", result.returncode, result.stderr[:200])
            return None
        if not out.exists():
            log.warning("say returned 0 but produced no file at %s", out)
            return None
        return out
    except FileNotFoundError:
        log.warning("`say` binary not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        log.warning("say timed out")
        return None
    except Exception as e:
        log.warning("say unexpected error: %s", e)
        return None


def synthesize(text: str) -> Path | None:
    """Synthesize `text` to an audio file using the configured TTS chain.

    Returns the path to a playable file (mp3 or aiff), or None if every
    backend failed.
    """
    log = get_logger()
    cfg = load_config()
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")

    # Primary: Deepgram, only if configured *and* a key is present.
    if cfg.primary_tts == "deepgram" and api_key:
        path = _synthesize_deepgram(
            text=text,
            voice=cfg.voice,
            api_key=api_key,
            speech_rate=cfg.speech_rate,
            max_chars=cfg.max_deepgram_chars,
        )
        if path is not None:
            return path
        log.info("deepgram failed; falling back to %s", cfg.fallback_tts)

    # Fallback: macOS say.
    if cfg.fallback_tts == "say" or cfg.primary_tts == "say":
        say_voice = cfg.say_voice_map.get(cfg.voice, "Samantha")
        path = _synthesize_say(text=text, voice_name=say_voice)
        if path is not None:
            return path
        log.warning("say also failed; speech will be silent")

    return None
```

Also add a top-of-file env loader so `DEEPGRAM_API_KEY` is read from `~/.claude/voice/.env` if not already in the environment. Add right after the imports block in `scripts/tts.py`:
```python
def _load_env_file_into_os() -> None:
    """If DEEPGRAM_API_KEY isn't already set, read it from ~/.claude/voice/.env."""
    if os.environ.get("DEEPGRAM_API_KEY"):
        return
    env_path = voice_home() / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        # Only set if not already set; never overwrite real env.
        os.environ.setdefault(k.strip(), v)


_load_env_file_into_os()
```

(Place this above `_synthesize_deepgram` and below the existing `_tmp_dir` / constants.)

Note: the import of `os` belongs in the existing import block — fold it in, don't add a duplicate import.

- [ ] **Step 4: Run all tts tests**

```bash
python3 -m pytest tests/test_tts_deepgram.py tests/test_tts_chain.py -v
```

Expected: all pass (10 total).

- [ ] **Step 5: Commit**

```bash
git add scripts/tts.py tests/test_tts_chain.py
git commit -m "$(cat <<'EOF'
feat(tts): say fallback chain and dotenv loader

synthesize() picks the configured primary backend, falls through to
say on any non-success, and returns None only if every backend failed.
Loads DEEPGRAM_API_KEY from ~/.claude/voice/.env when not in env.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Audio playback queue

**Files:**
- Create: `scripts/playback.py`
- Create: `tests/test_playback.py`

The queue is per-session: a file at `~/.claude/voice/state/<session>.json` holds an ordered list of pending audio paths. `enqueue(session, path)` appends and, if no afplay is currently running, starts one. When `afplay` exits, a wrapper script picks the next item and starts again. `clear_and_kill(session)` kills the current PID and empties the queue.

To keep this simple and POSIX-friendly, we use a small **wrapper subprocess** that loops over the queue file: it starts via `Popen`, drains the queue file, and exits when the queue is empty.

- [ ] **Step 1: Write the failing test**

`tests/test_playback.py`:
```python
import os
import time
from pathlib import Path

import pytest


def _make_audio_file(voice_home: Path, name: str = "a.mp3") -> Path:
    p = voice_home / "tmp" / name
    p.write_bytes(b"audio")
    return p


def test_enqueue_appends_to_queue(voice_home, monkeypatch):
    from scripts import playback, state

    a = _make_audio_file(voice_home, "a.mp3")
    b = _make_audio_file(voice_home, "b.mp3")

    started_pids = []
    def fake_popen(args, **kw):
        class P:
            pid = 90000 + len(started_pids)
        started_pids.append(P.pid)
        return P()
    monkeypatch.setattr(playback.subprocess, "Popen", fake_popen)

    playback.enqueue("sess1", a)
    playback.enqueue("sess1", b)

    s = state.load("sess1")
    # Both paths queued; first one's pid recorded as current.
    assert str(a) in s.queue or s.queue == [str(b)]  # a may have already been popped to play
    # Exactly one player started for this burst (second enqueue reuses).
    assert len(started_pids) == 1


def test_clear_and_kill(voice_home, monkeypatch):
    from scripts import playback, state

    killed = []
    monkeypatch.setattr(playback.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    s = state.load("sess2")
    s.current_pid = 12345
    s.queue = ["/tmp/x.mp3", "/tmp/y.mp3"]
    state.save(s)

    playback.clear_and_kill("sess2")

    assert killed and killed[0][0] == 12345
    s2 = state.load("sess2")
    assert s2.queue == []
    assert s2.current_pid is None


def test_clear_and_kill_handles_missing_pid(voice_home):
    from scripts import playback, state
    # Should not raise when there's nothing to kill.
    playback.clear_and_kill("nonexistent-session")
    s = state.load("nonexistent-session")
    assert s.queue == []
    assert s.current_pid is None


def test_player_loop_drains_queue(voice_home, monkeypatch, tmp_path):
    """The internal player loop processes queued files in FIFO order."""
    from scripts import playback, state

    a = _make_audio_file(voice_home, "a.mp3")
    b = _make_audio_file(voice_home, "b.mp3")

    s = state.load("sess3")
    s.queue = [str(a), str(b)]
    state.save(s)

    played = []
    def fake_run(args, check, capture_output, timeout):
        played.append(args[-1])  # last arg is the audio path
        Path(args[-1]).unlink(missing_ok=True)
        class R:
            returncode = 0
            stderr = b""
        return R()
    monkeypatch.setattr(playback.subprocess, "run", fake_run)

    playback.player_loop("sess3")

    assert played == [str(a), str(b)]
    s2 = state.load("sess3")
    assert s2.queue == []
    assert s2.current_pid is None
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_playback.py -v
```

Expected: FAIL — `scripts.playback` does not exist.

- [ ] **Step 3: Implement `scripts/playback.py`**

```python
"""FIFO audio queue per session.

`enqueue(session_id, path)` appends to the session's queue and (if no
player is currently running) launches a small Python child that drains
the queue by calling `afplay` for each file. The child's PID is recorded
in the session state so `clear_and_kill` can interrupt it.
"""
from __future__ import annotations
import os
import signal
import subprocess
import sys
from pathlib import Path

from scripts.log import get_logger
from scripts import state as state_mod

AFPLAY_TIMEOUT_SECONDS = 60


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just not ours
    except Exception:
        return False


def enqueue(session_id: str, audio_path: Path) -> None:
    log = get_logger()
    s = state_mod.load(session_id)
    s.queue.append(str(audio_path))
    state_mod.save(s)

    if s.current_pid and _is_pid_alive(s.current_pid):
        log.info("player already running for %s (pid=%d); queued %s",
                 session_id, s.current_pid, audio_path.name)
        return

    # Spawn a fresh player process pointed at this session.
    child = subprocess.Popen(
        [sys.executable, "-m", "scripts.playback", "--player", session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    s = state_mod.load(session_id)
    s.current_pid = child.pid
    state_mod.save(s)
    log.info("started player pid=%d for %s", child.pid, session_id)


def clear_and_kill(session_id: str) -> None:
    log = get_logger()
    s = state_mod.load(session_id)
    if s.current_pid:
        try:
            os.kill(s.current_pid, signal.SIGTERM)
            log.info("killed player pid=%d for %s", s.current_pid, session_id)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.warning("failed to kill pid=%d: %s", s.current_pid, e)
    s.queue = []
    s.current_pid = None
    state_mod.save(s)


def player_loop(session_id: str) -> None:
    """Drain the session queue. Runs in a child process."""
    log = get_logger()
    while True:
        s = state_mod.load(session_id)
        if not s.queue:
            s.current_pid = None
            state_mod.save(s)
            return
        next_path = s.queue.pop(0)
        state_mod.save(s)

        try:
            subprocess.run(
                ["afplay", next_path],
                check=False,
                capture_output=True,
                timeout=AFPLAY_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            log.warning("afplay missing; aborting player_loop for %s", session_id)
            s = state_mod.load(session_id)
            s.queue = []
            s.current_pid = None
            state_mod.save(s)
            return
        except subprocess.TimeoutExpired:
            log.warning("afplay timed out on %s", next_path)
        except Exception as e:
            log.warning("afplay error on %s: %s", next_path, e)
        finally:
            try:
                Path(next_path).unlink(missing_ok=True)
            except Exception:
                pass


def _main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--player":
        player_loop(argv[2])
        return 0
    print("playback.py is invoked internally; nothing to do here.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_playback.py -v
```

Expected: `4 passed`. If the first test races (Popen mock interaction), tighten the fixture: ensure `state.save` is called before checking, or assert on `len(started_pids) <= 1`.

- [ ] **Step 5: Commit**

```bash
git add scripts/playback.py tests/test_playback.py
git commit -m "$(cat <<'EOF'
feat(playback): per-session FIFO queue with detached afplay loop

enqueue() appends to the session queue and spawns a player_loop child
if none is running. clear_and_kill() SIGTERMs the player and empties
the queue (used by UserPromptSubmit). Audio files are unlinked after
playback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Transcript reader

**Files:**
- Create: `scripts/transcript.py`
- Create: `tests/test_transcript.py`

Reads the JSONL transcript file passed by Claude Code in the hook event payload. Provides helpers for "last assistant message text" (mode A's Stop) and "assistant text in current message past offset N" (mode B's Pre/Post hooks).

- [ ] **Step 1: Write the failing test**

`tests/test_transcript.py`:
```python
import json
from pathlib import Path

import pytest


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_last_assistant_text_returns_concatenated_text_blocks(tmp_path):
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant",
         "message": {
             "id": "msg_1",
             "content": [
                 {"type": "text", "text": "Hello there."},
                 {"type": "tool_use", "name": "Read"},
                 {"type": "text", "text": "Done!"},
             ]
         }},
    ])
    assert last_assistant_text(p) == ("msg_1", "Hello there. Done!")


def test_last_assistant_text_returns_none_when_no_assistant(tmp_path):
    from scripts.transcript import last_assistant_text

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}])
    assert last_assistant_text(p) is None


def test_text_after_offset(tmp_path):
    from scripts.transcript import current_assistant_text_after

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {
             "id": "msg_2",
             "content": [
                 {"type": "text", "text": "Looking at the file."},
                 {"type": "tool_use", "name": "Read"},
                 {"type": "text", "text": "Found the bug here."},
             ]
         }},
    ])
    assert current_assistant_text_after(p, "msg_2", offset=0) == "Looking at the file. Found the bug here."
    full = "Looking at the file. Found the bug here."
    # If we already spoke through char 20, we should get only the rest.
    assert current_assistant_text_after(p, "msg_2", offset=20) == full[20:]


def test_text_after_offset_unknown_id_returns_full(tmp_path):
    from scripts.transcript import current_assistant_text_after

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant",
         "message": {
             "id": "msg_3",
             "content": [{"type": "text", "text": "Some text."}],
         }},
    ])
    # Asking about a different id returns the full last-message text from offset 0.
    assert current_assistant_text_after(p, "msg_OTHER", offset=0) == "Some text."
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_transcript.py -v
```

Expected: FAIL — `scripts.transcript` does not exist.

- [ ] **Step 3: Implement `scripts/transcript.py`**

```python
"""Read assistant text out of a Claude Code transcript JSONL file."""
from __future__ import annotations
import json
from pathlib import Path


def _iter_entries(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _assistant_text_blocks(entry: dict) -> list[str]:
    msg = entry.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text") or ""
            if t:
                out.append(t)
    return out


def last_assistant_text(transcript_path: Path) -> tuple[str, str] | None:
    """Return (message_id, concatenated_text) for the last assistant message,
    or None if no assistant message exists."""
    last = None
    for entry in _iter_entries(transcript_path):
        if entry.get("type") == "assistant":
            last = entry
    if last is None:
        return None
    msg = last.get("message") or {}
    msg_id = msg.get("id") or ""
    parts = _assistant_text_blocks(last)
    if not parts:
        return (msg_id, "")
    return (msg_id, " ".join(parts))


def current_assistant_text_after(
    transcript_path: Path, message_id: str, offset: int
) -> str:
    """Return the concatenated text of the last assistant message past `offset`.

    If `message_id` doesn't match the last message's id, returns the full text
    starting from offset 0 (treat as a new message)."""
    res = last_assistant_text(transcript_path)
    if res is None:
        return ""
    last_id, text = res
    if last_id != message_id:
        return text
    if offset <= 0:
        return text
    if offset >= len(text):
        return ""
    return text[offset:]
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_transcript.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/transcript.py tests/test_transcript.py
git commit -m "$(cat <<'EOF'
feat(transcript): read assistant text from Claude Code JSONL transcript

last_assistant_text returns (id, concatenated text) for the most
recent assistant message. current_assistant_text_after returns text
past a recorded offset, or the full text when the message id has
changed (i.e., new message).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Hook entrypoint — Stop event (mode A end-to-end)

**Files:**
- Create: `scripts/speak.py`
- Create: `tests/test_speak_stop.py`

This is the first end-to-end pipeline. Stop hook receives JSON on stdin like:
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../session.jsonl",
  "hook_event_name": "Stop"
}
```

`speak.py` is the single entrypoint for ALL hooks; later tasks add other event handlers.

- [ ] **Step 1: Write the failing test**

`tests/test_speak_stop.py`:
```python
import io
import json
import sys
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_stop_pipeline_strip_voicify_synth_enqueue(voice_home, plugin_root,
                                                    write_config, monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})

    # Build a transcript with a final assistant message containing prose + code.
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant",
         "message": {"id": "m1", "content": [
             {"type": "text", "text": "Here is what I did. ```python\nx=1\n``` That is all."}
         ]}},
    ])

    from scripts import speak, extract, tts, playback

    # Mock the rewrite step so we don't hit the SDK.
    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Polished!"))
    # Mock TTS to return a dummy file.
    fake_audio = voice_home / "tmp" / "fake.mp3"
    fake_audio.parent.mkdir(parents=True, exist_ok=True)
    fake_audio.write_bytes(b"audio")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake_audio)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda session, path: enqueued.append((session, path)))

    payload = {"session_id": "abc", "transcript_path": str(transcript), "hook_event_name": "Stop"}
    rc = speak.run(json.dumps(payload))
    assert rc == 0
    assert enqueued == [("abc", fake_audio)]


def test_stop_with_disabled_config_is_noop(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": False})
    from scripts import speak, tts, playback

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not run"))
    monkeypatch.setattr(playback, "enqueue", lambda *a, **kw: pytest.fail("enqueue should not run"))

    payload = {"session_id": "abc", "transcript_path": "/nonexistent",
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_skips_when_strip_returns_empty(voice_home, plugin_root, write_config,
                                              monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    # Pure code block — strip will return ''.
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "```python\nprint('x')\n```"}
        ]}},
    ])
    from scripts import speak, extract, tts

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: pytest.fail("voicify should not be called"))
    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not be called"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


def test_stop_skips_when_voicify_returns_empty(voice_home, plugin_root, write_config,
                                                monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "A"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Here is some prose to speak about today."}
        ]}},
    ])
    from scripts import speak, extract, tts

    monkeypatch.setattr(extract, "_voicify_async", lambda text, model: _async_return(""))
    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("tts should not run"))
    payload = {"session_id": "x", "transcript_path": str(transcript),
               "hook_event_name": "Stop"}
    assert speak.run(json.dumps(payload)) == 0


# --- helpers ---

async def _async_return(value):
    return value
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_speak_stop.py -v
```

Expected: FAIL — `scripts.speak` does not exist.

- [ ] **Step 3: Implement `scripts/speak.py`**

```python
"""Hook entrypoint. Reads hook event JSON on stdin, dispatches by event."""
from __future__ import annotations
import json
import sys
from pathlib import Path

from scripts import extract, playback, state as state_mod, tts
from scripts.config import load as load_config
from scripts.log import get_logger
from scripts.transcript import last_assistant_text


def _handle_stop(payload: dict) -> None:
    cfg = load_config()
    log = get_logger()
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        log.info("Stop: no assistant message in transcript; nothing to speak")
        return
    msg_id, text = res

    stripped = extract.strip_for_voice(text, min_words=cfg.min_words)
    if not stripped:
        log.info("Stop: stripped text empty; skipping")
        return

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        log.info("Stop: voicify returned empty; skipping")
        return

    audio = tts.synthesize(voiced)
    if audio is None:
        log.warning("Stop: TTS produced no audio; skipping")
        return

    playback.enqueue(session_id, audio)

    # Record that we've spoken through the end of this message.
    s = state_mod.load(session_id)
    s.spoken_offsets[msg_id] = len(text)
    state_mod.save(s)


_DISPATCH = {
    "Stop": _handle_stop,
}


def run(stdin_text: str) -> int:
    log = get_logger()
    cfg = load_config()
    if not cfg.enabled:
        return 0

    try:
        payload = json.loads(stdin_text or "{}")
    except json.JSONDecodeError as e:
        log.warning("speak: invalid hook payload: %s", e)
        return 0

    event = payload.get("hook_event_name") or ""
    handler = _DISPATCH.get(event)
    if handler is None:
        log.info("speak: no handler for event %r; skipping", event)
        return 0

    try:
        handler(payload)
    except Exception as e:
        log.warning("speak: handler %r raised: %s", event, e)
    return 0


def main() -> int:
    return run(sys.stdin.read())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_speak_stop.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/speak.py tests/test_speak_stop.py
git commit -m "$(cat <<'EOF'
feat(speak): hook entrypoint with Stop handler (mode A end-to-end)

Reads hook event JSON on stdin, dispatches by event name. Stop handler
runs the full strip → voicify → synthesize → enqueue pipeline and
records the spoken offset for the message id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Hook handlers for B/C/operational events

**Files:**
- Modify: `scripts/speak.py` (add handlers for `PreToolUse`, `PostToolUse`, `Notification`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`)
- Create: `tests/test_speak_other_events.py`

- [ ] **Step 1: Write the failing test**

`tests/test_speak_other_events.py`:
```python
import json
from pathlib import Path

import pytest


def _write_jsonl(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


async def _async_return(v):
    return v


# --- UserPromptSubmit ---

def test_userpromptsubmit_clears_queue_and_kills_pid(voice_home, plugin_root,
                                                     write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, playback, state as state_mod

    s = state_mod.load("S")
    s.queue = ["/tmp/x.mp3"]
    s.current_pid = 999
    state_mod.save(s)

    killed = []
    monkeypatch.setattr(playback, "clear_and_kill", lambda sid: killed.append(sid))

    payload = {"session_id": "S", "hook_event_name": "UserPromptSubmit"}
    speak.run(json.dumps(payload))
    assert killed == ["S"]


# --- SessionStart / SessionEnd ---

def test_sessionstart_cleans_stale(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, state as state_mod

    called = []
    monkeypatch.setattr(state_mod, "clean_stale", lambda max_age_seconds: called.append(max_age_seconds))
    speak.run(json.dumps({"session_id": "X", "hook_event_name": "SessionStart"}))
    assert called == [24 * 3600]


def test_sessionend_removes_state_and_tmp(voice_home, plugin_root, write_config):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, state as state_mod

    s = state_mod.load("Z")
    state_mod.save(s)
    assert (voice_home / "state" / "Z.json").exists()

    junk = voice_home / "tmp" / "leftover.mp3"
    junk.write_bytes(b"audio")

    speak.run(json.dumps({"session_id": "Z", "hook_event_name": "SessionEnd"}))
    assert not (voice_home / "state" / "Z.json").exists()
    assert not junk.exists()


# --- Notification (mode C) ---

def test_notification_speaks_message_in_mode_C(voice_home, plugin_root,
                                                 write_config, monkeypatch):
    write_config({"enabled": True, "mode": "C"})
    from scripts import speak, extract, tts, playback

    monkeypatch.setattr(extract, "_voicify_async",
                        lambda text, model: _async_return("Heads up!"))
    fake = voice_home / "tmp" / "n.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "S", "hook_event_name": "Notification",
               "message": "Claude needs your attention to continue."}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", fake)]


def test_notification_skipped_in_mode_A(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "hook_event_name": "Notification",
                          "message": "x"}))


# --- Pre/PostToolUse (mode B) ---

def test_pretooluse_speaks_text_since_last_offset_in_mode_B(voice_home, plugin_root,
                                                              write_config, monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "B"})
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Looking at the file now to find the bug."},
            {"type": "tool_use", "name": "Read"},
        ]}},
    ])

    from scripts import speak, extract, tts, playback, state as state_mod

    captured = {}
    async def fake_voicify(text, model):
        captured["text"] = text
        return "voiced"
    monkeypatch.setattr(extract, "_voicify_async", fake_voicify)
    fake = voice_home / "tmp" / "p.mp3"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"x")
    monkeypatch.setattr(tts, "synthesize", lambda text: fake)
    enqueued = []
    monkeypatch.setattr(playback, "enqueue", lambda s, p: enqueued.append((s, p)))

    payload = {"session_id": "S", "transcript_path": str(transcript),
               "hook_event_name": "PreToolUse"}
    speak.run(json.dumps(payload))
    assert enqueued == [("S", fake)]
    assert "Looking at the file" in captured["text"]

    # Offset should now be at end of that text.
    s = state_mod.load("S")
    assert s.spoken_offsets["m1"] == len("Looking at the file now to find the bug.")


def test_pretooluse_skipped_in_mode_A(voice_home, plugin_root, write_config, monkeypatch):
    write_config({"enabled": True, "mode": "A"})
    from scripts import speak, tts

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "transcript_path": "/x",
                          "hook_event_name": "PreToolUse"}))


def test_pretooluse_skips_when_no_new_text(voice_home, plugin_root, write_config,
                                             monkeypatch, tmp_path):
    write_config({"enabled": True, "mode": "B"})
    from scripts import speak, tts, state as state_mod

    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "text", "text": "Already spoken."},
            {"type": "tool_use", "name": "Read"},
        ]}},
    ])

    s = state_mod.load("S")
    s.spoken_offsets["m1"] = len("Already spoken.")
    state_mod.save(s)

    monkeypatch.setattr(tts, "synthesize", lambda text: pytest.fail("should not synth"))
    speak.run(json.dumps({"session_id": "S", "transcript_path": str(transcript),
                          "hook_event_name": "PreToolUse"}))
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_speak_other_events.py -v
```

Expected: FAIL — handlers don't exist.

- [ ] **Step 3: Extend `scripts/speak.py`**

Replace the `_DISPATCH` block and add new handlers. The full updated dispatch section (replacing the old `_DISPATCH = {...}` and adding handlers above it):

```python
def _handle_user_prompt_submit(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    playback.clear_and_kill(session_id)


def _handle_session_start(payload: dict) -> None:
    state_mod.clean_stale(max_age_seconds=24 * 3600)


def _handle_session_end(payload: dict) -> None:
    session_id = payload.get("session_id") or "default"
    # Drop this session's state file.
    state_mod.remove(session_id)
    # Sweep tmp audio. (Cheap; tmp is small.)
    from scripts.log import voice_home
    tmp = voice_home() / "tmp"
    if tmp.exists():
        for f in tmp.iterdir():
            try:
                f.unlink()
            except Exception:
                pass


def _handle_notification(payload: dict) -> None:
    cfg = load_config()
    if cfg.mode != "C":
        return
    msg = payload.get("message") or ""
    if not msg.strip():
        return
    voiced = extract.voicify(
        msg,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        return
    audio = tts.synthesize(voiced)
    if audio is None:
        return
    session_id = payload.get("session_id") or "default"
    playback.enqueue(session_id, audio)


def _handle_pre_or_post_tool(payload: dict) -> None:
    cfg = load_config()
    if cfg.mode != "B":
        return
    transcript = Path(payload.get("transcript_path") or "")
    session_id = payload.get("session_id") or "default"

    res = last_assistant_text(transcript)
    if res is None:
        return
    msg_id, _full_text = res

    s = state_mod.load(session_id)
    offset = s.spoken_offsets.get(msg_id, 0)

    from scripts.transcript import current_assistant_text_after
    new_text = current_assistant_text_after(transcript, msg_id, offset)
    if not new_text:
        return

    stripped = extract.strip_for_voice(new_text, min_words=cfg.min_words)
    if not stripped:
        return

    voiced = extract.voicify(
        stripped,
        model=cfg.haiku_model,
        max_chars=cfg.max_haiku_chars,
        rewrite_enabled=cfg.rewrite,
    )
    if not voiced:
        return

    audio = tts.synthesize(voiced)
    if audio is None:
        return

    playback.enqueue(session_id, audio)
    # Update spoken offset to end of full text.
    s = state_mod.load(session_id)
    res2 = last_assistant_text(transcript)
    if res2 is not None and res2[0] == msg_id:
        s.spoken_offsets[msg_id] = len(res2[1])
        state_mod.save(s)


_DISPATCH = {
    "Stop": _handle_stop,
    "PreToolUse": _handle_pre_or_post_tool,
    "PostToolUse": _handle_pre_or_post_tool,
    "Notification": _handle_notification,
    "UserPromptSubmit": _handle_user_prompt_submit,
    "SessionStart": _handle_session_start,
    "SessionEnd": _handle_session_end,
}
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: every test passes (~30 tests across all files).

- [ ] **Step 5: Commit**

```bash
git add scripts/speak.py tests/test_speak_other_events.py
git commit -m "$(cat <<'EOF'
feat(speak): handlers for B/C and operational events

PreToolUse/PostToolUse (mode B) speak prose appearing since the last
spoken offset in the current message. Notification (mode C) speaks the
hook's message. UserPromptSubmit clears+kills any audio. SessionStart
cleans stale state files; SessionEnd removes this session's state and
sweeps tmp audio.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Hook registration generator

**Files:**
- Create: `scripts/hooks_gen.py`
- Create: `tests/test_hooks_gen.py`

This module produces the `hooks/hooks.json` file from a config (mode + plugin root). The setup skill calls this when the user changes mode.

- [ ] **Step 1: Write the failing test**

`tests/test_hooks_gen.py`:
```python
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


def test_mode_b_adds_pre_post_tool(plugin_root):
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="B")
    assert "PreToolUse" in out["hooks"]
    assert "PostToolUse" in out["hooks"]
    assert "Notification" not in out["hooks"]


def test_mode_c_adds_notification(plugin_root):
    from scripts import hooks_gen

    out = hooks_gen.generate(mode="C")
    assert "Notification" in out["hooks"]
    assert "PreToolUse" not in out["hooks"]


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
    assert "PreToolUse" in parsed["hooks"]
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_hooks_gen.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `scripts/hooks_gen.py`**

```python
"""Generate hooks/hooks.json from the current mode."""
from __future__ import annotations
import json
from pathlib import Path

OPERATIONAL_EVENTS = ("UserPromptSubmit", "SessionStart", "SessionEnd")
MODE_EVENTS = {
    "A": ("Stop",),
    "B": ("Stop", "PreToolUse", "PostToolUse"),
    "C": ("Stop", "Notification"),
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
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_hooks_gen.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add scripts/hooks_gen.py tests/test_hooks_gen.py
git commit -m "$(cat <<'EOF'
feat(hooks): generator for hooks/hooks.json from mode

Mode A registers only Stop. Mode B adds Pre/PostToolUse. Mode C adds
Notification. UserPromptSubmit, SessionStart, and SessionEnd are
always registered as operational hooks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Plugin manifest and example env

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.env.example`

- [ ] **Step 1: Write `.claude-plugin/plugin.json`**

```json
{
  "name": "claude-voice",
  "version": "0.1.0",
  "description": "Give Claude a voice via Deepgram Aura-2 with macOS `say` fallback",
  "author": {
    "name": "Jason",
    "email": "jace.croucher@gmail.com"
  }
}
```

- [ ] **Step 2: Write `.env.example`**

```
# This file is a template only. Real secrets live at ~/.claude/voice/.env,
# never in the plugin repo. The setup skill writes that file for you.

DEEPGRAM_API_KEY=your-deepgram-key-here
```

- [ ] **Step 3: Generate an initial `hooks/hooks.json` for mode A**

```bash
cd /Users/jason/dev/claude-chat
python3 -c "from scripts.hooks_gen import write; from pathlib import Path; write(mode='A', out_path=Path('hooks/hooks.json'))"
cat hooks/hooks.json
```

Expected: a JSON object with `Stop`, `UserPromptSubmit`, `SessionStart`, `SessionEnd` entries.

- [ ] **Step 4: Commit**

```bash
git add .claude-plugin/plugin.json .env.example hooks/hooks.json
git commit -m "$(cat <<'EOF'
feat(plugin): manifest, env example, mode-A hooks

Plugin manifest declaring name/version/description. .env.example
points users at ~/.claude/voice/.env (never committed). Initial
hooks.json registers Stop + operational hooks for default mode A.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Setup skill

**Files:**
- Create: `skills/voice-setup/SKILL.md`

The skill is markdown that Claude reads and acts on when the user asks to set up voice. It does NOT contain executable code itself — it instructs Claude to run specific commands and prompts.

- [ ] **Step 1: Write `skills/voice-setup/SKILL.md`**

```markdown
---
name: voice-setup
description: Configure or change Claude Voice — install deps, set the Deepgram API key, pick a voice and a mode (A/B/C), regenerate hooks.json, smoke test.
triggers:
  - set up voice
  - configure voice
  - change voice
  - change voice mode
  - install voice plugin
  - voice setup
---

# Claude Voice — Setup Skill

You are walking the user through configuring (or reconfiguring) the Claude Voice plugin.
Be concise. One short prompt at a time. Use the Bash tool for commands and the Edit/Write
tools for files. Do NOT speak to them via the plugin during setup — that's the smoke test
at the end.

The plugin lives at `${CLAUDE_PLUGIN_ROOT}` (in this case `/Users/jason/dev/claude-chat`).
Per-user data and secrets live at `~/.claude/voice/`.

## Step 1 — Pre-flight

Run:

```
python3 --version
```

Confirm Python ≥ 3.10. If lower, stop and tell the user to install a newer Python.

Then:

```
python3 -c "import claude_agent_sdk" 2>/dev/null && echo OK || python3 -m pip install claude-agent-sdk
```

If it fails, fall back to `pip install claude-agent-sdk` and report the error if that also fails.

## Step 2 — Deepgram API key (optional)

Check whether `~/.claude/voice/.env` already contains `DEEPGRAM_API_KEY`. Use:

```
test -s ~/.claude/voice/.env && grep -q '^DEEPGRAM_API_KEY=' ~/.claude/voice/.env && echo HAVE || echo MISSING
```

If MISSING, ask the user:

> "Do you have a Deepgram API key? (Paste it now to enable Aura-2 voice quality, or
> say 'skip' to use the macOS `say` fallback only.)"

If they paste a key, write it to `~/.claude/voice/.env`:

```
mkdir -p ~/.claude/voice
printf 'DEEPGRAM_API_KEY=%s\n' "<KEY_FROM_USER>" >> ~/.claude/voice/.env
chmod 600 ~/.claude/voice/.env
```

If they skip, set `primary_tts: "say"` in the next step's config.

## Step 3 — Voice picker

Present these six curated Aura-2 voices with one-liners:

- `aura-2-thalia-en` — clear, confident, energetic American female (default)
- `aura-2-orion-en`  — approachable American male
- `aura-2-luna-en`   — friendly young-adult American female
- `aura-2-zeus-en`   — deep, trustworthy American male
- `aura-2-pandora-en` — smooth, calm British female
- `aura-2-asteria-en` — knowledgeable, energetic American female

Ask: "Which voice would you like? (Or paste any other Aura-2 model id, e.g.
`aura-2-callista-en`.)" Default to `aura-2-thalia-en`.

If the user wants to preview voices and Deepgram is configured, synthesize a 5-word
preview for each candidate by running:

```
python3 -c "
from scripts import tts
import os
os.environ.setdefault('CLAUDE_PLUGIN_ROOT', '/Users/jason/dev/claude-chat')
# Temporarily override config voice via env (requires test path)
# Or — simpler — just call _synthesize_deepgram directly:
from scripts.tts import _synthesize_deepgram
p = _synthesize_deepgram(text='Hi, this is the voice', voice='<voice-id>',
                         api_key=os.environ['DEEPGRAM_API_KEY'],
                         speech_rate=1.0, max_chars=2000)
import subprocess; subprocess.run(['afplay', str(p)])
"
```

(Substitute `<voice-id>` per candidate.)

## Step 4 — Mode picker

Ask: "Which mode?

- **A (default)** — Claude speaks only the final response of each turn.
- **B** — Live commentary: Claude speaks each prose chunk between tool calls plus the
  final summary.
- **C** — Final response plus distinct alerts when Claude needs your attention.

(A is the safe default. B is more 'alive' but can stack up. Try A first.)"

## Step 5 — Write config.json

Build the config object based on the user's picks and write to `${CLAUDE_PLUGIN_ROOT}/config.json`:

```json
{
  "enabled": true,
  "mode": "<A|B|C>",
  "voice": "<chosen-voice-id>",
  "primary_tts": "<deepgram or say>",
  "fallback_tts": "say",
  "rewrite": true,
  "speech_rate": 1.0
}
```

## Step 6 — Regenerate hooks.json

Run:

```
cd /Users/jason/dev/claude-chat
python3 -c "from scripts.hooks_gen import write; from pathlib import Path; \
import json; cfg = json.load(open('config.json')); \
write(mode=cfg['mode'], out_path=Path('hooks/hooks.json'))"
```

Verify by `cat hooks/hooks.json` and confirming the expected events are present
for the chosen mode.

## Step 7 — Smoke test

Synthesize and play "Voice setup complete. I'm ready to talk." through the same
pipeline the hooks would use:

```
python3 -c "
import os, sys
os.environ.setdefault('CLAUDE_PLUGIN_ROOT', '/Users/jason/dev/claude-chat')
sys.path.insert(0, '/Users/jason/dev/claude-chat')
from scripts import tts
p = tts.synthesize(\"Voice setup complete. I'm ready to talk.\")
print('audio:', p)
import subprocess; subprocess.run(['afplay', str(p)])
"
```

Ask: "Did you hear that?" If they heard the wrong voice (e.g. `say` when they
expected Aura), check `~/.claude/voice/voice.log` for the fallback reason and
surface it.

## Step 8 — Reload notice

Tell the user:

> "Run `/reload-plugins` in this Claude Code session to pick up the new hook
> registration. After that, every turn from Claude will be spoken aloud."

## Re-running

If the user invokes this skill again, skip steps that are already done unless they
explicitly want to change them. Common shortcuts:

- "change voice" → jump to Step 3 + 5 + 6.
- "change mode" → jump to Step 4 + 5 + 6.
- "rotate key" → jump to Step 2 only.
- "mute voice" → set `enabled: false` in config.json (skip hooks regen).
```

- [ ] **Step 2: Commit**

```bash
git add skills/voice-setup/SKILL.md
git commit -m "$(cat <<'EOF'
feat(skill): voice-setup walks user through install + config

Triggered by phrases like 'set up voice' or 'change voice mode'. Steps:
pre-flight, Deepgram key, voice pick (with preview), mode pick, write
config, regen hooks, smoke test, reload notice.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Final verification — full test suite + manual smoke

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/jason/dev/claude-chat
python3 -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Manual install + smoke test**

In a Claude Code session in this directory:

```
/plugin install /Users/jason/dev/claude-chat
/reload-plugins
```

Then: `set up voice`. Walk through the setup skill. After completion, ask Claude
something simple like "what's 2+2?" and confirm you hear a response. Then ask for
a small code change and confirm code is *not* spoken.

If anything fails, check `~/.claude/voice/voice.log`.

- [ ] **Step 3: Tag the release**

```bash
git tag v0.1.0
git log --oneline -20
```

The tag is local-only; push later when the user is ready.

- [ ] **Step 4: Final commit if anything was tweaked during smoke test**

If the smoke test exposed any issues that needed fixes (regex that ate a fixture,
config default that surprised), commit those fixes with a `fix:` prefix and re-run
tests.

---

## Self-review notes

After writing this plan, I checked it against the spec:

- **Spec coverage:**
  - Plugin shape & file layout → Tasks 1, 14, 15
  - Modes A/B/C → Task 13 (hooks_gen) + Task 11/12 (handlers)
  - Hook events Stop/PreToolUse/PostToolUse/Notification/UserPromptSubmit/SessionStart/SessionEnd → Tasks 11, 12, 13
  - Heuristic strip → Task 5
  - Haiku rewrite via Claude Agent SDK → Task 6
  - Deepgram TTS with `Authorization: Token` header and 2000-char limit → Task 7
  - macOS `say` fallback with voice mapping → Task 8
  - Audio queue + UserPromptSubmit kill → Task 9, 12
  - Per-session state with offsets and pid → Task 4
  - Config at plugin root + `.env` at `~/.claude/voice/` → Tasks 3, 7
  - Setup skill (7-ish step flow) → Task 15
  - Edge cases (silent skip on every failure) → covered in tts.py, extract.py, speak.py
  - Testing approach (TDD on extract + tts + speak + playback) → Tasks 5, 6, 7, 8, 9, 10, 11, 12, 13

- **Placeholder scan:** No "TBD" / "implement later" / handwave in the plan. Each step has either exact code or an exact command.

- **Type consistency:** `Config`, `SessionState`, `last_assistant_text`, `current_assistant_text_after`, `strip_for_voice`, `voicify`, `_synthesize_deepgram`, `_synthesize_say`, `synthesize`, `enqueue`, `clear_and_kill`, `player_loop`, `generate`, `write` — all referenced consistently across tasks. `_voicify_async` patched in tests matches the signature on the implementation.

- **Scope:** focused on a single plugin; all tasks contribute to one shippable v0.1.0. No subsystem decomposition needed.

One thing intentionally deferred (call out for the implementer): the Claude Agent SDK API may differ slightly from the patterns shown above (`ClaudeSDKClient`, `ClaudeAgentOptions`, `client.receive_response()`). If the installed SDK version uses different attribute names, adjust `_voicify_async` accordingly — the tests mock at `extract._voicify_async`, so the test surface stays the same.
