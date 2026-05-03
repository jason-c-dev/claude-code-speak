# Claude Voice

A Claude Code plugin that gives Claude a spoken voice. The natural-language parts of Claude's responses are spoken aloud through Deepgram Aura-2 (with macOS `say` as a local fallback). Code, tool calls, file paths, and other non-prose are filtered out.

## What it does

- Hooks into Claude Code's `Stop` event (and optionally `Notification`) to extract the speakable prose from each turn.
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
- [Claude Code](https://claude.com/claude-code) with a Max plan (only needed if you re-enable the Haiku rewrite step; off by default)
- Optional but recommended: a [Deepgram](https://deepgram.com) API key (free tier works; Aura-2 voices are well above `say` quality)
- Optional but recommended: `ffplay` from `ffmpeg` (`brew install ffmpeg`). With `ffplay`, audio streams directly from Deepgram for ~1s time-to-first-audio. Without it, the plugin falls back to writing temp mp3s and playing with `afplay`.

## Install

Inside a Claude Code session:

```
/plugin marketplace add jason-c-dev/claude-code-speak
/plugin install claude-voice@claude-code-speak
/reload-plugins
```

Or from a local clone:

```bash
git clone https://github.com/jason-c-dev/claude-code-speak ~/dev/claude-code-speak
```
```
/plugin marketplace add ~/dev/claude-code-speak
/plugin install claude-voice@claude-code-speak
/reload-plugins
```

Then ask Claude to set it up:

```
> set up voice
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
  "rewrite": true,
  "speech_rate": 1.0
}
```

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

## How "vocal intent" is decided

You don't write speech tags. The plugin extracts speech in three steps:

1. **Strip** — drop code blocks, inline code, file paths, URLs, markdown, emoji.
2. **Rewrite** — pass the stripped text through Claude Haiku 4.5 with a prompt that says: "rewrite as one or two natural spoken sentences, or return empty if there's nothing worth saying aloud."
3. **Synthesize** — Aura-2 turns it into audio.

Haiku returning the empty string is a valid signal — Claude says nothing aloud for that turn. Pure tool-use turns naturally produce nothing speakable and stay silent.

## Failure behavior

Every failure mode is "log and silent skip". The plugin will not block your session, throw errors back into Claude Code, or partially break a turn. Worst case: Claude doesn't speak. Logs go to `~/.claude/voice/voice.log` for diagnosis.

Common fallbacks:

| Situation | What happens |
|---|---|
| `ffplay` not installed | Use file-based playback (Deepgram → temp mp3 → `afplay`) |
| Deepgram 5xx, timeout, or mid-stream error | Fall through to file-based playback, then to `say` if needed |
| No Deepgram key configured | Speak via `say` instead |
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

The plugin repo itself contains code, hooks, the setup skill, the spec, and example config. No user data lives in the repo.

## Privacy

- Stripped + rewritten text is sent to Deepgram (TTS) and to Anthropic (Haiku rewrite via the Claude Agent SDK, billed against your Max plan).
- The full transcript is **not** sent to either — the plugin only reads what's needed for the current event.
- Audio files are local. They're deleted after playback and on session end.

## Roadmap (post-v1)

- Overlap synthesis: kick off the next chunk's Deepgram request before the current chunk finishes streaming, so its bytes are ready the moment the previous audio drains.
- Linux/Windows fallback (replace `say` with `espeak-ng` or Piper).
- A `/voice mute` slash command for quick toggling.
- Cost telemetry surfaced through the setup skill.

## License

TBD — pre-release.
