"""GLiNER zero-shot NER (spec Phase 8 sections 10-11). Apache-2.0
(`urchade/gliner_multi-v2.1` model card), CPU-only torch.

Primary English NER path: predicts the full controlled label set
(PERSON/ORG/LOCATION/EVENT/PRODUCT/VEHICLE/SOCIAL_HANDLE). spaCy is only
a fallback when this model fails to load (see entity_extractor.py).

Cold load downloads once into the Hugging Face cache
(~/.cache/huggingface); later processes reuse local files. Set `HF_TOKEN`
in `.env` for the first authenticated download (and to override a stale
CLI token at ~/.cache/huggingface/token that otherwise causes 401s).
"""

from __future__ import annotations

import logging

from app.core.config import apply_hf_token_to_environ, get_settings
from app.services.intelligence.models import GLINER_LABELS, EntityCandidate, EntityType

logger = logging.getLogger("webintel.intelligence.gliner")

MODEL_NAME = "urchade/gliner_multi-v2.1"
CONFIDENCE_THRESHOLD = 0.4

# Kept for API compatibility with entity_extractor callers that still pass
# skip_spacy_covered_labels (e.g. legacy spaCy-primary path). When GLiNER
# is primary we pass False so the full label set runs.
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
        apply_hf_token_to_environ()
        from gliner import GLiNER

        token = get_settings().hf_token
        # Explicit token beats a stale huggingface-cli cache; False = anonymous.
        _model = GLiNER.from_pretrained(MODEL_NAME, token=token if token else False)
        logger.info("GLiNER model loaded model=%s", MODEL_NAME)
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
