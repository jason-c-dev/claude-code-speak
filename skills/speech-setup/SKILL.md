---
name: speech-setup
description: Configure or change Claude Speech — install deps, set the Deepgram API key, pick a voice and a mode (A/B/C), regenerate hooks.json, smoke test.
triggers:
  - set up speech
  - configure speech
  - change speech mode
  - install speech plugin
  - speech setup
  - change voice
  - set up voice
---

# Claude Speech — Setup Skill

You are walking the user through configuring (or reconfiguring) the Claude Speech plugin.
Be concise. One short prompt at a time. Use the Bash tool for commands and the Edit/Write
tools for files. Do NOT speak to them via the plugin during setup — that's the smoke test
at the end.

The plugin lives at `${CLAUDE_PLUGIN_ROOT}`. Per-user data and secrets live at `~/.claude/voice/`.

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
cd "${CLAUDE_PLUGIN_ROOT}" && python3 -c "
import os
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

- **A (default)** — Claude speaks only the final response of each turn (deterministic,
  hook-driven).
- **B** — Same as A, plus deterministic short cues before every tool call
  ('running this', 'reading', 'looking that up', etc.) via a hook-driven
  PreToolUse event. Both end-of-turn and pre-tool speech are fully
  hook-driven; nothing depends on Claude remembering to narrate.
- **C** — Final response plus distinct alerts when Claude needs your attention.

(A is the safe default — silent for tool work. B is more 'alive' with
audible cues for every tool call. Try A first if you're unsure.)"

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
cd "${CLAUDE_PLUGIN_ROOT}"
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
cd "${CLAUDE_PLUGIN_ROOT}" && python3 -c "
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
