from app.services.deduplication.dedup import (
    exact_hash,
    hamming_distance,
    is_near_duplicate,
    normalized_hash,
    normalize_text,
    simhash64,
)


def test_normalize_text_collapses_whitespace_and_case():
    assert normalize_text("  Hello   World \n\n") == "hello world"


def test_exact_hash_is_sensitive_to_every_byte():
    assert exact_hash(b"hello") != exact_hash(b"Hello")
    assert exact_hash(b"hello") == exact_hash(b"hello")


def test_normalized_hash_ignores_whitespace_and_case_differences():
    a = normalized_hash("Hello   World")
    b = normalized_hash("hello world")
    assert a == b


def test_normalized_hash_differs_for_different_content():
    assert normalized_hash("Article one content") != normalized_hash("Article two content")


def test_simhash_identical_text_has_zero_distance():
    text = "The quick brown fox jumps over the lazy dog. " * 5
    assert hamming_distance(simhash64(text), simhash64(text)) == 0


def test_simhash_near_duplicate_text_is_flagged():
    base = "Breaking news: the city council approved the new budget today. " * 10
    near = base + " Minor addendum added at the end."
    assert is_near_duplicate(simhash64(base), simhash64(near), max_distance=5)


def test_simhash_unrelated_text_is_not_near_duplicate():
    a = simhash64("Sports team wins championship after dramatic overtime victory. " * 10)
    b = simhash64("Stock markets fell sharply amid inflation concerns this week. " * 10)
    assert not is_near_duplicate(a, b, max_distance=3)
