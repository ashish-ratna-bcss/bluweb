"""AI4Bharat IndicNER (spec Phase 8 sections 3/10): fine-tuned NER across
11 Indian languages (Assamese, Bengali, Gujarati, Hindi, Kannada,
Malayalam, Marathi, Oriya, Punjabi, Tamil, Telugu).

Verified this session: the model card (huggingface.co/ai4bharat/IndicNER)
is public and MIT-licensed, but the actual model weights are behind
Hugging Face's gated-repo access request flow -- `snapshot_download`
returns `GatedRepoError` even with an anonymous/no-token request,
confirming this isn't a licensing ambiguity (MIT is unambiguous) but an
access-request gate this session cannot complete (it requires a human to
click "Access repository" on huggingface.co, tied to an account).

Per spec section 48 ("if licensing is ambiguous: make it optional,
document it, provide a fallback") and section 46 (NLP failure must
degrade gracefully): this module is fully wired -- correct model id,
correct BIO-tag label mapping -- and will start working the moment access
is granted and `transformers` can fetch it, with no code change. Until
then, `is_available()` reports False and Indic-routed text falls back to
GLiNER's full multilingual label set (gliner_extractor.py), which is a
real but weaker substitute: general multilingual coverage, not fine-tuned
for these 11 languages specifically.

To unblock: request access at https://huggingface.co/ai4bharat/IndicNER
with the account whose token the deployment uses, then set
`HF_TOKEN` in the environment.
"""

from __future__ import annotations

import logging

from app.services.intelligence.models import EntityCandidate, EntityType

logger = logging.getLogger("webintel.intelligence.indicner")

MODEL_NAME = "ai4bharat/IndicNER"

# IndicNER's BIO tag scheme, per its model card / training setup.
_LABEL_MAP: dict[str, EntityType] = {
    "PER": EntityType.PERSON,
    "ORG": EntityType.ORGANIZATION,
    "LOC": EntityType.LOCATION,
}

_pipeline = None
_load_attempted = False


def is_available() -> bool:
    return _get_pipeline() is not None


def _get_pipeline():
    global _pipeline, _load_attempted
    if _load_attempted:
        return _pipeline
    _load_attempted = True
    try:
        from app.core.config import apply_hf_token_to_environ, get_settings

        apply_hf_token_to_environ()
        from transformers import pipeline

        token = get_settings().hf_token
        _pipeline = pipeline(
            "token-classification",
            model=MODEL_NAME,
            aggregation_strategy="simple",
            token=token if token else None,
        )
    except Exception:  # noqa: BLE001 - gated/missing model must degrade, never crash the crawl
        logger.warning(
            "IndicNER unavailable (gated HF repo -- see module docstring); Indic NER falls back to GLiNER",
            exc_info=True,
        )
        _pipeline = None
    return _pipeline


def extract_indicner(text: str) -> list[EntityCandidate]:
    pipe = _get_pipeline()
    if pipe is None:
        return []

    try:
        raw_entities = pipe(text[:5_000])
    except Exception:  # noqa: BLE001
        logger.warning("IndicNER inference failed", exc_info=True)
        return []

    candidates: list[EntityCandidate] = []
    for e in raw_entities:
        entity_type = _LABEL_MAP.get(e.get("entity_group", ""))
        if entity_type is None:
            continue
        candidates.append(EntityCandidate(
            raw_text=e["word"], entity_type=entity_type, confidence=float(e["score"]), extractor="indicner",
            start_offset=e.get("start", -1), end_offset=e.get("end"),
        ))
    return candidates
