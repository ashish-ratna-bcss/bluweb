"""Field-level fingerprints for Phase 7 change detection, built on top of
the existing dedup primitives (app/services/deduplication/dedup.py) which
stay untouched -- those remain crawl_engine's own exact/normalized-hash and
simhash building blocks. This module adds the finer-grained fingerprints
the change detector needs (title/metadata/structure) using the same
sha256-over-normalized-text approach, plus SimHash distance calibration.

Fingerprint usage map (spec Phase 7 section 7):
  exact equality        -> content_hash (existing exact_hash, on raw bytes)
  normalized equality    -> normalized_hash (existing, on extracted body text)
  near-duplicate/change  -> simhash + Hamming distance (this module)
  field-level change     -> title_hash / metadata_hash (this module), used
                             as cheap short-circuits before the more
                             expensive paragraph diff (text_diff.py)
"""

from __future__ import annotations

import hashlib
import json

from app.services.deduplication.dedup import hamming_distance, normalize_text

# Calibrated against realistic paragraph-length news text (see
# tests/test_fingerprints.py::test_simhash_distance_tiers_match_calibration
# for the exact measurements this was picked from): a single-word edit or a
# short added sentence lands at distance 3; a fully rewritten paragraph out
# of three lands around 16; an unrelated document lands at 27+.
SIMHASH_NEAR_IDENTICAL_MAX = 3   # probably unchanged / trivial edit
SIMHASH_MODERATE_MAX = 12        # possible/minor content change
# > SIMHASH_MODERATE_MAX -> likely significant change


def title_hash(title: str | None) -> str | None:
    if not title:
        return None
    return hashlib.sha256(normalize_text(title).encode("utf-8")).hexdigest()


def metadata_hash(metadata: dict) -> str:
    """Hash over a stable JSON encoding so key order never causes a false
    "changed" signal. Only meant for a cheap equality short-circuit -- the
    page-type-specific comparators still diff individual metadata fields
    (price, status, ...) directly for anything that needs a real value."""
    stable = json.dumps(metadata, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def structure_hash(present_fields: list[str]) -> str:
    """Hash of which fields the document actually has values for (sorted,
    deduped) -- a cheap "did the page's shape change" signal, e.g. a listing
    that used to have images and now has none, independent of the values."""
    stable = ",".join(sorted(set(present_fields)))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


_UINT64_MASK = (1 << 64) - 1


def to_signed_int64(value: int) -> int:
    """simhash64() (dedup.py) returns an unsigned 64-bit int; Postgres
    BIGINT is signed 64-bit. Two's-complement wrap for storage -- values
    with the top bit set (roughly half of all fingerprints) would
    otherwise overflow asyncpg's int64 bind and fail the INSERT."""
    return value - (1 << 64) if value & (1 << 63) else value


def from_signed_int64(value: int) -> int:
    return value & _UINT64_MASK


def simhash_similarity(a: int | None, b: int | None) -> float:
    """1.0 = identical fingerprint, 0.0 = maximally different (all 64 bits
    flipped). Returns 0.0 if either fingerprint is missing (never computed,
    e.g. legacy rows from before this phase) rather than raising."""
    if a is None or b is None:
        return 0.0
    return 1.0 - (hamming_distance(a, b) / 64)


def simhash_distance_tier(a: int | None, b: int | None) -> str:
    """"near_identical" | "moderate" | "large" -- see thresholds above."""
    if a is None or b is None:
        return "large"
    distance = hamming_distance(a, b)
    if distance <= SIMHASH_NEAR_IDENTICAL_MAX:
        return "near_identical"
    if distance <= SIMHASH_MODERATE_MAX:
        return "moderate"
    return "large"
