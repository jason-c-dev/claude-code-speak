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

# 8. Speech normalization: convert symbols/units to spoken words BEFORE
#    the emoji strip eats them. Order matters — more-specific patterns first.
_SPEECH_NORMALIZE = [
    (re.compile(r"°\s*C\b"), " degrees Celsius"),
    (re.compile(r"°\s*F\b"), " degrees Fahrenheit"),
    (re.compile(r"°"), " degrees "),
    (re.compile(r"\bkm/h\b"), " kilometers per hour"),
    (re.compile(r"\bmph\b"), " miles per hour"),
    (re.compile(r"\bkm\b"), " kilometers"),
    (re.compile(r"\bcm\b"), " centimeters"),
    (re.compile(r"\bmm\b"), " millimeters"),
    (re.compile(r"~"), " roughly "),
    (re.compile(r"&"), " and "),
    (re.compile(r"%"), " percent"),
]

MIN_WORDS_DEFAULT = 3


# Sentence boundary: end-punctuation followed by whitespace (and ideally a
# capital, but we don't enforce that strictly — handles ellipses-before-cap too).
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_into_chunks(
    text: str,
    *,
    max_chars: int = 300,
    min_chars: int = 60,
) -> list[str]:
    """Split text into chunks suitable for streaming TTS playback.

    Each chunk is sized so the first one synthesizes quickly (low time-to-first-
    audio) while the rest queue up behind. Splits on sentence boundaries, then
    greedily packs sentences into chunks no larger than `max_chars`. Tiny
    trailing chunks (< `min_chars`) get merged back into the previous chunk so
    we don't end with a 5-character clip nobody will notice.

    Returns a list of non-empty strings. For very short input the list has
    exactly one element."""
    if not text or not text.strip():
        return []

    sentences = [s for s in _SENTENCE_BOUNDARY.split(text.strip()) if s]
    if not sentences:
        return [text.strip()]

    chunks: list[str] = []
    current = ""
    for s in sentences:
        if not current:
            current = s
        elif len(current) + 1 + len(s) <= max_chars:
            current = f"{current} {s}"
        else:
            chunks.append(current)
            current = s
    if current:
        chunks.append(current)

    # Merge tiny trailing chunk back so we don't end on a stub.
    if len(chunks) >= 2 and len(chunks[-1]) < min_chars:
        chunks[-2] = chunks[-2] + " " + chunks[-1]
        chunks.pop()

    return chunks


def _normalize_for_speech(s: str) -> str:
    """Replace common symbols/units with spoken equivalents (before emoji strip)."""
    for pattern, replacement in _SPEECH_NORMALIZE:
        s = pattern.sub(replacement, s)
    return s


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
    s = _normalize_for_speech(s)
    s = _strip_emoji(s)
    s = _WHITESPACE.sub(" ", s).strip()

    if not s:
        return ""
    if len(s.split()) < min_words:
        return ""
    return s


import asyncio

from scripts.log import get_logger

VOICIFY_SYSTEM_PROMPT = (
    "You are polishing text so it sounds natural when spoken aloud. "
    "Return one or two natural spoken sentences in the same first-person "
    "voice as the input. Lightly smooth phrasing, drop code-like fragments "
    "and technical references, but ALWAYS return real speakable English — "
    "even short greetings, acknowledgments, or single-sentence updates "
    "should be returned (lightly polished). Do NOT return the empty string "
    "unless the input is literally just punctuation or symbols. "
    "Return ONLY the rewritten text — no preface, no quotation marks, no commentary."
)


async def _voicify_async(text: str, model: str) -> str:
    # Imported lazily so unit tests can run without the SDK installed.
    import os
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions  # type: ignore

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=VOICIFY_SYSTEM_PROMPT,
        allowed_tools=[],
    )
    parts: list[str] = []
    # Mark this SDK invocation as internal so the child claude process's
    # hooks short-circuit. Without this, the rewrite subagent fires its
    # own UserPromptSubmit/Stop hooks and we end up speaking the rewrite
    # subagent's own intro lines via every parallel voicify call.
    prev = os.environ.get("CLAUDE_VOICE_INTERNAL")
    os.environ["CLAUDE_VOICE_INTERNAL"] = "1"
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(text)
            async for msg in client.receive_response():
                for block in getattr(msg, "content", []) or []:
                    t = getattr(block, "text", None)
                    if t:
                        parts.append(t)
    finally:
        if prev is None:
            os.environ.pop("CLAUDE_VOICE_INTERNAL", None)
        else:
            os.environ["CLAUDE_VOICE_INTERNAL"] = prev
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
    SDK call fails. Returns '' for empty input. Never raises.
    """
    if not text or not text.strip():
        return ""
    if not rewrite_enabled:
        return text
    if len(text) > max_chars:
        text = text[-max_chars:]
    try:
        rewritten = asyncio.run(_voicify_async(text, model))
    except Exception as e:  # SDK failure must never propagate
        get_logger().warning("voicify Haiku call failed: %s; using stripped text", e)
        return text
    # Safety net: if Haiku returns empty for non-trivial input, fall back to
    # the stripped text rather than silently losing speakable content. The
    # heuristic strip already filtered tiny/empty inputs upstream.
    if not rewritten and text.strip():
        get_logger().info("voicify returned empty for non-trivial input; using stripped text")
        return text
    return rewritten
