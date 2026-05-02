# TODO

## Revisit voicify (Haiku rewrite) prompt design

**Status:** Disabled by default in `config.json` (`rewrite: false`).

**Why disabled:** Empirical testing on 2026-05-02 showed Haiku regularly went off-prompt — instead of lightly polishing input text, it would invent test announcements, summaries, or generic responses. Example: a 812-char status report came out as "this is a test message if you're hearing my voice right now the fix is working." With `rewrite: false`, the regex-stripped text goes straight to Deepgram and matches the assistant's actual response verbatim, which is what users want.

**What to revisit:**
- The voicify system prompt is too permissive ("Lightly smooth phrasing"). Haiku interprets "smooth" as "rewrite freely."
- Try: very strict prompt that forbids inventing content (e.g., "Return EXACTLY the input text. Only fix: word that look like code identifiers, missing punctuation, or unspeakable acronyms. NEVER invent or summarize."), plus a similarity check (Levenshtein or token-overlap) that falls back to original if the rewrite diverges too far.
- Or replace Haiku with a much smaller dedicated model fine-tuned for TTS-prep, if such a thing exists.

**Code pointer:** `scripts/extract.py` — `VOICIFY_SYSTEM_PROMPT` and `_voicify_async`.

**Acceptance:** When re-enabled, regression test: feed it the conversation transcript that triggered the "test message" hallucination and confirm Haiku now outputs a faithful polish, not a confabulation.
