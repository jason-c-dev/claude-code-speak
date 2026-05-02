"""Tests for extract.split_into_chunks — sentence-level chunking for streaming TTS."""
from scripts.extract import split_into_chunks


def test_empty_returns_empty_list():
    assert split_into_chunks("") == []
    assert split_into_chunks("   ") == []


def test_short_single_sentence_returns_one_chunk():
    out = split_into_chunks("Hello there.")
    assert out == ["Hello there."]


def test_two_short_sentences_pack_into_one_chunk():
    out = split_into_chunks("First. Second.", max_chars=300)
    # Both fit comfortably in one chunk.
    assert out == ["First. Second."]


def test_long_text_splits_at_sentence_boundary():
    s1 = "This is the first sentence and it is moderately long."
    s2 = "Here is the second sentence which carries more words."
    s3 = "And finally a third concluding sentence."
    text = f"{s1} {s2} {s3}"
    out = split_into_chunks(text, max_chars=60, min_chars=20)
    assert len(out) >= 2
    # No chunk should exceed max_chars.
    assert all(len(c) <= 60 for c in out)
    # All sentences appear in the joined output.
    joined = " ".join(out)
    assert s1 in joined and s2 in joined and s3 in joined


def test_tiny_trailing_chunk_merges_back():
    # Last sentence is shorter than min_chars, so it should attach to prior.
    text = "First sentence has some words. Second is bigger and meaningful. Yes."
    out = split_into_chunks(text, max_chars=80, min_chars=30)
    # The "Yes." stub must not stand alone.
    assert all(len(c) >= 4 for c in out)
    assert "Yes" in out[-1]
    assert len(out[-1]) >= 30


def test_text_with_no_sentence_punct_returns_single_chunk():
    # No punctuation at all — caller might pass partial fragments.
    text = "this is a fragment without a period"
    out = split_into_chunks(text)
    assert out == [text]


def test_preserves_original_text_content():
    text = "Alpha. Beta! Gamma? Delta. Epsilon."
    out = split_into_chunks(text, max_chars=100)
    joined = " ".join(out)
    for word in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        assert word in joined


def test_questions_and_exclamations_are_boundaries_too():
    text = "What time is it? Now! Or never."
    out = split_into_chunks(text, max_chars=10, min_chars=5)
    # Should split on ? and ! as well as .
    assert len(out) >= 2
