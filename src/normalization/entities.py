from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz


CORPORATE_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
}


def _ascii(value: str | None) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def normalize_name(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", _ascii(value).lower()).strip()
    tokens = [token for token in text.split() if token not in CORPORATE_SUFFIXES]
    return " ".join(tokens)


def normalize_address(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", _ascii(value).lower()).strip()
    replacements = {
        "street": "st",
        "road": "rd",
        "avenue": "ave",
        "boulevard": "blvd",
        "drive": "dr",
        "highway": "hwy",
        "suite": "ste",
    }
    return " ".join(replacements.get(token, token) for token in text.split())


@dataclass(frozen=True)
class OrganizationCandidate:
    name: str
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    official_identifier: str | None = None
    local_number: str | None = None
    website: str | None = None
    public_phone: str | None = None


@dataclass(frozen=True)
class MatchResult:
    confidence: float
    reason: str
    auto_merge: bool


def _domain(url: str | None) -> str:
    if not url:
        return ""
    value = re.sub(r"^https?://", "", url.lower()).split("/", 1)[0]
    return value.removeprefix("www.")


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def match_organizations(
    incoming: OrganizationCandidate, existing: OrganizationCandidate
) -> MatchResult:
    if (
        incoming.official_identifier
        and existing.official_identifier
        and incoming.official_identifier == existing.official_identifier
    ):
        return MatchResult(1.0, "exact authoritative identifier", True)

    incoming_name = normalize_name(incoming.name)
    existing_name = normalize_name(existing.name)
    incoming_address = normalize_address(incoming.street)
    existing_address = normalize_address(existing.street)
    same_name = bool(incoming_name and incoming_name == existing_name)
    same_address = bool(incoming_address and incoming_address == existing_address)
    same_zip = bool(incoming.zip_code and incoming.zip_code == existing.zip_code)
    same_city = bool(
        normalize_name(incoming.city)
        and normalize_name(incoming.city) == normalize_name(existing.city)
        and (incoming.state or "").upper() == (existing.state or "").upper()
    )
    same_local = bool(
        incoming.local_number
        and existing.local_number
        and incoming.local_number == existing.local_number
    )
    same_domain = bool(_domain(incoming.website) and _domain(incoming.website) == _domain(existing.website))
    incoming_phone = _digits(incoming.public_phone)
    same_phone = bool(
        len(incoming_phone) >= 10 and incoming_phone == _digits(existing.public_phone)
    )

    if same_name and same_address:
        return MatchResult(0.98, "exact normalized name and street address", True)
    if same_name and same_local and (same_city or same_zip):
        return MatchResult(0.96, "exact name, local number, and locality", True)
    if same_name and (same_domain or same_phone) and (same_city or same_zip):
        return MatchResult(0.94, "exact name plus professional contact and locality", True)
    if same_name and (same_city or same_zip):
        return MatchResult(0.88, "exact name and locality", True)

    name_score = fuzz.token_set_ratio(incoming_name, existing_name)
    address_score = fuzz.ratio(incoming_address, existing_address) if incoming_address and existing_address else 0
    if name_score >= 92 and address_score >= 90 and (same_city or same_zip):
        return MatchResult(0.86, "strong multi-field fuzzy match", True)
    if same_name:
        return MatchResult(0.79, "name-only match requires review", False)
    if name_score >= 90 and (same_city or same_zip):
        return MatchResult(0.74, "probable name and locality match requires review", False)
    if name_score >= 80:
        return MatchResult(0.55, "similar name without enough corroboration", False)
    return MatchResult(0.25, "insufficient identity evidence", False)

