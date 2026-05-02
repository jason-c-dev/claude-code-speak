# Claude Voice — Design Spec

**Date:** 2026-05-02
**Status:** Draft for review
**Owner:** Jason

## Goal

Give Claude a voice. When Claude responds in Claude Code, the natural-language portions of the response are spoken aloud through a high-quality cloud TTS (Deepgram Aura-2), with a built-in macOS `say` fallback when Deepgram is unavailable. Code, tool calls, file paths, and other non-prose content are excluded from speech. The whole thing ships as a Claude Code plugin with a setup skill for first-run configuration.

## Non-goals

- Speech-to-text (microphone input). Out of scope; this is one-way TTS.
- Real-time streaming TTS that begins playback before generation finishes. Batch synthesis per clip is sufficient for v1.
- Cross-platform fallback. macOS-only for v1 (the `say` fallback is Darwin-only).
- Voice cloning, custom voice training, or per-message voice switching.
- Anything beyond English (Aura-2 multilingual is a future possibility).

## High-level architecture

```
Claude Code session
        │
        │ hook event JSON via stdin
        ▼
  speak.py (entrypoint, dispatches by event type)
        │
        ├── reads transcript JSONL at $CLAUDE_TRANSCRIPT_PATH
        ├── extracts the relevant text for this event
        │
        ▼
  extract.strip_for_voice(text)         heuristic strip
        │
        ▼
  extract.voicify(stripped) via         Haiku rewrite
  Claude Agent SDK (Max plan auth)      one-shot, max_turns=1
        │
        ▼
  tts.synthesize(voiced) →
        Deepgram primary  -or- say fallback
        │
        ▼
  playback.enqueue(audio_path)          FIFO queue per session,
                                        UserPromptSubmit clears + kills
```

Each hook event is a fully isolated invocation of `speak.py`. State that needs to persist across events (the per-session "what's been spoken already" offset, the current `afplay` PID) lives in `~/.claude/voice/state/<session_id>.json`.

## Modes

The mode controls *which hooks are registered*. The dispatch logic in `speak.py` is the same for every mode — the harness decides when speech happens, not Claude.

| Mode | Mode-specific hooks | Behavior |
|---|---|---|
| A (default) | `Stop` | Speak the final prose of each turn. |
| B | `Stop`, `PreToolUse`, `PostToolUse` | Speak each chunk of natural prose between tool calls plus the final summary. State file tracks which message text has already been spoken so we never repeat. |
| C | `Stop`, `Notification` | Speak the final prose plus distinct audio for `Notification` events (when Claude needs attention or permission). |

`UserPromptSubmit`, `SessionStart`, and `SessionEnd` are always registered regardless of mode — they are operational, not mode-specific:

- `UserPromptSubmit` clears any audio still playing or queued from the prior turn (kills the recorded `afplay` PID).
- `SessionStart` cleans stale state files older than 24 hours.
- `SessionEnd` removes this session's state file and any unplayed tmp audio.

The setup skill regenerates `hooks/hooks.json` based on the mode in `config.json` so the mode-specific hook set is always consistent with the chosen mode. No "registered but disabled" hooks.

## Speech pipeline

### 1. Extract text for this event

| Hook event | Extracted text |
|---|---|
| `Stop` | All text content blocks of the last assistant message in the transcript. |
| `PreToolUse` | Assistant text content from the current message that occurs *before* the tool_use block, after the last byte offset already recorded in the session state file. |
| `PostToolUse` | Assistant text content from the current message that occurs *after* the most recent tool_result, after the last recorded offset. |
| `Notification` | The notification message field from the hook event JSON. |
| `UserPromptSubmit` | (no extraction — fires queue clear + afplay kill, then exits) |

### 2. Heuristic strip — `extract.strip_for_voice(text) -> str`

In order:

1. Remove fenced code blocks (` ```…``` `).
2. Remove inline code (`` `…` ``).
3. Remove file:line references (regex matching `\S+:\d+(:\d+)?`).
4. Remove bare URLs (regex matching `https?://\S+`).
5. Flatten markdown emphasis (`**…**`, `*…*`, `_…_`) to plain text.
6. Drop lines that are only headers (`#…`), bullet markers (`-`, `*`, `>`) with nothing else, or horizontal rules.
7. Strip emoji (Unicode property `Emoji`).
8. Collapse whitespace.

