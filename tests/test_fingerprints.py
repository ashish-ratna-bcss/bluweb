from app.services.deduplication.dedup import hamming_distance, simhash64
from app.services.monitoring.fingerprints import (
    SIMHASH_MODERATE_MAX,
    SIMHASH_NEAR_IDENTICAL_MAX,
    from_signed_int64,
    metadata_hash,
    simhash_distance_tier,
    simhash_similarity,
    structure_hash,
    title_hash,
    to_signed_int64,
)

_BASE = """Police announced a major operation today targeting organized crime networks across the city.
The operation, which involved coordination between multiple agencies, resulted in several arrests.
Officials say the investigation began several months ago after receiving credible intelligence.
""" * 3


def test_identical_content_zero_distance():
    a = simhash64(_BASE)
    assert hamming_distance(a, a) == 0
    assert simhash_distance_tier(a, a) == "near_identical"
    assert simhash_similarity(a, a) == 1.0


def test_whitespace_only_change_stays_near_identical():
    variant = _BASE.replace("\n", "\n\n")
    assert simhash_distance_tier(simhash64(_BASE), simhash64(variant)) == "near_identical"


def test_formatting_only_change_stays_near_identical():
    variant = _BASE.replace("organized", "organised")  # one word, cosmetic spelling
    tier = simhash_distance_tier(simhash64(_BASE), simhash64(variant))
    assert tier == "near_identical"


def test_minor_content_addition_is_near_identical_or_moderate():
    variant = _BASE + "\nA follow-up press conference is scheduled for tomorrow.\n"
    distance = hamming_distance(simhash64(_BASE), simhash64(variant))
    assert distance <= SIMHASH_MODERATE_MAX


def test_major_rewrite_is_large_distance():
    unrelated = "Stock markets rallied today as investors reacted positively to earnings reports. " * 5
    tier = simhash_distance_tier(simhash64(_BASE), simhash64(unrelated))
    assert tier == "large"
    assert simhash_similarity(simhash64(_BASE), simhash64(unrelated)) < 0.7


def test_missing_fingerprint_is_treated_as_large_not_crash():
    assert simhash_distance_tier(None, simhash64(_BASE)) == "large"
    assert simhash_similarity(None, simhash64(_BASE)) == 0.0


def test_title_hash_ignores_case_and_whitespace():
    assert title_hash("Hello   World") == title_hash("hello world")
    assert title_hash(None) is None


def test_metadata_hash_ignores_key_order():
    assert metadata_hash({"a": 1, "b": 2}) == metadata_hash({"b": 2, "a": 1})
    assert metadata_hash({"a": 1}) != metadata_hash({"a": 2})


def test_structure_hash_ignores_order_and_duplicates():
    assert structure_hash(["title", "body"]) == structure_hash(["body", "title", "body"])
    assert structure_hash(["title"]) != structure_hash(["title", "body"])


def test_near_identical_threshold_is_tighter_than_moderate():
    assert SIMHASH_NEAR_IDENTICAL_MAX < SIMHASH_MODERATE_MAX


def test_signed_int64_roundtrip_for_high_bit_fingerprints():
    # Postgres BIGINT is signed 64-bit; simhash64() is unsigned -- any
    # fingerprint with the top bit set previously overflowed the INSERT
    # (caught live: 15093231179685694834 > int64 max).
    high_bit_value = 15_093_231_179_685_694_834
    signed = to_signed_int64(high_bit_value)
    assert -(2**63) <= signed < 2**63
    assert from_signed_int64(signed) == high_bit_value


def test_signed_int64_roundtrip_across_real_fingerprints():
    for text in ("hello world", "a" * 500, "", "unicode: café résumé"):
        value = simhash64(text)
        assert from_signed_int64(to_signed_int64(value)) == value
