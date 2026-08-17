from __future__ import annotations

import pytest

from src.scoring.model import DataQualityInputs, ProspectFeatures, score_prospect


def _quality() -> DataQualityInputs:
    return DataQualityInputs(
        eligible_source_strengths=(5, 4),
        material_field_count=10,
        sourced_material_field_count=10,
        current_assertion_count=8,
        total_assertion_count=10,
        identity_confidence=0.98,
        conflict_count=0,
    )


def test_score_is_transparent_and_uses_required_adjustment():
    features = ProspectFeatures(
        organization_type="union_local",
        industry="electrical construction",
        estimated_reach=1_000,
        current_role_titles=("Business Agent",),
        estimated_driving_distance=18,
        public_contact_available=True,
        public_accessibility=True,
        upcoming_event_count=1,
        active_program=True,
        relevant_workforce_pct=30,
        osha_context_available=True,
    )
    result = score_prospect(features, _quality())
    assert result.workforce_relevance == 25
    assert result.role_influence == 20
    assert result.proximity == 12
    assert result.adjusted_priority == pytest.approx(
        result.raw_prospect_score * (0.70 + 0.30 * result.data_quality_score),
        abs=0.01,
    )
    assert "Business Agent" in result.explanation
    assert "likely to become" not in result.explanation
    assert "dangerous" not in result.explanation


def test_osha_context_is_capped_and_injury_counts_are_not_inputs():
    base = ProspectFeatures(
        organization_type="workplace",
        industry="manufacturing",
        estimated_reach=100,
        current_role_titles=(),
        estimated_driving_distance=10,
        public_contact_available=False,
        public_accessibility=False,
        upcoming_event_count=0,
        active_program=False,
        osha_context_available=False,
    )
    with_context = ProspectFeatures(**{**base.__dict__, "osha_context_available": True})
    assert (
        score_prospect(with_context, _quality()).workforce_relevance
        - score_prospect(base, _quality()).workforce_relevance
        == 2
    )

