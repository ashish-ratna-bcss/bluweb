"""Deduplication: exact hash, normalized-content hash, and a lightweight
64-bit SimHash for near-duplicate detection.

ponytail: SimHash here is a plain hashlib-based implementation (no numpy,
no extra dependency) -- fine at instant-crawl volumes (hundreds of pages
per run). If near-dup matching needs to scale to millions of documents,
swap the linear Hamming-distance scan in `is_near_duplicate` for an
indexed LSH/MinHash structure.
"""

from __future__ import annotations

import hashlib
import re

_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+")


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def exact_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def normalized_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def simhash64(text: str) -> int:
    """64-bit SimHash fingerprint over word tokens of the normalized text."""
    weights = [0] * 64
    tokens = _WORD_RE.findall(normalize_text(text))
    if not tokens:
        return 0

    for token in tokens:
        token_hash = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) & ((1 << 64) - 1)
        for bit in range(64):
            weights[bit] += 1 if (token_hash >> bit) & 1 else -1

    fingerprint = 0
    for bit in range(64):
        if weights[bit] > 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def is_near_duplicate(fingerprint_a: int, fingerprint_b: int, *, max_distance: int = 3) -> bool:
    return hamming_distance(fingerprint_a, fingerprint_b) <= max_distance
