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

MIN_WORDS_DEFAULT = 2


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
