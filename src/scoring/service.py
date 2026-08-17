from __future__ import annotations

import json
import sqlite3
from datetime import date

from src.config import Settings
from src.ingestion.common import new_id, utc_now
from src.scoring.model import DataQualityInputs, ProspectFeatures, score_prospect


ELIGIBLE_STATUSES = ("verified", "validated", "corroborated", "current")
MATERIAL_FIELDS = (
    "canonical_name",
    "organization_type",
    "industry",
    "union_affiliation",
    "local_number",
    "website",
    "public_phone",
    "public_email",
    "street",
    "city",
    "state",
    "zip",
    "estimated_reach",
    "active_status",
)


def _eligible_assertions(
    connection: sqlite3.Connection, organization_id: str, minimum_strength: int
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in ELIGIBLE_STATUSES)
    return connection.execute(
        f"""
        SELECT a.*, s.source_strength
        FROM source_assertions a
        JOIN sources s ON s.source_id = a.source_id
        WHERE a.entity_type = 'organization' AND a.entity_id = ?
          AND s.source_strength >= ?
          AND LOWER(s.validation_status) IN ({placeholders})
          AND LOWER(a.validation_status) IN ({placeholders})
        """,
        (
            organization_id,
            minimum_strength,
            *ELIGIBLE_STATUSES,
            *ELIGIBLE_STATUSES,
        ),
    ).fetchall()


def _current_role_titles(
    connection: sqlite3.Connection, organization_id: str, minimum_strength: int
) -> tuple[str, ...]:
    placeholders = ", ".join("?" for _ in ELIGIBLE_STATUSES)
    rows = connection.execute(
        f"""
        SELECT r.role_title
        FROM roles r
        JOIN source_assertions a
          ON a.entity_type = 'role' AND a.entity_id = r.role_id
         AND a.field_name = 'role_title'
        JOIN sources s ON s.source_id = a.source_id
        WHERE r.organization_id = ? AND r.current_status = 'current'
          AND s.source_strength >= ?
          AND LOWER(s.validation_status) IN ({placeholders})
          AND LOWER(a.validation_status) IN ({placeholders})
          AND (a.freshness_expires_at IS NULL OR a.freshness_expires_at >= date('now'))
        GROUP BY r.role_id, r.role_title
        HAVING MAX(s.source_strength) >= 4 OR COUNT(DISTINCT s.source_id) >= 2
        """,
        (organization_id, minimum_strength, *ELIGIBLE_STATUSES, *ELIGIBLE_STATUSES),
    ).fetchall()
    return tuple(row["role_title"] for row in rows)


def _has_verified_contact(
    connection: sqlite3.Connection, organization_id: str, minimum_strength: int
) -> bool:
    placeholders = ", ".join("?" for _ in ELIGIBLE_STATUSES)
    row = connection.execute(
        f"""
        SELECT 1
        FROM contacts c
        JOIN source_assertions a
          ON a.entity_type = 'contact' AND a.entity_id = c.contact_id
        JOIN sources s ON s.source_id = a.source_id
        WHERE c.organization_id = ?
          AND c.verification_status = 'verified'
          AND c.do_not_contact = 0
          AND s.source_strength >= ?
          AND LOWER(s.validation_status) IN ({placeholders})
          AND LOWER(a.validation_status) IN ({placeholders})
          AND a.field_name IN ('public_email', 'public_phone', 'professional_url')
          AND (a.freshness_expires_at IS NULL OR a.freshness_expires_at >= date('now'))
        LIMIT 1
        """,
        (organization_id, minimum_strength, *ELIGIBLE_STATUSES, *ELIGIBLE_STATUSES),
    ).fetchone()
    return row is not None


def _upcoming_events(
    connection: sqlite3.Connection, organization_id: str, minimum_strength: int
) -> int:
    placeholders = ", ".join("?" for _ in ELIGIBLE_STATUSES)
    row = connection.execute(
        f"""
        SELECT COUNT(DISTINCT e.event_id) AS event_count
        FROM events e
        JOIN source_assertions a
          ON a.entity_type = 'event' AND a.entity_id = e.event_id
        JOIN sources s ON s.source_id = a.source_id
        WHERE e.organization_id = ?
          AND date(e.starts_at) >= date(?)
          AND e.freshness_status = 'upcoming'
          AND s.source_strength >= ?
          AND LOWER(s.validation_status) IN ({placeholders})
          AND LOWER(a.validation_status) IN ({placeholders})
        """,
        (
            organization_id,
            date.today().isoformat(),
            minimum_strength,
            *ELIGIBLE_STATUSES,
            *ELIGIBLE_STATUSES,
        ),
    ).fetchone()
    return int(row["event_count"] or 0)


