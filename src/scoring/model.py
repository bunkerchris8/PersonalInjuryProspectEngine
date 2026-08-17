from __future__ import annotations

from dataclasses import asdict, dataclass


RELEVANT_INDUSTRY_KEYWORDS = (
    "construction",
    "electric",
    "transport",
    "warehouse",
    "manufactur",
    "municipal",
    "public works",
    "healthcare support",
    "maintenance",
    "landscap",
    "utility",
    "labor",
    "driver",
    "trade",
    "fire",
    "police",
    "correction",
)

ROLE_VALUES = (
    (("business manager", "business agent", "business representative"), 20.0),
    (("president", "principal officer", "secretary-treasurer"), 18.0),
    (("membership director", "membership development", "organizer"), 16.0),
    (("safety director", "safety officer", "training director"), 16.0),
    (("apprenticeship coordinator", "apprenticeship director", "director of training"), 15.0),
    (("event coordinator", "event organizer"), 12.0),
    (("owner", "public-facing manager", "general manager"), 12.0),
    (("steward",), 10.0),
)


@dataclass(frozen=True)
class DataQualityInputs:
    eligible_source_strengths: tuple[int, ...]
    material_field_count: int
    sourced_material_field_count: int
    current_assertion_count: int
    total_assertion_count: int
    identity_confidence: float
    conflict_count: int = 0


@dataclass(frozen=True)
class ProspectFeatures:
    organization_type: str
    industry: str | None
    estimated_reach: int | None
    current_role_titles: tuple[str, ...]
    estimated_driving_distance: float | None
    public_contact_available: bool
    public_accessibility: bool
    upcoming_event_count: int
    active_program: bool
    relevant_workforce_pct: float | None = None
    osha_context_available: bool = False


@dataclass(frozen=True)
class ScoreResult:
    workforce_relevance: float
    organizational_reach: float
    role_influence: float
    proximity: float
    public_accessibility: float
    relationship_potential: float
    raw_prospect_score: float
    data_quality_score: float
    adjusted_priority: float
    explanation: str
    input_snapshot: dict[str, object]


def _workforce_score(features: ProspectFeatures) -> tuple[float, list[str]]:
    org_type = features.organization_type.lower()
    industry = (features.industry or "").lower()
    score = 0.0
    reasons: list[str] = []
    if "union" in org_type or "labor" in org_type:
        score += 13
        reasons.append("represents an organized workforce")
    if any(keyword in industry for keyword in RELEVANT_INDUSTRY_KEYWORDS):
        score += 8
        reasons.append("serves a priority workforce sector")
    if features.relevant_workforce_pct is not None:
        score += min(4.0, max(0.0, features.relevant_workforce_pct) / 10.0)
        if features.relevant_workforce_pct >= 20:
            reasons.append("is linked to a community with a substantial relevant-workforce share")
    # OSHA only confirms organization-level workforce context; injury totals never add points.
    if features.osha_context_available:
        score += 2
    return min(25.0, score), reasons


def _reach_score(estimated_reach: int | None) -> tuple[float, str | None]:
    if estimated_reach is None or estimated_reach <= 0:
        return 0.0, None
    thresholds = (
        (5000, 20.0, "has very broad documented organizational reach"),
        (2000, 17.0, "has broad documented organizational reach"),
        (750, 14.0, "has substantial documented organizational reach"),
        (250, 10.0, "has meaningful documented organizational reach"),
        (75, 6.0, "has a documented local membership or workforce"),
        (1, 3.0, "has a documented organizational constituency"),
    )
    for minimum, score, reason in thresholds:
        if estimated_reach >= minimum:
            return score, reason
    return 0.0, None


def _role_score(role_titles: tuple[str, ...]) -> tuple[float, str | None]:
    best = 0.0
    best_title: str | None = None
    for title in role_titles:
        normalized = title.lower()
        for keywords, value in ROLE_VALUES:
            if any(keyword in normalized for keyword in keywords) and value > best:
                best = value
                best_title = title
    if best_title:
        return best, f"has a currently verified public {best_title} role"
    return 0.0, None


