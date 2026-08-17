from __future__ import annotations

from src.normalization.entities import OrganizationCandidate, match_organizations, normalize_name
from src.normalization.geography import calculate_distance


def test_normalize_name_preserves_local_number():
    assert normalize_name("IBEW Local No. 223, Inc.") == "ibew local no 223"


def test_exact_name_and_address_can_merge():
    incoming = OrganizationCandidate(
        name="Example Workers Local 10",
        street="10 Main Street",
        city="Bridgewater",
        state="MA",
        zip_code="02324",
    )
    existing = OrganizationCandidate(
        name="Example Workers Local 10",
        street="10 Main St.",
        city="Bridgewater",
        state="MA",
        zip_code="02324",
    )
    result = match_organizations(incoming, existing)
    assert result.auto_merge is True
    assert result.confidence >= 0.95


def test_similar_name_alone_does_not_merge():
    incoming = OrganizationCandidate(name="Acme Services")
    existing = OrganizationCandidate(name="Acme Service Company")
    result = match_organizations(incoming, existing)
    assert result.auto_merge is False
    assert result.confidence < 0.80


def test_geographic_tiers_use_estimated_driving_distance(settings):
    result = calculate_distance(41.956761589881, -71.134291473971, settings)
    assert result.straight_line_miles > 0
    assert result.estimated_driving_miles > result.straight_line_miles
    assert result.geographic_tier == "A"
    assert result.within_configured_radius is True

