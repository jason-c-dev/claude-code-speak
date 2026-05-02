---
description: Stop any audio Claude Voice is currently playing or has queued for this session.
---

Stop any in-flight Claude Voice audio for the current session. The plugin's
playback is per-session, so we need to clear the queue and SIGTERM the player
process (and its `afplay` child) for this Claude Code session id.

Run this Bash command and report the result back to the user:

```bash
SESSION_FILE=$(ls -t ~/.claude/voice/state/*.json 2>/dev/null | head -1)
if [ -z "$SESSION_FILE" ]; then
  echo "No active voice session found."
  exit 0
fi
python3 -c "
import sys
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}')
from scripts.playback import clear_and_kill
from pathlib import Path
import json
sid = json.load(open('${SESSION_FILE}'))['session_id']
clear_and_kill(sid)
print(f'Stopped voice for session {sid}.')
"
```

Reply briefly to the user with whatever was printed (e.g. "Stopped voice for
session abc123." or "No active voice session found."). Do not add commentary.
