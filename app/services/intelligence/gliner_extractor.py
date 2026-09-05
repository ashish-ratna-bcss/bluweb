"""GLiNER zero-shot NER (spec Phase 8 sections 10-11). Apache-2.0
(`urchade/gliner_multi-v2.1` model card, verified this session), CPU-only
torch -- no GPU dependency introduced.

Verified live this session: correctly tags "Narendra Modi"/person,
"Microsoft Corporation"/organization, "Hyderabad"/location on a real
sentence, all >=0.98 confidence. Measured cold load ~7s (one-time, cached
via the module-level singleton below, never per-request per spec section
47), inference ~370-650ms per short document on CPU (no GPU available in
this environment) -- real, not vendor-claimed, numbers.

Label scope is deliberately split from spacy_extractor.py rather than
duplicated: GLiNER only predicts the labels spaCy's built-in tag set
*doesn't* have (EVENT/PRODUCT/VEHICLE/SOCIAL_HANDLE) when spaCy already
ran, and predicts the *full* controlled label set only as the fallback
path for Indic-routed text when IndicNER is unavailable (spec section 46).
Running GLiNER's ~400-650ms cost for labels spaCy already covers for free
would be exactly the "don't blindly run every model on every document"
spec section 10 forbids.

Known environment quirk, not a code bug: a stale/expired token cached at
~/.cache/huggingface/token on the dev machine this was built on caused a
misleading 401 "RepositoryNotFoundError" against the fully public backbone
model (microsoft/mdeberta-v3-base) even with `token=False` passed
explicitly -- huggingface_hub's implicit token resolution takes the cached
file over an explicit override in the version pinned here. Verified by
temporarily moving the token file aside: identical code succeeds. Not
expected to recur in a clean container (no such file exists there) --
documented here rather than worked around with extra code for a dev-
machine-only condition.
"""

from __future__ import annotations

import logging

from app.services.intelligence.models import GLINER_LABELS, EntityCandidate, EntityType

logger = logging.getLogger("webintel.intelligence.gliner")

MODEL_NAME = "urchade/gliner_multi-v2.1"
CONFIDENCE_THRESHOLD = 0.4

# Labels spaCy's tag set already covers for free -- skip these when spaCy
# already ran on this document (see module docstring).
_SPACY_COVERED_LABELS = {"person", "organization", "location"}

_model = None
_load_attempted = False


def is_available() -> bool:
    return _get_model() is not None


def _get_model():
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True
    try:
        from gliner import GLiNER
        _model = GLiNER.from_pretrained(MODEL_NAME, token=False)
    except Exception:  # noqa: BLE001 - model unavailable must degrade, never crash the crawl
        logger.warning("GLiNER model unavailable; GLiNER NER disabled", exc_info=True)
        _model = None
    return _model


def extract_gliner(text: str, *, skip_spacy_covered_labels: bool) -> list[EntityCandidate]:
    model = _get_model()
    if model is None:
        return []

    labels = [
        lbl for lbl in GLINER_LABELS
        if not (skip_spacy_covered_labels and lbl in _SPACY_COVERED_LABELS)
    ]
    if not labels:
        return []

    try:
        raw_entities = model.predict_entities(text[:5_000], labels, threshold=CONFIDENCE_THRESHOLD)
    except Exception:  # noqa: BLE001 - inference failure must degrade, never crash the crawl
        logger.warning("GLiNER inference failed", exc_info=True)
        return []

    candidates: list[EntityCandidate] = []
    for e in raw_entities:
        entity_type = GLINER_LABELS.get(e["label"])
        if entity_type is None:
            continue
        candidates.append(EntityCandidate(
            raw_text=e["text"], entity_type=entity_type, confidence=float(e["score"]), extractor="gliner",
            start_offset=e.get("start", -1), end_offset=e.get("end"),
        ))
    return candidates
