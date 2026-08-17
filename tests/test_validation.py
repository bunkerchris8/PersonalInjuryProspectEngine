from __future__ import annotations

from datetime import date

import pytest

from src.validation.privacy import PrivacyValidationError, validate_contact_row, validate_headers
from src.validation.sources import eligible_for_scoring, evaluate_freshness


def test_prohibited_individual_fields_are_rejected():
    with pytest.raises(PrivacyValidationError, match="prohibited"):
        validate_headers(["organization", "employee_name", "injury_narrative"])


def test_named_leader_requires_role_date():
    with pytest.raises(PrivacyValidationError, match="role date"):
        validate_contact_row(
            {
                "contact_scope": "named_professional",
                "is_public_professional": "1",
                "contact_name": "Public Leader",
                "role_title": "President",
            }
        )


def test_source_strength_one_is_never_scoring_eligible():
    assert not eligible_for_scoring(1, "verified")
    assert not eligible_for_scoring(2, "verified")
    assert eligible_for_scoring(4, "verified")


def test_leadership_freshness_window():
    current = evaluate_freshness(
        "leadership_page", "2026-01-01", as_of=date(2026, 8, 16)
    )
    stale = evaluate_freshness(
        "leadership_page", "2024-01-01", as_of=date(2026, 8, 16)
    )
    assert current.status == "current"
    assert stale.status == "stale"

