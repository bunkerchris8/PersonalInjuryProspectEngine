from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


class PrivacyValidationError(ValueError):
    pass


PROHIBITED_FIELD_PATTERNS = (
    r"(^|_)medical($|_)",
    r"(^|_)health_condition($|_)",
    r"(^|_)disability($|_)",
    r"(^|_)individual_age($|_)",
    r"(^|_)date_of_birth($|_)",
    r"(^|_)dob($|_)",
    r"(^|_)race($|_)",
    r"(^|_)ethnicity($|_)",
    r"(^|_)religion($|_)",
    r"(^|_)immigration($|_)",
    r"(^|_)political_affiliation($|_)",
    r"(^|_)injury_history($|_)",
    r"(^|_)accident_history($|_)",
    r"(^|_)workers_comp_claim($|_)",
    r"(^|_)claimant($|_)",
    r"(^|_)crash_report($|_)",
    r"(^|_)home_address($|_)",
    r"(^|_)private_phone($|_)",
    r"(^|_)personal_email($|_)",
    r"(^|_)social_media_profile($|_)",
    r"(^|_)employee_name($|_)",
    r"(^|_)injury_narrative($|_)",
)


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def validate_headers(headers: Iterable[str]) -> None:
    prohibited: list[str] = []
    for header in headers:
        normalized = _normalized_header(header)
        if any(re.search(pattern, normalized) for pattern in PROHIBITED_FIELD_PATTERNS):
            prohibited.append(header)
    if prohibited:
        fields = ", ".join(sorted(prohibited))
        raise PrivacyValidationError(
            f"Import rejected because prohibited individual-level fields were found: {fields}"
        )


def validate_contact_row(row: Mapping[str, str]) -> None:
    scope = (row.get("contact_scope") or "").strip()
    if scope not in {"role_based", "named_professional"}:
        raise PrivacyValidationError(
            "Contacts must be role_based or named_professional public contacts."
        )
    if (row.get("is_public_professional") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise PrivacyValidationError(
            "Every contact must be explicitly marked as public professional information."
        )
    if not (row.get("role_title") or "").strip():
        raise PrivacyValidationError("Every contact must be connected to an organizational role.")
    if scope == "named_professional":
        if not (row.get("contact_name") or "").strip():
            raise PrivacyValidationError("Named professional contacts require a public name.")
        if not ((row.get("role_date") or "").strip() or (row.get("filing_year") or "").strip()):
            raise PrivacyValidationError(
                "Leadership names require a role date or filing year."
            )