def _proximity_score(distance: float | None) -> tuple[float, str | None]:
    if distance is None:
        return 0.0, None
    if distance <= 15:
        return 15.0, f"is about {distance:.0f} estimated driving miles from Bridgewater"
    if distance <= 30:
        return 12.0, f"is about {distance:.0f} estimated driving miles from Bridgewater"
    if distance <= 45:
        return 8.0, f"is about {distance:.0f} estimated driving miles from Bridgewater"
    if distance <= 60:
        return 4.0, f"is about {distance:.0f} estimated driving miles from Bridgewater"
    return 0.0, None


def _accessibility_score(features: ProspectFeatures) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if features.public_contact_available:
        score += 3
        reasons.append("publishes a professional organizational contact channel")
    if features.public_accessibility:
        score += 3
    if features.upcoming_event_count:
        score += min(3.0, 1.5 + features.upcoming_event_count)
        reasons.append("has a current public-facing event or program window")
    if features.active_program:
        score += 1
        reasons.append("operates an active member, training, or apprenticeship program")
    return min(10.0, score), reasons


def _relationship_score(features: ProspectFeatures) -> float:
    org_type = features.organization_type.lower()
    if "union" in org_type or "labor" in org_type:
        score = 8.0
    elif any(term in org_type for term in ("association", "chamber", "trade")):
        score = 7.0
    elif any(term in org_type for term in ("community", "nonprofit", "apprenticeship")):
        score = 6.0
    elif "workplace" in org_type or "employer" in org_type:
        score = 4.0
    else:
        score = 3.0
    if features.active_program:
        score += 2.0
    return min(10.0, score)


def calculate_data_quality(inputs: DataQualityInputs) -> float:
    if not inputs.eligible_source_strengths:
        return 0.0
    source_score = sum(inputs.eligible_source_strengths) / (
        5 * len(inputs.eligible_source_strengths)
    )
    coverage = (
        inputs.sourced_material_field_count / inputs.material_field_count
        if inputs.material_field_count
        else 0.0
    )
    freshness = (
        inputs.current_assertion_count / inputs.total_assertion_count
        if inputs.total_assertion_count
        else 0.0
    )
    identity = min(1.0, max(0.0, inputs.identity_confidence))
    conflict = max(0.0, 1.0 - min(inputs.conflict_count, 3) / 3)
    quality = (
        0.30 * source_score
        + 0.25 * min(1.0, coverage)
        + 0.20 * min(1.0, freshness)
        + 0.15 * identity
        + 0.10 * conflict
    )
    return round(min(1.0, max(0.0, quality)), 3)


def _explanation(raw_score: float, reasons: list[str]) -> str:
    if raw_score >= 70:
        lead = "High priority"
    elif raw_score >= 45:
        lead = "Moderate priority"
    else:
        lead = "Developing priority"
    unique_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    if not unique_reasons:
        return f"{lead} based on currently verified organizational information; additional research is needed."
    if len(unique_reasons) == 1:
        body = unique_reasons[0]
    else:
        body = ", ".join(unique_reasons[:-1]) + f", and {unique_reasons[-1]}"
    return f"{lead} because the organization {body}."


def score_prospect(
    features: ProspectFeatures, quality_inputs: DataQualityInputs
) -> ScoreResult:
    workforce, reasons = _workforce_score(features)
    reach, reach_reason = _reach_score(features.estimated_reach)
    influence, role_reason = _role_score(features.current_role_titles)
    proximity, proximity_reason = _proximity_score(features.estimated_driving_distance)
    accessibility, access_reasons = _accessibility_score(features)
    relationship = _relationship_score(features)
    reasons.extend(reason for reason in (reach_reason, role_reason, proximity_reason) if reason)
    reasons.extend(access_reasons)
    raw = round(workforce + reach + influence + proximity + accessibility + relationship, 2)
    quality = calculate_data_quality(quality_inputs)
    adjusted = round(raw * (0.70 + 0.30 * quality), 2)
    return ScoreResult(
        workforce_relevance=workforce,
        organizational_reach=reach,
        role_influence=influence,
        proximity=proximity,
        public_accessibility=accessibility,
        relationship_potential=relationship,
        raw_prospect_score=raw,
        data_quality_score=quality,
        adjusted_priority=adjusted,
        explanation=_explanation(raw, reasons),
        input_snapshot={
            "features": asdict(features),
            "data_quality": asdict(quality_inputs),
        },
    )
