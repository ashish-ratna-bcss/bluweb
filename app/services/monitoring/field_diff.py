"""Generic, deterministic field-level diff primitives (spec Phase 7 section
18). Page-type-specific comparators (change_detection.py) call these
instead of re-implementing scalar/list comparison per page type."""

from __future__ import annotations

from typing import Any


def diff_scalar(old: Any, new: Any) -> dict[str, Any] | None:
    """None if unchanged, else {"old": ..., "new": ...}. Strings are
    compared after strip() so trailing-whitespace differences from the
    extractor never register as a change."""
    old_cmp = old.strip() if isinstance(old, str) else old
    new_cmp = new.strip() if isinstance(new, str) else new
    if old_cmp == new_cmp:
        return None
    return {"old": old, "new": new}


def diff_list(old: list, new: list) -> dict[str, list]:
    """Order-independent added/removed for simple hashable-item lists
    (tags, image URLs). Returns {} keys always present, empty when equal."""
    old_set, new_set = set(old or []), set(new or [])
    return {
        "added": sorted(new_set - old_set),
        "removed": sorted(old_set - new_set),
    }


def diff_number(old: float | int | None, new: float | int | None) -> dict[str, float] | None:
    """Scalar diff for prices/counts, with delta + percentage change --
    percentage omitted (not 0) when old is 0/None to avoid a fake
    division-by-zero-flavored number (spec section 13: don't hardcode
    currency assumptions, and don't fabricate a percentage that isn't
    meaningful)."""
    if old == new:
        return None
    result: dict[str, float] = {"old": old, "new": new}
    if old is not None and new is not None:
        result["delta"] = new - old
        if old:
            result["percentage"] = round((new - old) / old * 100, 2)
    return result
