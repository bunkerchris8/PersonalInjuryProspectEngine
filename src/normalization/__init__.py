"""Entity and geographic normalization helpers."""

from .entities import MatchResult, OrganizationCandidate, match_organizations, normalize_name
from .geography import DistanceResult, calculate_distance

__all__ = [
    "DistanceResult",
    "MatchResult",
    "OrganizationCandidate",
    "calculate_distance",
    "match_organizations",
    "normalize_name",
]

