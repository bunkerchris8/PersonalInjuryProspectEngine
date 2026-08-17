from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


FRESHNESS_WINDOWS_DAYS = {
    "event": 90,
    "official_event_page": 90,
    "leadership_page": 365,
    "contact_page": 365,
    "official_organization_page": 365,
    "union_filing": 730,
    "olms_filing": 730,
    "osha_ita_summary": 1095,
    "osha_inspection": 1095,
    "census_acs": 1095,
    "license": 365,
    "permit": 730,
    "public_contract": 730,
}


@dataclass(frozen=True)
class FreshnessResult:
    status: str
    expires_at: date
    age_days: int


def parse_iso_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def evaluate_freshness(
    source_type: str,
    observed_or_published_at: str | date,
    *,
    as_of: date | None = None,
) -> FreshnessResult:
    reference = parse_iso_date(observed_or_published_at)
    if reference is None:
        raise ValueError("Freshness evaluation requires an observation or publication date.")
    today = as_of or date.today()
    window = FRESHNESS_WINDOWS_DAYS.get(source_type, 365)
    expires = reference + timedelta(days=window)
    age = (today - reference).days
    if age < 0:
        status = "future_dated"
    elif today <= expires:
        status = "current"
    else:
        status = "stale"
    return FreshnessResult(status=status, expires_at=expires, age_days=age)


def event_freshness(event_date: str | date | None, *, as_of: date | None = None) -> str:
    event_day = parse_iso_date(event_date)
    if event_day is None:
        return "unverified"
    today = as_of or date.today()
    if event_day >= today:
        return "upcoming"
    if event_day >= today - timedelta(days=90):
        return "recent"
    return "stale"


def validate_source_metadata(source: dict[str, object]) -> None:
    required = (
        "source_url",
        "publisher",
        "source_title",
        "retrieval_date",
        "source_strength",
        "source_type",
        "validation_status",
    )
    missing = [key for key in required if not source.get(key)]
    if missing:
        raise ValueError(f"Missing source metadata: {', '.join(missing)}")
    strength = int(source["source_strength"])
    if strength not in range(1, 6):
        raise ValueError("Source strength must be an integer from 1 through 5.")
    parse_iso_date(str(source["retrieval_date"]))
    publication = source.get("publication_date")
    if publication:
        parse_iso_date(str(publication))


def eligible_for_scoring(
    source_strength: int, validation_status: str, minimum_strength: int = 3
) -> bool:
    return source_strength >= minimum_strength and validation_status.lower() in {
        "verified",
        "validated",
        "corroborated",
        "current",
    }


def contact_source_requirement_met(
    strengths: list[int], official_current: bool = False
) -> bool:
    if official_current and any(strength >= 4 for strength in strengths):
        return True
    if any(strength == 5 for strength in strengths):
        return True
    return sum(strength >= 3 for strength in strengths) >= 2

