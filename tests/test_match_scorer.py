import uuid
from datetime import datetime, timedelta, timezone

from app.services.deduplication.dedup import simhash64
from app.services.intelligence.match_scorer import (
    Confidence,
    EntityRef,
    MatchCandidateInput,
    MatchDecision,
    score_candidate,
)
from app.services.intelligence.models import EntityType

_BASE_TIME = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


def _entity(entity_type=EntityType.ORGANIZATION) -> EntityRef:
    return EntityRef(entity_id=uuid.uuid4(), entity_type=entity_type)


def test_same_developing_story_attaches_high_confidence():
    """Spec section 17: articles A/B/C, minutes/hours apart, same police
    operation -- shared org+location entities, close in time -- must ATTACH."""
    police = _entity(EntityType.ORGANIZATION)
    city = _entity(EntityType.LOCATION)
    operation = _entity(EntityType.EVENT)

    doc_body = "Officials confirmed the arrests took place this afternoon."
    story_body = "Police launched a major operation in Hyderabad this morning."
    doc = MatchCandidateInput(
        document_title="Three suspects arrested in Hyderabad police operation",
        document_body_sample=doc_body,
        document_simhash=simhash64(doc_body),
        document_entities=[police, city, operation],
        document_time=_BASE_TIME + timedelta(hours=2),
        document_domain="localnews.example.com",
        document_page_type="NEWS_ARTICLE",
        story_title="Police operation begins in Hyderabad",
        story_body_sample=story_body,
        story_simhash=simhash64(story_body),
        story_entities=[police, city, operation],
        story_last_activity_at=_BASE_TIME,
        story_domains=["bbc.example.com"],
    )

    result = score_candidate(doc)
    assert result.decision == MatchDecision.ATTACH
    assert result.confidence in (Confidence.HIGH, Confidence.MEDIUM)
    assert result.feature_scores["entity_overlap"] > 0.5
    assert result.feature_scores["location_overlap"] == 1.0


def test_unrelated_operation_a_week_later_does_not_attach():
    """Spec section 17: article D, a week later, unrelated -- shares no
    specific entities and fails the temporal window -- must REJECT."""
    doc_body = "A different department announced plans for an unrelated initiative."
    story_body = "Police launched a major operation in Hyderabad this morning."
    doc = MatchCandidateInput(
        document_title="Unrelated police operation planned for next week",
        document_body_sample=doc_body,
        document_simhash=simhash64(doc_body),
        document_entities=[_entity(EntityType.ORGANIZATION)],
        document_time=_BASE_TIME + timedelta(days=7),
        document_domain="othernews.example.com",
        document_page_type="NEWS_ARTICLE",
        story_title="Police operation begins in Hyderabad",
        story_body_sample=story_body,
        story_simhash=simhash64(story_body),
        story_entities=[_entity(EntityType.ORGANIZATION), _entity(EntityType.LOCATION)],
        story_last_activity_at=_BASE_TIME,
        story_domains=["bbc.example.com"],
    )
    result = score_candidate(doc)
    assert result.decision == MatchDecision.REJECT


def test_shared_generic_words_alone_do_not_correlate_unrelated_stories():
    """Spec section Z: sharing only common role-noun-style entities (here
    modeled as no real entity overlap at all -- "police"/"arrest" are not
    even distinct entities, just common words) must not attach."""
    doc_body = "Local police made an arrest today in connection with a burglary."
    story_body = "Hyderabad police made an arrest today in connection with a fraud case."
    doc = MatchCandidateInput(
        document_title="Delhi police arrest suspect in unrelated case",
        document_body_sample=doc_body,
        document_simhash=simhash64(doc_body),
        document_entities=[_entity(EntityType.ORGANIZATION)],  # a different, unrelated "police" org entity
        document_time=_BASE_TIME + timedelta(hours=1),
        document_domain="delhinews.example.com",
        document_page_type="NEWS_ARTICLE",
        story_title="Hyderabad police arrest suspect in local case",
        story_body_sample=story_body,
        story_simhash=simhash64(story_body),
        story_entities=[_entity(EntityType.ORGANIZATION)],  # a DIFFERENT org entity (different city's police)
        story_last_activity_at=_BASE_TIME,
        story_domains=["hydnews.example.com"],
    )
    result = score_candidate(doc)
    # different entity_ids (not the same Delhi/Hyderabad org) -> zero entity overlap
    assert result.feature_scores["entity_overlap"] == 0.0
    assert result.decision == MatchDecision.REJECT


def test_person_only_overlap_is_down_weighted():
    same_person = _entity(EntityType.PERSON)
    doc_body = "Totally different content about a home renovation project entirely unrelated to anything else."
    story_body = "Some other unrelated content about a cooking recipe that shares no real subject matter."
    doc = MatchCandidateInput(
        document_title="A completely different headline about renovation",
        document_body_sample=doc_body,
        document_simhash=simhash64(doc_body),
        document_entities=[same_person],
        document_time=_BASE_TIME,
        document_domain="siteA.example.com",
        document_page_type="NEWS_ARTICLE",
        story_title="An unrelated other headline about cooking",
        story_body_sample=story_body,
        story_simhash=simhash64(story_body),
        story_entities=[same_person],
        story_last_activity_at=_BASE_TIME,
        story_domains=["siteB.example.com"],
    )
    result = score_candidate(doc)
    # person-only overlap weighted down (0.3 vs 1.0) -- must not alone push entity_overlap to a high value
    assert result.feature_scores["entity_overlap"] < 1.0
    assert result.decision == MatchDecision.REJECT
