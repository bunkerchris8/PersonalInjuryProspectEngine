from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Sequence

from src.ingestion.common import new_id, utc_now
from src.presentation import criteria_fulfilled_label, format_address


EXPORT_FIELD_LABELS = {
    "prospect_name": "Prospect name",
    "prospect_type": "Prospect type",
    "industry": "Industry",
    "union_affiliation": "Union affiliation",
    "local_number": "Local number",
    "full_address": "Full address",
    "street": "Street",
    "city": "City",
    "state": "State",
    "zip": "ZIP",
    "organization_phone": "Organization phone",
    "organization_email": "Organization email",
    "website": "Website",
    "contact_name": "Contact name",
    "contact_role": "Contact role",
    "contact_phone": "Contact phone",
    "contact_email": "Contact email",
    "estimated_driving_distance": "Estimated driving miles",
    "geographic_tier": "Geographic tier",
    "criteria_fulfilled": "Criteria fulfilled",
    "criteria_score": "Criteria score",
    "raw_score": "Raw prospect score",
    "adjusted_priority": "Adjusted priority",
    "score_explanation": "Score explanation",
    "review_status": "Review status",
}

DEFAULT_EXPORT_FIELDS = (
    "prospect_name",
    "full_address",
    "organization_phone",
    "organization_email",
    "contact_name",
    "contact_role",
    "contact_phone",
    "contact_email",
)