If the resulting text has fewer than 3 words, return an empty string and short-circuit the rest of the pipeline.

### 3. Haiku rewrite — `extract.voicify(stripped) -> str`

Uses the Claude Agent SDK in headless one-shot mode:

```python
options = ClaudeAgentOptions(
    model="claude-haiku-4-5-20251001",
    system_prompt=(
        "Rewrite the input as one or two natural spoken sentences in the same "
        "first-person voice. Skip any technical references that don't sound "
        "natural aloud. If there is nothing worth saying aloud, return the "
        "empty string."
    ),
    max_turns=1,
)
```

Auths via the user's Claude Code OAuth credentials, so usage rolls into the Max plan with no separate `ANTHROPIC_API_KEY`.

If the SDK call raises (auth failure, network error), fall back to speaking `stripped` directly without rewrite. Log the fallback.

If the rewrite returns the empty string, skip TTS — Haiku is signalling "nothing worth saying".

Inputs over 4000 characters are truncated from the start (keep most recent prose) before being sent to Haiku.

### 4. TTS — `tts.synthesize(voiced) -> Path`

Fallback chain:

1. **Deepgram Aura-2** (primary). POST to `https://api.deepgram.com/v1/speak?model=<voice>&encoding=mp3` with `Authorization: Token <DEEPGRAM_API_KEY>`. Body: `{"text": voiced}`. Response body bytes are written to `~/.claude/voice/tmp/<uuid>.mp3`.
2. **macOS `say`** (fallback). If Deepgram is unconfigured, returns 4xx/5xx, or times out, run `say -v <mapped_voice> -o <path>.aiff <voiced>`. The voice mapping (e.g. `aura-2-thalia-en` → `Samantha`) lives in config.
3. **Silent skip** (last resort). If `say` is unavailable or fails (which would mean a broken macOS audio system), log and exit 0.

Every fallback transition is logged to `~/.claude/voice/voice.log` for diagnostic surfacing in the setup skill.

### 5. Playback — `playback.enqueue(audio_path)`

FIFO queue per session. On enqueue:

- If no audio currently playing, fork an `afplay <path>` subprocess and record its PID in the session state file.
- Otherwise, append to the queue file. A short watchdog (a separate `afplay` orchestrator script started lazily) checks for the next item when the current playback ends.

`UserPromptSubmit` hook fires `kill <stored_pid>` and truncates the queue file. Audio from the prior turn never bleeds into the next turn.

Played audio files are deleted from `~/.claude/voice/tmp/` after playback. The directory is cleaned on `SessionEnd`.

## Configuration

### `${CLAUDE_PLUGIN_ROOT}/config.json`

```json
{
  "enabled": true,
  "mode": "A",
  "voice": "aura-2-thalia-en",
  "primary_tts": "deepgram",
  "fallback_tts": "say",
  "say_voice_map": {
    "aura-2-thalia-en": "Samantha",
    "aura-2-orion-en": "Alex"
  },
  "rewrite": true,
  "haiku_model": "claude-haiku-4-5-20251001",
  "min_words": 3,
  "max_haiku_chars": 4000
}
```

Lives in the plugin directory so it's version-controllable. Setup skill regenerates it on changes.

### `~/.claude/voice/.env`

```
DEEPGRAM_API_KEY=...
```

