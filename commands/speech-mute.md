---
description: Toggle Claude Speech on or off (and stop any audio currently playing).
---

Toggle the plugin's `enabled` flag in `config.json`. If muting, also stop any
in-flight audio for the active session so the user gets immediate silence,
not just "no future speech."

Run this Bash command and report the result back to the user:

```bash
python3 -c "
import json, sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}')
cfg_path = Path('${CLAUDE_PLUGIN_ROOT}/config.json')
cfg = json.loads(cfg_path.read_text())
was_enabled = bool(cfg.get('enabled', True))
cfg['enabled'] = not was_enabled
cfg_path.write_text(json.dumps(cfg, indent=2) + '\n')

if was_enabled:
    # Muting — also kill any audio currently playing.
    from scripts.playback import clear_and_kill
    from scripts.state import state_dir
    for f in state_dir().glob('*.json'):
        try:
            clear_and_kill(f.stem)
        except Exception:
            pass
    print('Voice muted.')
else:
    print('Voice unmuted.')
"
```

Reply briefly to the user with whatever was printed (one short line — "Voice
muted." or "Voice unmuted."). Do not add commentary.
