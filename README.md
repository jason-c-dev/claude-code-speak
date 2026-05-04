# Claude Speech

[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#requirements)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-7c4dff.svg)](https://claude.com/claude-code)
[![TTS](https://img.shields.io/badge/TTS-Deepgram%20%7C%20Piper%20%7C%20say-2ea44f.svg)](#voices)

A Claude Code plugin that gives Claude a spoken voice. The natural-language parts of Claude's responses are spoken aloud through one of three TTS backends — Deepgram Aura-2 (cloud), Piper (local neural), or macOS `say` (local classic) — with automatic fallback if the primary fails. Code, tool calls, file paths, and other non-prose are filtered out.

## What it does

- Hooks into Claude Code's `Stop` event (always), `PreToolUse` in Mode B for short tool cues, and `Notification` in Mode C, to extract or generate the speakable text for each event.
- Strips code blocks, file paths, URLs, and markdown formatting so only natural sentences remain.
- Optionally runs the result through Claude Haiku 4.5 to lightly polish phrasing (off by default — see `config.rewrite`).
- Sends the polished text to Deepgram Aura-2 for high-quality TTS, with `say` as a local fallback if Deepgram is unconfigured or unreachable.
- Streams Deepgram's audio bytes directly into `ffplay`'s stdin so the first sentence starts playing within ~1s instead of waiting for the full mp3. Long replies split into sentence-sized chunks; consecutive chunks share one `ffplay` so audio plays gaplessly across chunk boundaries.
- Falls back to file-based `afplay` playback if `ffplay` is not installed.
- Audio from a prior turn is killed the moment you send a new prompt.

## Modes

| Mode | When Claude speaks |
|---|---|
| **A** (default) | Final response only — one clip per turn. Fully hook-driven and deterministic. |
| **B** | Same as A, plus automatic short cues ("Looking that up", "Pulling it up") before tool calls via a hook-driven PreToolUse event. Both end-of-turn and pre-tool speech are fully deterministic and hook-driven. `speak_cli` is reserved for between-tool interjections only. |
| **C** | Final response plus distinct alerts when Claude hits a `Notification` event (asks for permission, signals a blocker, etc.). |

Mode is set via `config.json`. The setup skill regenerates `hooks/hooks.json` so only the hooks for the current mode are registered. In mode B, a `SessionStart` hook injects instructions forbidding Claude from narrating tool calls (the PreToolUse hook handles this deterministically) and reserving `speak_cli` for optional interjections between tool calls.

## Requirements

- macOS (the `say` fallback is Darwin-only)
- Python 3.10+
- [Claude Code](https://claude.com/claude-code)
- Optional but recommended: a [Deepgram](https://deepgram.com) API key (free tier works; Aura-2 voices are well above `say` quality)
- Optional but recommended: `ffplay` from `ffmpeg` (`brew install ffmpeg`). With `ffplay`, audio streams directly from Deepgram for ~1s time-to-first-audio. Without it, the plugin falls back to writing temp mp3s and playing with `afplay`.
- Optional: [Piper](https://github.com/rhasspy/piper) for local neural TTS that beats `say` quality without needing a network. See the [Voices](#voices) section below.

## Install

Inside a Claude Code session:

```
/plugin marketplace add jason-c-dev/claude-code-speak
/plugin install claude-speech@claude-code-speak
/reload-plugins
```

Or from a local clone:

```bash
git clone https://github.com/jason-c-dev/claude-code-speak ~/dev/claude-code-speak
```
```
/plugin marketplace add ~/dev/claude-code-speak
/plugin install claude-speech@claude-code-speak
/reload-plugins
```

Then ask Claude to set it up:

```
> set up speech
```

The setup skill walks you through installing the Python dependency (`claude-agent-sdk`), entering your Deepgram key (or skipping for `say`-only mode), picking a voice, picking a mode, and running a smoke test. About a minute, end to end.

## Configuration

Plugin-level config lives in `config.json` at the repo root. The setup skill regenerates this — you shouldn't normally edit it by hand. Example:

```json
{
  "enabled": true,
  "mode": "A",
  "voice": "aura-2-thalia-en",
  "primary_tts": "deepgram",
  "fallback_tts": "say",
  "piper_voice": "",
  "say_voice_map": {},
  "rewrite": false,
  "speech_rate": 1.0
}
```

`primary_tts` and `fallback_tts` each accept `"deepgram"`, `"piper"`, or `"say"`. The plugin tries primary first; if that backend's prerequisites are missing or it returns no audio, it falls through to fallback. See [Voices](#voices) for backend details.

Your Deepgram key lives **outside the repo** at `~/.claude/voice/.env`:

```
DEEPGRAM_API_KEY=...
```

This survives plugin reinstall and never lands in version control.

To change voice or mode later, just say `change voice` or `change voice mode` and re-run the setup flow.

To temporarily mute Claude:

```bash
echo '{"enabled": false}' | jq -s '.[0] * .[1]' config.json - > /tmp/c.json && mv /tmp/c.json config.json
```

(Or simply uninstall the plugin via `/plugin uninstall`.)

## Voices

Three TTS backends are supported. Mix-and-match via `primary_tts` / `fallback_tts` in `config.json`.

### Deepgram (cloud, default primary)

Aura-2 neural voices. Pick one via `voice` in config; the setup skill knows the curated set:

| Voice id | Description |
|---|---|
| `aura-2-thalia-en` (default) | Clear, confident, energetic American female |
| `aura-2-orion-en` | Approachable American male |
| `aura-2-luna-en` | Friendly young-adult American female |
| `aura-2-asteria-en` | Knowledgeable, energetic American female |
| `aura-2-zeus-en` | Deep, trustworthy American male |
| `aura-2-pandora-en` | Smooth, calm British female |

Any other Aura-2 model id (e.g. `aura-2-callista-en`) also works — see [Deepgram's docs](https://developers.deepgram.com/docs/tts-models) for the full list. Streaming via `ffplay` keeps time-to-first-audio around 1s.

### Piper (local neural)

[Piper](https://github.com/rhasspy/piper) runs CPU-only with Apple-silicon-fast latency (~100ms). Quality is comparable to entry-tier Deepgram and fully local. To enable:

```bash
# Install the Piper CLI. pipx is recommended; pip works too.
pipx install piper-tts          # `brew install pipx` first if you don't have pipx

# Download a voice. Pick any from https://huggingface.co/rhasspy/piper-voices
mkdir -p ~/piper-voices && cd ~/piper-voices
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
```

Then in `config.json`:

```json
{
  "primary_tts": "piper",
  "fallback_tts": "say",
  "piper_voice": "~/piper-voices/en_US-amy-medium.onnx"
}
```

Each `.onnx` file ships with a sibling `.onnx.json`; both must live in the same directory. Piper voices range from ~20MB (low) to ~75MB (high) — `medium` is usually the sweet spot.

### macOS `say` (local classic)

Always available without configuration. The `voice` field is a Deepgram id, so when `say` is in use the plugin maps Deepgram → say voice via a lookup table. Defaults:

| Deepgram voice | `say` voice |
|---|---|
| `aura-2-thalia-en` / `-luna-en` / `-asteria-en` | `Samantha` (en_US) |
| `aura-2-orion-en` | `Alex` (en_US) |
| `aura-2-zeus-en` | `Daniel` (en_GB) |
| `aura-2-pandora-en` | `Karen` (en_AU) |

Override the mapping in `config.json` to use any system voice (`say -v '?'` lists every voice on your machine):

```json
{
  "primary_tts": "say",
  "say_voice_map": {
    "aura-2-thalia-en": "Reed (English (US))"
  }
}
```

The newer macOS voices — **Reed, Sandy, Flo, Eddy, Grandma, Grandpa, Rocko, Shelley** — sound noticeably better than legacy ones. They use Apple's newer neural engine. Some come pre-installed on macOS 14+; others can be added via System Settings → Accessibility → Spoken Content → System Voice → Manage Voices.

## How "vocal intent" is decided

You don't write speech tags. The plugin extracts speech in three steps:

1. **Strip** — drop code blocks, inline code, file paths, URLs, markdown, emoji.
2. **Rewrite** (optional, off by default) — if `config.rewrite` is `true`, the stripped text is passed through Claude Haiku 4.5 with a prompt that says: "rewrite as one or two natural spoken sentences, or return empty if there's nothing worth saying aloud." This step requires a Claude Code Max plan (or API auth) to call Haiku; with rewrite off, the stripped text is spoken directly.

   **Tradeoff:** rewrite produces nicer, more conversational summaries — but each turn pays a Haiku round-trip (typically 1–3s) before the first audio plays. The default (off) keeps end-of-turn audio near-instant; flip it on if you'd rather have polished phrasing than low latency. Mode B's pre-tool cues are unaffected — they always come from the static lookup table, no rewrite.
3. **Synthesize** — Aura-2 turns it into audio.

When rewrite is enabled, Haiku returning the empty string is a valid signal — Claude says nothing aloud for that turn. Pure tool-use turns naturally produce nothing speakable and stay silent.

## Failure behavior

Every failure mode is "log and silent skip". The plugin will not block your session, throw errors back into Claude Code, or partially break a turn. Worst case: Claude doesn't speak. Logs go to `~/.claude/voice/voice.log` for diagnosis.

Common fallbacks:

| Situation | What happens |
|---|---|
| `ffplay` not installed | Use file-based playback (Deepgram → temp mp3 → `afplay`) |
| Deepgram 5xx, timeout, or mid-stream error | Fall through to file-based playback, then to the configured fallback backend (`piper` or `say`) |
| No Deepgram key configured | Skip Deepgram, speak via the fallback backend |
| Piper binary or voice model missing | Skip Piper, fall through to `say` |
| Haiku auth fails (when rewrite is enabled) | Speak the heuristic-stripped text raw |
| Stripped text is empty | Stay silent for this turn |
| Audio device unavailable | Log and stay silent |

## Filesystem layout

```
~/.claude/voice/
├── .env                       # DEEPGRAM_API_KEY
├── voice.log                  # diagnostic log
├── state/<session_id>.json    # per-session state (offsets, current pid)
└── tmp/<uuid>.{mp3,aiff}      # transient audio, deleted after playback
```

The plugin repo itself contains code, hooks, the setup skill, and example config. No user data lives in the repo.

The user-data directory stays at `~/.claude/voice/` even though the plugin renamed from `claude-voice` to `claude-speech`. Keeping the path stable means existing setups (your Deepgram key, voice log, session state) survive the rename without migration.

## Privacy

- Stripped + rewritten text is sent to Deepgram (TTS) and to Anthropic (Haiku rewrite via the Claude Agent SDK, billed against your Max plan).
- The full transcript is **not** sent to either — the plugin only reads what's needed for the current event.
- Audio files are local. They're deleted after playback and on session end.

## Slash commands

| Command | What it does |
|---|---|
| `/speech-mute` | Toggle the plugin on/off. When muting, also kills any in-flight audio so you get immediate silence. |
| `/speech-stop` | Stop only the audio currently playing/queued for this session, without disabling the plugin. |

Named `speech-*` rather than `voice-*` to avoid clashing with Anthropic's built-in `/voice` (speech-to-text dictation).

## Roadmap (post-v1)

- Overlap synthesis: kick off the next chunk's Deepgram request before the current chunk finishes streaming, so its bytes are ready the moment the previous audio drains.
- Piper streaming via `--output_raw` piped into `ffplay` (currently file-based).
- Linux/Windows fallback (replace `say` with `espeak-ng`).
- Cost telemetry surfaced through the setup skill.

## License

MIT. See [LICENSE](LICENSE) for the full text.