def score_all_organizations(
    connection: sqlite3.Connection, settings: Settings
) -> int:
    organizations = connection.execute("SELECT * FROM organizations").fetchall()
    scored = 0
    for organization in organizations:
        assertions = _eligible_assertions(
            connection,
            organization["organization_id"],
            settings.minimum_scoring_source_strength,
        )
        if not assertions:
            connection.execute(
                """
                UPDATE organizations
                SET data_quality_score = NULL, raw_prospect_score = NULL,
                    adjusted_priority = NULL,
                    score_explanation = 'Not scored: no verified Strength 3 or better source assertions.',
                    review_status = CASE WHEN review_status = 'pending' THEN 'research_only' ELSE review_status END,
                    updated_at = ?
                WHERE organization_id = ?
                """,
                (utc_now(), organization["organization_id"]),
            )
            continue

        strengths = tuple(int(row["source_strength"]) for row in assertions)
        sourced_fields = {
            row["field_name"] for row in assertions if row["field_name"] in MATERIAL_FIELDS
        }
        today = date.today().isoformat()
        current_count = sum(
            not row["freshness_expires_at"] or row["freshness_expires_at"] >= today
            for row in assertions
        )
        conflict_count = connection.execute(
            """
            SELECT COUNT(DISTINCT conflict_group) AS conflicts
            FROM source_assertions
            WHERE entity_type = 'organization' AND entity_id = ?
              AND conflict_group IS NOT NULL
            """,
            (organization["organization_id"],),
        ).fetchone()["conflicts"]
        census = None
        if organization["census_geography_id"]:
            census = connection.execute(
                "SELECT * FROM census_geographies WHERE census_geography_id = ?",
                (organization["census_geography_id"],),
            ).fetchone()
        relevant_workforce_pct = (
            census["relevant_workforce_pct"]
            if census and census["estimate_stability"] != "unstable"
            else None
        )
        osha_context = connection.execute(
            "SELECT 1 FROM osha_metrics WHERE organization_id = ? LIMIT 1",
            (organization["organization_id"],),
        ).fetchone()

        features = ProspectFeatures(
            organization_type=organization["organization_type"],
            industry=organization["industry"],
            estimated_reach=organization["estimated_reach"],
            current_role_titles=_current_role_titles(
                connection,
                organization["organization_id"],
                settings.minimum_scoring_source_strength,
            ),
            estimated_driving_distance=organization["estimated_driving_distance"],
            public_contact_available=_has_verified_contact(
                connection,
                organization["organization_id"],
                settings.minimum_scoring_source_strength,
            ),
            public_accessibility=bool(organization["public_accessibility"]),
            upcoming_event_count=_upcoming_events(
                connection,
                organization["organization_id"],
                settings.minimum_scoring_source_strength,
            ),
            active_program=bool(organization["active_program"]),
            relevant_workforce_pct=relevant_workforce_pct,
            osha_context_available=osha_context is not None,
        )
        quality = DataQualityInputs(
            eligible_source_strengths=strengths,
            material_field_count=len(MATERIAL_FIELDS),
            sourced_material_field_count=len(sourced_fields),
            current_assertion_count=current_count,
            total_assertion_count=len(assertions),
            identity_confidence=organization["entity_match_confidence"] or 0.0,
            conflict_count=int(conflict_count or 0),
        )
        result = score_prospect(features, quality)
        score_id = new_id("score")
        timestamp = utc_now()
        connection.execute(
            """
            INSERT INTO scores(
                score_id, organization_id, scoring_version,
                workforce_relevance, organizational_reach, role_influence,
                proximity, public_accessibility, relationship_potential,
                raw_prospect_score, data_quality_score, adjusted_priority,
                explanation, input_snapshot_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_id,
                organization["organization_id"],
                settings.scoring_version,
                result.workforce_relevance,
                result.organizational_reach,
                result.role_influence,
                result.proximity,
                result.public_accessibility,
                result.relationship_potential,
                result.raw_prospect_score,
                result.data_quality_score,
                result.adjusted_priority,
                result.explanation,
                json.dumps(result.input_snapshot, sort_keys=True),
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE organizations
            SET data_quality_score = ?, raw_prospect_score = ?,
                adjusted_priority = ?, score_explanation = ?, updated_at = ?
            WHERE organization_id = ?
            """,
            (
                result.data_quality_score,
                result.raw_prospect_score,
                result.adjusted_priority,
                result.explanation,
                timestamp,
                organization["organization_id"],
            ),
        )
        scored += 1
    connection.commit()
    return scored