def fetch_ranked_prospects(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        WITH ranked_contacts AS (
            SELECT
                c.organization_id,
                c.display_name AS primary_contact_name,
                r.role_title AS primary_contact_role,
                c.public_phone AS primary_contact_phone,
                c.public_email AS primary_contact_email,
                ROW_NUMBER() OVER (
                    PARTITION BY c.organization_id
                    ORDER BY
                        CASE WHEN r.current_status = 'current' THEN 0 ELSE 1 END,
                        CASE WHEN NULLIF(TRIM(c.public_phone), '') IS NOT NULL
                                   OR NULLIF(TRIM(c.public_email), '') IS NOT NULL
                             THEN 0 ELSE 1 END,
                        COALESCE(r.role_title, ''),
                        COALESCE(c.display_name, '')
                ) AS contact_rank
            FROM contacts c
            LEFT JOIN roles r ON r.contact_id = c.contact_id
            WHERE c.verification_status = 'verified' AND c.do_not_contact = 0
        )
        SELECT
            o.*,
            contact.primary_contact_name,
            contact.primary_contact_role,
            contact.primary_contact_phone,
            contact.primary_contact_email,
            COALESCE((
                SELECT MAX(s.source_strength)
                FROM source_assertions a
                JOIN sources s ON s.source_id = a.source_id
                WHERE a.entity_type = 'organization' AND a.entity_id = o.organization_id
            ), 0) AS max_source_strength,
            COALESCE((
                SELECT COUNT(*) FROM contacts c
                WHERE c.organization_id = o.organization_id
                  AND c.verification_status = 'verified' AND c.do_not_contact = 0
            ), 0) AS verified_contact_count,
            COALESCE((
                SELECT COUNT(*) FROM events e
                WHERE e.organization_id = o.organization_id
                  AND e.freshness_status = 'upcoming'
            ), 0) AS upcoming_event_count,
            COALESCE((
                SELECT COUNT(DISTINCT conflict_group) FROM source_assertions a
                WHERE a.entity_type = 'organization' AND a.entity_id = o.organization_id
                  AND a.conflict_group IS NOT NULL
            ), 0) AS conflict_count,
            CASE WHEN EXISTS (
                SELECT 1 FROM source_assertions a
                WHERE a.entity_type = 'organization' AND a.entity_id = o.organization_id
                  AND a.freshness_expires_at < date('now')
            ) THEN 1 ELSE 0 END AS has_stale_information
        FROM organizations o
        LEFT JOIN ranked_contacts contact
          ON contact.organization_id = o.organization_id
         AND contact.contact_rank = 1
        ORDER BY o.adjusted_priority DESC, o.data_quality_score DESC, o.canonical_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_organization_detail(
    connection: sqlite3.Connection, organization_id: str
) -> dict[str, object] | None:
    row = connection.execute(
        "SELECT * FROM organizations WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    return dict(row) if row else None


def fetch_contacts(connection: sqlite3.Connection, organization_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT c.*, r.role_title, r.role_date, r.filing_year, r.current_status
        FROM contacts c
        LEFT JOIN roles r ON r.contact_id = c.contact_id
        WHERE c.organization_id = ?
        ORDER BY r.current_status = 'current' DESC, r.role_title, c.display_name
        """,
        (organization_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_events(connection: sqlite3.Connection, organization_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT * FROM events WHERE organization_id = ?
        ORDER BY starts_at DESC
        """,
        (organization_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_provenance(connection: sqlite3.Connection, organization_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT a.field_name, a.asserted_value, a.validation_status,
               a.observed_at, a.freshness_expires_at, a.conflict_group,
               s.source_url, s.publisher, s.dataset_or_page_title,
               s.retrieval_date, s.publication_or_filing_date,
               s.source_strength, s.source_type, s.raw_source_identifier,
               s.extraction_method
        FROM source_assertions a
        JOIN sources s ON s.source_id = a.source_id
        WHERE (a.entity_type = 'organization' AND a.entity_id = ?)
           OR (a.entity_type = 'contact' AND a.entity_id IN (
                SELECT contact_id FROM contacts WHERE organization_id = ?
           ))
           OR (a.entity_type = 'role' AND a.entity_id IN (
                SELECT role_id FROM roles WHERE organization_id = ?
           ))
           OR (a.entity_type = 'event' AND a.entity_id IN (
                SELECT event_id FROM events WHERE organization_id = ?
           ))
        ORDER BY s.source_strength DESC, s.retrieval_date DESC, a.field_name
        """,
        (organization_id, organization_id, organization_id, organization_id),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_latest_score_components(
    connection: sqlite3.Connection, organization_id: str
) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT * FROM scores WHERE organization_id = ?
        ORDER BY scored_at DESC, rowid DESC LIMIT 1
        """,
        (organization_id,),
    ).fetchone()
    return dict(row) if row else None


def record_review(
    connection: sqlite3.Connection,
    organization_id: str,
    decision: str,
    *,
    reviewer: str = "",
    notes: str = "",
    ethics_review_completed: bool = False,
) -> str:
    if decision not in {"pending", "approved", "rejected"}:
        raise ValueError("Review decision must be pending, approved, or rejected.")
    organization = connection.execute(
        "SELECT do_not_contact FROM organizations WHERE organization_id = ?",
        (organization_id,),
    ).fetchone()
    if organization is None:
        raise ValueError("Organization not found.")
    active_suppression = connection.execute(
        """
        SELECT 1 FROM suppressions
        WHERE entity_type = 'organization' AND entity_id = ? AND active = 1
        LIMIT 1
        """,
        (organization_id,),
    ).fetchone()
    if decision == "approved":
        if organization["do_not_contact"] or active_suppression:
            raise ValueError("A suppressed organization cannot be approved for outreach.")
        if not ethics_review_completed:
            raise ValueError("Approval requires a documented human ethics review.")
    review_id = new_id("review")
    timestamp = utc_now()
    connection.execute(
        """
        INSERT INTO outreach_reviews(
            review_id, organization_id, decision, reviewer, review_notes,
            ethics_review_completed, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            organization_id,
            decision,
            reviewer or None,
            notes or None,
            int(ethics_review_completed),
            timestamp,
        ),
    )
    connection.execute(
        "UPDATE organizations SET review_status = ?, updated_at = ? WHERE organization_id = ?",
        (decision, timestamp, organization_id),
    )
    connection.commit()
    return review_id


def set_organization_suppression(
    connection: sqlite3.Connection,
    organization_id: str,
    *,
    active: bool,
    reason: str,
) -> None:
    timestamp = utc_now()
    if active:
        if not reason.strip():
            raise ValueError("A suppression reason is required.")
        existing = connection.execute(
            """
            SELECT suppression_id FROM suppressions
            WHERE entity_type = 'organization' AND entity_id = ? AND active = 1
            LIMIT 1
            """,
            (organization_id,),
        ).fetchone()
        if not existing:
            connection.execute(
                """
                INSERT INTO suppressions(
                    suppression_id, entity_type, entity_id, reason, active, suppressed_at
                ) VALUES (?, 'organization', ?, ?, 1, ?)
                """,
                (new_id("suppress"), organization_id, reason.strip(), timestamp),
            )
        connection.execute(
            """
            UPDATE organizations
            SET do_not_contact = 1, review_status = 'suppressed', updated_at = ?
            WHERE organization_id = ?
            """,
            (timestamp, organization_id),
        )
    else:
        connection.execute(
            """
            UPDATE suppressions SET active = 0, lifted_at = ?
            WHERE entity_type = 'organization' AND entity_id = ? AND active = 1
            """,
            (timestamp, organization_id),
        )
        connection.execute(
            """
            UPDATE organizations
            SET do_not_contact = 0, review_status = 'pending', updated_at = ?
            WHERE organization_id = ?
            """,
            (timestamp, organization_id),
        )
    connection.commit()


def fetch_approved_prospects_for_export(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        WITH ranked_contacts AS (
            SELECT
                c.organization_id,
                c.display_name AS contact_name,
                r.role_title AS contact_role,
                c.public_phone AS contact_phone,
                c.public_email AS contact_email,
                ROW_NUMBER() OVER (
                    PARTITION BY c.organization_id
                    ORDER BY
                        CASE WHEN r.current_status = 'current' THEN 0 ELSE 1 END,
                        CASE WHEN NULLIF(TRIM(c.public_phone), '') IS NOT NULL
                                   OR NULLIF(TRIM(c.public_email), '') IS NOT NULL
                             THEN 0 ELSE 1 END,
                        COALESCE(r.role_title, ''),
                        COALESCE(c.display_name, '')
                ) AS contact_rank
            FROM contacts c
            LEFT JOIN roles r
              ON r.contact_id = c.contact_id AND r.current_status = 'current'
            WHERE c.verification_status = 'verified' AND c.do_not_contact = 0
        )
        SELECT
               o.organization_id,
               o.canonical_name AS prospect_name,
               o.organization_type AS prospect_type,
               o.industry,
               o.union_affiliation,
               o.local_number,
               o.website,
               o.public_phone AS organization_phone,
               o.public_email AS organization_email,
               o.street,
               o.city,
               o.state,
               o.zip,
               o.estimated_driving_distance,
               o.geographic_tier,
               o.raw_prospect_score AS raw_score,
               o.data_quality_score AS criteria_score,
               o.adjusted_priority,
               o.score_explanation,
               o.review_status,
               contact.contact_name,
               contact.contact_role,
               contact.contact_email,
               contact.contact_phone
        FROM organizations o
        LEFT JOIN ranked_contacts contact
          ON contact.organization_id = o.organization_id
         AND contact.contact_rank = 1
        WHERE o.review_status = 'approved' AND o.do_not_contact = 0
          AND NOT EXISTS (
              SELECT 1 FROM suppressions s
              WHERE s.entity_type = 'organization' AND s.entity_id = o.organization_id
                AND s.active = 1
          )
          AND EXISTS (
              SELECT 1 FROM outreach_reviews review
              WHERE review.organization_id = o.organization_id
                AND review.decision = 'approved'
                AND review.ethics_review_completed = 1
          )
        ORDER BY o.adjusted_priority DESC, o.canonical_name
        """
    ).fetchall()

    results = []
    for row in rows:
        record = dict(row)
        record["full_address"] = format_address(
            record.get("street"),
            record.get("city"),
            record.get("state"),
            record.get("zip"),
        )
        record["criteria_fulfilled"] = criteria_fulfilled_label(
            record.get("criteria_score")
        )
        results.append(record)
    return results


def approved_prospects_csv(
    connection: sqlite3.Connection,
    fields: Sequence[str] | None = None,
    *,
    organization_ids: Sequence[str] | None = None,
) -> bytes:
    selected_fields = tuple(EXPORT_FIELD_LABELS) if fields is None else tuple(fields)
    if not selected_fields:
        raise ValueError("Select at least one CSV field.")
    unknown_fields = [field for field in selected_fields if field not in EXPORT_FIELD_LABELS]
    if unknown_fields:
        raise ValueError(f"Unknown CSV fields: {', '.join(unknown_fields)}")

    rows = fetch_approved_prospects_for_export(connection)
    if organization_ids is not None:
        allowed_ids = set(organization_ids)
        rows = [row for row in rows if row["organization_id"] in allowed_ids]

    output = io.StringIO(newline="")
    headers = [EXPORT_FIELD_LABELS[field] for field in selected_fields]
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                EXPORT_FIELD_LABELS[field]: row.get(field)
                for field in selected_fields
            }
        )
    return output.getvalue().encode("utf-8")
