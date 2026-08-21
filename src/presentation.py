from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


CRITERIA_LEVELS = ("Not much", "A little", "Some", "Most", "Many")
CRITERIA_MINIMUMS = {
    "Not much": 0.0,
    "A little": 0.25,
    "Some": 0.50,
    "Most": 0.75,
    "Many": 0.90,
}


def criteria_fulfilled_label(score: object) -> str:
    """Return the plain-language criteria level for a 0-1 quality score."""
    try:
        numeric_score = float(score)
    except (TypeError, ValueError):
        numeric_score = 0.0
    if pd.isna(numeric_score):
        numeric_score = 0.0
    for label in reversed(CRITERIA_LEVELS):
        if numeric_score >= CRITERIA_MINIMUMS[label]:
            return label
    return CRITERIA_LEVELS[0]


def minimum_criteria_score(label: str) -> float:
    try:
        return CRITERIA_MINIMUMS[label]
    except KeyError as exc:
        raise ValueError(f"Unknown criteria level: {label}") from exc


def format_address(
    street: object = None,
    city: object = None,
    state: object = None,
    zip_code: object = None,
) -> str:
    parts = []
    for value in (street, city, state, zip_code):
        if value is None or pd.isna(value):
            continue
        normalized = str(value).strip()
        if normalized:
            parts.append(normalized)
    return ", ".join(parts)


def filter_prospects(
    frame: pd.DataFrame,
    minimum_level: str,
    query: str = "",
) -> pd.DataFrame:
    """Apply the dashboard's quality threshold and plain-text search."""
    minimum_score = minimum_criteria_score(minimum_level)
    if frame.empty:
        return frame.copy()
    result = frame[frame["data_quality_score"].fillna(0) >= minimum_score].copy()
    normalized_query = query.strip().casefold()
    if normalized_query:
        searchable_columns = [
            column
            for column in (
                "canonical_name",
                "organization_type",
                "industry",
                "union_affiliation",
                "local_number",
                "street",
                "city",
                "state",
                "zip",
                "public_phone",
                "public_email",
                "primary_contact_name",
                "primary_contact_role",
                "primary_contact_phone",
                "primary_contact_email",
            )
            if column in result
        ]
        searchable_text = (
            result[searchable_columns]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
            .str.casefold()
        )
        result = result[searchable_text.str.contains(normalized_query, regex=False)]
    return result.sort_values(
        ["adjusted_priority", "data_quality_score", "canonical_name"],
        ascending=[False, False, True],
        na_position="last",
    )


def criteria_breakdown(frame: pd.DataFrame) -> pd.DataFrame:
    labels: Iterable[str]
    if frame.empty:
        labels = ()
    else:
        labels = frame["data_quality_score"].map(criteria_fulfilled_label)
    counts = pd.Series(labels, dtype="object").value_counts()
    return pd.DataFrame(
        {
            "Criteria fulfilled": CRITERIA_LEVELS,
            "Prospects": [int(counts.get(level, 0)) for level in CRITERIA_LEVELS],
        }
    )


def build_prospect_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Organization",
        "Address",
        "Organization phone",
        "Organization email",
        "Primary contact",
        "Contact role",
        "Contact phone",
        "Contact email",
        "Criteria fulfilled",
        "Adjusted priority",
        "Review",
        "Data flags",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        address = format_address(row.get("street"), row.get("city"), row.get("state"), row.get("zip"))
        organization_phone = _display_value(row.get("public_phone"))
        organization_email = _display_value(row.get("public_email"))
        contact_phone = _display_value(row.get("primary_contact_phone"))
        contact_email = _display_value(row.get("primary_contact_email"))
        flags = []
        if not address:
            flags.append("Address missing")
        if not any((organization_phone, organization_email, contact_phone, contact_email)):
            flags.append("Contact details missing")
        if row.get("conflict_count"):
            flags.append("Conflicting sources")
        if row.get("has_stale_information"):
            flags.append("Stale information")
        if float(row.get("max_source_strength") or 0) < 3:
            flags.append("Unverified source")

        rows.append(
            {
                "Organization": _display_value(row.get("canonical_name")),
                "Address": address,
                "Organization phone": organization_phone,
                "Organization email": organization_email,
                "Primary contact": _display_value(row.get("primary_contact_name")),
                "Contact role": _display_value(row.get("primary_contact_role")),
                "Contact phone": contact_phone,
                "Contact email": contact_email,
                "Criteria fulfilled": criteria_fulfilled_label(row.get("data_quality_score")),
                "Adjusted priority": row.get("adjusted_priority"),
                "Review": str(row.get("review_status") or "Unknown").replace("_", " ").title(),
                "Data flags": ", ".join(flags) if flags else "Current",
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _display_value(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()
