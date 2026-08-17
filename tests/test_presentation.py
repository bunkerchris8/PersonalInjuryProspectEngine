from __future__ import annotations

import pandas as pd
import pytest

from src.presentation import (
    CRITERIA_LEVELS,
    build_prospect_table,
    criteria_breakdown,
    criteria_fulfilled_label,
    filter_prospects,
    format_address,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, "Not much"),
        (0.24, "Not much"),
        (0.25, "A little"),
        (0.50, "Some"),
        (0.75, "Most"),
        (0.90, "Many"),
    ],
)
def test_criteria_fulfilled_uses_plain_language_scale(score, expected):
    assert criteria_fulfilled_label(score) == expected


def test_only_criteria_level_filters_prospects():
    frame = pd.DataFrame(
        [
            {
                "canonical_name": "Distant but complete",
                "data_quality_score": 0.91,
                "adjusted_priority": 10,
                "estimated_driving_distance": 500,
                "review_status": "rejected",
            },
            {
                "canonical_name": "Nearby but incomplete",
                "data_quality_score": 0.60,
                "adjusted_priority": 90,
                "estimated_driving_distance": 1,
                "review_status": "approved",
            },
        ]
    )

    filtered = filter_prospects(frame, "Many")

    assert filtered["canonical_name"].tolist() == ["Distant but complete"]


def test_prospect_search_matches_names_locations_and_contacts():
    frame = pd.DataFrame(
        [
            {
                "canonical_name": "Bridgewater Builders",
                "city": "Bridgewater",
                "zip": "02324",
                "primary_contact_name": "Alex Rivera",
                "data_quality_score": 0.8,
                "adjusted_priority": 50,
            },
            {
                "canonical_name": "Plymouth Electric",
                "city": "Plymouth",
                "zip": "02360",
                "primary_contact_name": "Morgan Lee",
                "data_quality_score": 0.9,
                "adjusted_priority": 60,
            },
        ]
    )

    assert filter_prospects(frame, "Not much", "alex")["canonical_name"].tolist() == [
        "Bridgewater Builders"
    ]
    assert filter_prospects(frame, "Not much", "02360")["canonical_name"].tolist() == [
        "Plymouth Electric"
    ]


def test_breakdown_always_includes_the_full_scale():
    frame = pd.DataFrame({"data_quality_score": [0.1, 0.55, 0.76, 0.95]})
    breakdown = criteria_breakdown(frame)

    assert breakdown["Criteria fulfilled"].tolist() == list(CRITERIA_LEVELS)
    assert breakdown["Prospects"].sum() == 4


def test_prospect_table_prioritizes_address_and_contact_fields():
    frame = pd.DataFrame(
        [
            {
                "canonical_name": "Example prospect",
                "street": "10 Main St",
                "city": "Bridgewater",
                "state": "MA",
                "zip": "02324",
                "public_phone": "508-555-0100",
                "public_email": None,
                "primary_contact_name": "Public Contact",
                "primary_contact_role": "Director",
                "primary_contact_phone": None,
                "primary_contact_email": "contact@example.org",
                "data_quality_score": 0.80,
                "adjusted_priority": 42,
                "review_status": "needs_review",
                "conflict_count": 0,
                "has_stale_information": 0,
                "max_source_strength": 4,
            }
        ]
    )

    table = build_prospect_table(frame)

    assert table.columns[:8].tolist() == [
        "Organization",
        "Address",
        "Organization phone",
        "Organization email",
        "Primary contact",
        "Contact role",
        "Contact phone",
        "Contact email",
    ]
    assert table.loc[0, "Address"] == "10 Main St, Bridgewater, MA, 02324"
    assert table.loc[0, "Contact email"] == "contact@example.org"
    assert table.loc[0, "Criteria fulfilled"] == "Most"


def test_format_address_omits_blank_parts_without_placeholder_text():
    assert format_address("10 Main St", "", "MA", None) == "10 Main St, MA"