User-level, outside the plugin directory, so it survives plugin reinstall. The only secret. (No `ANTHROPIC_API_KEY` — the Agent SDK uses Claude Code's OAuth.)

If the file is missing or the key is empty, `config.primary_tts` is auto-set to `"say"` by the setup skill so the plugin works out-of-the-box without a Deepgram account.

## Setup skill (`skills/voice-setup/SKILL.md`)

Triggers on phrases like "set up voice", "configure voice", "change voice mode", "install voice plugin". When invoked, the skill instructs Claude to walk the user through:

1. **Pre-flight** — verify Python 3.10+, ensure `claude-agent-sdk` is installed (try `uv pip install claude-agent-sdk`, fall back to `pip install`).
2. **Deepgram key (optional)** — check `~/.claude/voice/.env`. If missing, ask the user; if they decline, set `config.primary_tts = "say"` and continue.
3. **Voice picker** — list 6 curated Aura-2 voices with one-line descriptions. Optionally synthesize a 5-word preview ("Hi, this is the voice I'd use") for each candidate using the same pipeline.
4. **Mode picker** — A/B/C with the same descriptions used in this design.
5. **Regenerate `hooks/hooks.json`** based on the chosen mode.
6. **Smoke test** — speak "Voice setup complete. I'm ready to talk." Confirm the user heard it.
7. **Reload notice** — instruct the user to run `/reload-plugins` so the new hook set is picked up.

Re-running the skill at any time updates one or more of those steps (mode change, voice change, key rotation, key removal).

## Edge cases

| Edge case | Handling |
|---|---|
| Deepgram key missing or 401/403 | Fall back to `say`; log. |
| Deepgram 5xx, timeout, network error | Fall back to `say`; log. |
| `say` not on PATH or fails | Log; silent skip. |
| Claude Agent SDK auth fails | Speak the heuristic-stripped text directly; log. |
| Haiku rewrite returns empty | Skip TTS — correct signal. |
| Stripped text < 3 words | Skip — nothing worth saying. |
| Haiku input > 4000 chars | Truncate from start. |
| `afplay` not on PATH | Log; silent skip. |
| `config.enabled == false` | `speak.py` exits 0 immediately. |
| Mode B/C "already spoken" check | Session state file `~/.claude/voice/state/<session_id>.json` records byte offsets per assistant message id. |
| New user turn while audio queued or playing | `UserPromptSubmit` clears queue and kills the recorded `afplay` PID. |
| Mode change requires hook reload | Setup skill prints reminder to run `/reload-plugins`. |
| Session state file from old session | `SessionEnd` hook removes it; stale files older than 24h cleaned on `SessionStart`. |

**Failure philosophy:** every failure is "log and silent skip". `speak.py` always exits 0. Voice is a UX layer, never load-bearing on Claude Code's session.

## Testing

TDD on the extract pipeline since that is where the real logic lives.

- `tests/test_extract.py` — fixtures of representative responses (code-heavy, prose-heavy, all bullets, single sentence, empty, only emoji, file paths, URLs, mixed). Assert `strip_for_voice()` returns the expected prose. Mock `claude_agent_sdk` for `voicify()` happy path and auth-failure fallback.
- `tests/test_tts.py` — mock `urllib.request` for Deepgram. Assert primary success, 401 falls through to `say`, 5xx falls through to `say`, both fail = silent skip. Mock `subprocess` for the `say` invocation.
- `tests/test_speak.py` — fixture transcript JSONL files plus hook event JSON on stdin. Assert dispatch by event type and that `config.enabled = false` short-circuits.
- `tests/test_playback.py` — mock `subprocess` for `afplay`. Assert FIFO queue order, that `UserPromptSubmit` kills the PID and clears the queue file.

No CI integration tests against the live Deepgram or Anthropic APIs in v1. The setup skill's smoke-test step is the integration check.

## File layout

```
/Users/jason/dev/claude-chat/
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   └── hooks.json                   # regenerated by setup skill from mode
├── scripts/
│   ├── speak.py                     # hook entrypoint
│   ├── extract.py                   # strip + voicify
│   ├── tts.py                       # Deepgram + say fallback
│   └── playback.py                  # queue + afplay
├── skills/
│   └── voice-setup/
│       └── SKILL.md
├── tests/
│   ├── test_extract.py
│   ├── test_tts.py
│   ├── test_speak.py
│   └── test_playback.py
├── docs/superpowers/specs/
│   └── 2026-05-02-claude-voice-design.md
├── config.example.json
├── .env.example                     # references ~/.claude/voice/.env
└── README.md
```

User-level data (state, secrets, logs, tmp audio) lives outside the plugin at:

```
~/.claude/voice/
├── .env
├── voice.log
├── state/<session_id>.json
└── tmp/<uuid>.{mp3,aiff}
```

## Open items intentionally deferred (YAGNI)

- Streaming TTS playback (start audio before synthesis completes).
- Per-mode voice (e.g. one voice for prose, another for notifications).
- Linux/Windows fallback. (Ship macOS-only first.)
- Cost / usage telemetry beyond the log file.
- Configurable speech rate / pitch.
- Audio caching keyed by stripped text hash.
- Multi-session audio coordination (right now each session is independent).
