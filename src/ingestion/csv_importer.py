from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings
from src.ingestion.common import (
    create_assertion,
    finish_ingestion_run,
    new_id,
    observed_date,
    parse_bool,
    parse_float,
    parse_int,
    queue_research_row,
    source_metadata_from_row,
    stable_id,
    start_ingestion_run,
    upsert_source,
    utc_now,
)
from src.normalization.entities import OrganizationCandidate, match_organizations, normalize_name
from src.normalization.geography import calculate_distance
from src.validation.privacy import validate_contact_row, validate_headers
from src.validation.sources import (
    contact_source_requirement_met,
    event_freshness,
    evaluate_freshness,
)


SOURCE_COLUMNS = {
    "source_url",
    "publisher",
    "source_title",
    "retrieval_date",
    "publication_date",
    "source_strength",
    "source_type",
    "raw_source_identifier",
    "extraction_method",
    "validation_status",
}

ORGANIZATION_FIELDS = {
    "canonical_name",
    "organization_type",
    "industry",
    "union_affiliation",
    "local_number",
    "official_identifier",
    "website",
    "public_phone",
    "public_email",
    "street",
    "city",
    "state",
    "zip",
    "latitude",
    "longitude",
    "estimated_reach",
    "active_status",
    "public_accessibility",
    "active_program",
}


@dataclass
class ImportStats:
    run_id: str
    rows_seen: int = 0
    rows_imported: int = 0
    rows_queued: int = 0
    rows_rejected: int = 0


def _require_headers(fieldnames: list[str] | None, required: set[str]) -> list[str]:
    headers = fieldnames or []
    validate_headers(headers)
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
    return headers


def _open_reader(path: str | Path) -> tuple[object, csv.DictReader[str]]:
    handle = Path(path).open("r", encoding="utf-8-sig", newline="")
    return handle, csv.DictReader(handle)


def _candidate_from_row(row: dict[str, str]) -> OrganizationCandidate:
    return OrganizationCandidate(
        name=(row.get("canonical_name") or "").strip(),
        street=(row.get("street") or "").strip() or None,
        city=(row.get("city") or "").strip() or None,
        state=(row.get("state") or "").strip() or None,
        zip_code=(row.get("zip") or "").strip() or None,
        official_identifier=(row.get("official_identifier") or "").strip() or None,
        local_number=(row.get("local_number") or "").strip() or None,
        website=(row.get("website") or "").strip() or None,
        public_phone=(row.get("public_phone") or "").strip() or None,
    )


def _candidate_from_db(row: sqlite3.Row) -> OrganizationCandidate:
    return OrganizationCandidate(
        name=row["canonical_name"],
        street=row["street"],
        city=row["city"],
        state=row["state"],
        zip_code=row["zip"],
        official_identifier=row["official_identifier"],
        local_number=row["local_number"],
        website=row["website"],
        public_phone=row["public_phone"],
    )


def _find_match(
    connection: sqlite3.Connection, candidate: OrganizationCandidate
) -> tuple[sqlite3.Row | None, float, bool]:
    normalized = normalize_name(candidate.name)
    rows = connection.execute(
        """
        SELECT DISTINCT o.*
        FROM organizations o
        LEFT JOIN organization_aliases a ON a.organization_id = o.organization_id
        WHERE o.normalized_name = ? OR a.normalized_alias = ?
           OR ((o.city = ? OR o.zip = ?) AND o.state = ?)
        LIMIT 100
        """,
        (
            normalized,
            normalized,
            candidate.city,
            candidate.zip_code,
            candidate.state,
        ),
    ).fetchall()
    best_row: sqlite3.Row | None = None
    best_confidence = 0.0
    best_auto_merge = False
    for existing in rows:
        result = match_organizations(candidate, _candidate_from_db(existing))
        if result.confidence > best_confidence:
            best_row = existing
            best_confidence = result.confidence
            best_auto_merge = result.auto_merge
    return best_row, best_confidence, best_auto_merge


def _organization_values(row: dict[str, str], settings: Settings) -> dict[str, object]:
    latitude = parse_float(row.get("latitude"))
    longitude = parse_float(row.get("longitude"))
    distance = (
        calculate_distance(latitude, longitude, settings)
        if latitude is not None and longitude is not None
        else None
    )
    return {
        "canonical_name": (row.get("canonical_name") or "").strip(),
        "normalized_name": normalize_name(row.get("canonical_name")),
        "organization_type": (row.get("organization_type") or "").strip(),
        "industry": (row.get("industry") or "").strip() or None,
        "union_affiliation": (row.get("union_affiliation") or "").strip() or None,
        "local_number": (row.get("local_number") or "").strip() or None,
        "official_identifier": (row.get("official_identifier") or "").strip() or None,
        "website": (row.get("website") or "").strip() or None,
        "public_phone": (row.get("public_phone") or "").strip() or None,
        "public_email": (row.get("public_email") or "").strip() or None,
        "street": (row.get("street") or "").strip() or None,
        "city": (row.get("city") or "").strip() or None,
        "state": (row.get("state") or "").strip() or None,
        "zip": (row.get("zip") or "").strip() or None,
        "latitude": latitude,
        "longitude": longitude,
        "straight_line_distance": distance.straight_line_miles if distance else None,
        "estimated_driving_distance": distance.estimated_driving_miles if distance else None,
        "distance_method": distance.method if distance else None,
        "geographic_tier": distance.geographic_tier if distance else None,
        "estimated_reach": parse_int(row.get("estimated_reach")),
        "active_status": (row.get("active_status") or "unknown").strip(),
        "public_accessibility": int(parse_bool(row.get("public_accessibility"))),
        "active_program": int(parse_bool(row.get("active_program"))),
    }


def _insert_organization(
    connection: sqlite3.Connection,
    values: dict[str, object],
    confidence: float,
    review_status: str,
) -> str:
    organization_id = new_id("org")
    timestamp = utc_now()
    fields = list(values)
    connection.execute(
        f"""
        INSERT INTO organizations(
            organization_id, {', '.join(fields)}, entity_match_confidence,
            review_status, created_at, updated_at
        ) VALUES (?, {', '.join('?' for _ in fields)}, ?, ?, ?, ?)
        """,
        (
            organization_id,
            *(values[field] for field in fields),
            confidence,
            review_status,
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO organization_aliases(
            alias_id, organization_id, alias, normalized_alias, alias_type
        ) VALUES (?, ?, ?, ?, 'source_name')
        """,
        (
            new_id("alias"),
            organization_id,
            values["canonical_name"],
            values["normalized_name"],
        ),
    )
    return organization_id


def _assert_and_update_organization(
    connection: sqlite3.Connection,
    *,
    organization_id: str,
    values: dict[str, object],
    asserted_fields: set[str],
    source_id: str,
    metadata: dict[str, object],
    is_new: bool,
) -> None:
    current = connection.execute(
        "SELECT * FROM organizations WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    if current is None:
        raise ValueError("Organization disappeared during import.")

    for field in asserted_fields & ORGANIZATION_FIELDS:
        value = values.get(field)
        if value is None or value == "":
            continue
        existing_value = current[field] if field in current.keys() else None
        conflict_group = None
        assertion_status = str(metadata["validation_status"])
        equivalent_text_fields = {
            "canonical_name",
            "organization_type",
            "industry",
            "union_affiliation",
            "active_status",
            "street",
            "city",
            "state",
        }
        values_match = str(existing_value) == str(value)
        if field in equivalent_text_fields and existing_value not in (None, ""):
            values_match = str(existing_value).strip().casefold() == str(value).strip().casefold()
        if not is_new and existing_value not in (None, "") and not values_match:
            conflict_group = stable_id("conflict", organization_id, field)
            assertion_status = "conflicting"
            connection.execute(
                """
                UPDATE source_assertions
                SET validation_status = 'conflicting', conflict_group = ?
                WHERE entity_type = 'organization' AND entity_id = ?
                  AND field_name = ? AND asserted_value != ?
                """,
                (conflict_group, organization_id, field, str(value)),
            )
            connection.execute(
                "UPDATE organizations SET review_status = 'needs_review' WHERE organization_id = ?",
                (organization_id,),
            )
        elif existing_value in (None, ""):
            connection.execute(
                f"UPDATE organizations SET {field} = ?, updated_at = ? WHERE organization_id = ?",
                (value, utc_now(), organization_id),
            )
        create_assertion(
            connection,
            source_id=source_id,
            entity_type="organization",
            entity_id=organization_id,
            field_name=field,
            asserted_value=value,
            observed_at=observed_date(metadata),
            source_type=str(metadata["source_type"]),
            validation_status=assertion_status,
            relevant_source_excerpt=None,
            structured_field_name=field,
            conflict_group=conflict_group,
        )


def _upsert_primary_location(
    connection: sqlite3.Connection, organization_id: str, values: dict[str, object]
) -> None:
    if not any(values.get(field) for field in ("street", "city", "state", "zip")):
        return
    existing = connection.execute(
        "SELECT location_id FROM locations WHERE organization_id = ? AND is_primary = 1",
        (organization_id,),
    ).fetchone()
    fields = (
        "street",
        "city",
        "state",
        "zip",
        "latitude",
        "longitude",
        "straight_line_distance",
        "estimated_driving_distance",
        "distance_method",
        "geographic_tier",
    )
    if existing:
        assignments = ", ".join(f"{field} = COALESCE(?, {field})" for field in fields)
        connection.execute(
            f"UPDATE locations SET {assignments}, updated_at = ? WHERE location_id = ?",
            (*(values[field] for field in fields), utc_now(), existing["location_id"]),
        )
    else:
        connection.execute(
            f"""
            INSERT INTO locations(
                location_id, organization_id, {', '.join(fields)}, is_primary
            ) VALUES (?, ?, {', '.join('?' for _ in fields)}, 1)
            """,
            (new_id("loc"), organization_id, *(values[field] for field in fields)),
        )


def import_organizations_csv(
    connection: sqlite3.Connection, path: str | Path, settings: Settings
) -> ImportStats:
    run_id = start_ingestion_run(connection, "organization_csv", str(path))
    stats = ImportStats(run_id)
    handle = None
    try:
        handle, reader = _open_reader(path)
        _require_headers(
            reader.fieldnames,
            {"canonical_name", "organization_type"} | SOURCE_COLUMNS,
        )
        for raw_row in reader:
            stats.rows_seen += 1
            row = {key: (value or "") for key, value in raw_row.items()}
            try:
                if not row["canonical_name"].strip() or not row["organization_type"].strip():
                    raise ValueError("Organization name and type are required.")
                metadata = source_metadata_from_row(row)
                if int(metadata["source_strength"]) == 1:
                    queue_research_row(
                        connection,
                        ingestion_run_id=run_id,
                        entity_type="organization",
                        display_name=row["canonical_name"],
                        reason="Strength 1 sources are quarantined and never scored.",
                        row=row,
                    )
                    stats.rows_queued += 1
                    continue
                source_id = upsert_source(connection, metadata, run_id)
                values = _organization_values(row, settings)
                candidate = _candidate_from_row(row)
                match, confidence, auto_merge = _find_match(connection, candidate)
                is_new = match is None or not auto_merge
                if is_new:
                    review_status = "needs_review" if match and confidence >= 0.60 else "pending"
                    organization_id = _insert_organization(
                        connection,
                        values,
                        confidence if match else 1.0,
                        review_status,
                    )
                else:
                    organization_id = match["organization_id"]
                    connection.execute(
                        """
                        UPDATE organizations
                        SET entity_match_confidence = MAX(COALESCE(entity_match_confidence, 0), ?),
                            updated_at = ?
                        WHERE organization_id = ?
                        """,
                        (confidence, utc_now(), organization_id),
                    )
                configured = {
                    field.strip()
                    for field in (row.get("asserted_fields") or "").split(";")
                    if field.strip()
                }
                asserted_fields = configured or {
                    field for field in ORGANIZATION_FIELDS if row.get(field, "").strip()
                }
                _assert_and_update_organization(
                    connection,
                    organization_id=organization_id,
                    values=values,
                    asserted_fields=asserted_fields,
                    source_id=source_id,
                    metadata=metadata,
                    is_new=is_new,
                )
                _upsert_primary_location(connection, organization_id, values)
                stats.rows_imported += 1
            except Exception as exc:
                queue_research_row(
                    connection,
                    ingestion_run_id=run_id,
                    entity_type="organization",
                    display_name=row.get("canonical_name"),
                    reason=f"Import validation failed: {exc}",
                    row=row,
                )
                stats.rows_rejected += 1
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
        )
        return stats
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
            error_message=str(exc),
        )
        raise
    finally:
        if handle:
            handle.close()


def _organization_id_for_name(connection: sqlite3.Connection, name: str) -> str:
    normalized = normalize_name(name)
    rows = connection.execute(
        """
        SELECT DISTINCT o.organization_id
        FROM organizations o
        LEFT JOIN organization_aliases a ON a.organization_id = o.organization_id
        WHERE o.normalized_name = ? OR a.normalized_alias = ?
        """,
        (normalized, normalized),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"Organization lookup for '{name}' returned {len(rows)} records; resolve it manually."
        )
    return rows[0]["organization_id"]


def _refresh_contact_verification(
    connection: sqlite3.Connection, contact_id: str, *, has_conflict: bool
) -> None:
    if has_conflict:
        status = "conflicting"
    else:
        rows = connection.execute(
            """
            SELECT DISTINCT s.source_id, s.source_strength
            FROM source_assertions a
            JOIN sources s ON s.source_id = a.source_id
            WHERE a.entity_type = 'contact' AND a.entity_id = ?
              AND LOWER(a.validation_status) IN (
                  'verified', 'validated', 'corroborated', 'current'
              )
              AND LOWER(s.validation_status) IN (
                  'verified', 'validated', 'corroborated', 'current'
              )
              AND (a.freshness_expires_at IS NULL OR a.freshness_expires_at >= date('now'))
            """,
            (contact_id,),
        ).fetchall()
        strengths = [int(row["source_strength"]) for row in rows]
        official_current = any(strength >= 4 for strength in strengths)
        status = (
            "verified"
            if contact_source_requirement_met(strengths, official_current)
            else "needs_corroboration"
        )
    connection.execute(
        "UPDATE contacts SET verification_status = ? WHERE contact_id = ?",
        (status, contact_id),
    )


def import_contacts_csv(connection: sqlite3.Connection, path: str | Path) -> ImportStats:
    run_id = start_ingestion_run(connection, "contact_csv", str(path))
    stats = ImportStats(run_id)
    handle = None
    try:
        handle, reader = _open_reader(path)
        _require_headers(
            reader.fieldnames,
            {
                "organization_name",
                "contact_name",
                "contact_scope",
                "is_public_professional",
                "role_title",
                "role_date",
                "filing_year",
            }
            | SOURCE_COLUMNS,
        )
        for raw_row in reader:
            stats.rows_seen += 1
            row = {key: (value or "") for key, value in raw_row.items()}
            try:
                validate_contact_row(row)
                metadata = source_metadata_from_row(row)
                if int(metadata["source_strength"]) == 1:
                    queue_research_row(
                        connection,
                        ingestion_run_id=run_id,
                        entity_type="contact",
                        display_name=row.get("contact_name") or row.get("role_title"),
                        reason="Strength 1 contact information is quarantined.",
                        row=row,
                    )
                    stats.rows_queued += 1
                    continue
                organization_id = _organization_id_for_name(
                    connection, row["organization_name"]
                )
                source_id = upsert_source(connection, metadata, run_id)
                observed = observed_date(metadata)
                freshness = evaluate_freshness(str(metadata["source_type"]), observed)
                role_status = "current" if freshness.status != "stale" else "stale"
                official_current = int(metadata["source_strength"]) >= 4 and role_status == "current"
                verification_status = (
                    "verified"
                    if contact_source_requirement_met(
                        [int(metadata["source_strength"])], official_current
                    )
                    else "needs_corroboration"
                )
                display_name = row["contact_name"].strip() or None
                existing = connection.execute(
                    """
                    SELECT c.*
                    FROM contacts c
                    LEFT JOIN roles r ON r.contact_id = c.contact_id
                    WHERE c.organization_id = ?
                      AND COALESCE(LOWER(c.display_name), '') = COALESCE(LOWER(?), '')
                      AND LOWER(r.role_title) = LOWER(?)
                    LIMIT 1
                    """,
                    (organization_id, display_name, row["role_title"].strip()),
                ).fetchone()
                contact_id = existing["contact_id"] if existing else new_id("contact")
                timestamp = utc_now()
                contact_conflicts: dict[str, str] = {}
                role_conflicts: dict[str, str] = {}
                if existing:
                    connection.execute(
                        """
                        UPDATE contacts
                        SET verification_status = ?, last_verified_at = ?, updated_at = ?
                        WHERE contact_id = ?
                        """,
                        (
                            verification_status,
                            observed,
                            timestamp,
                            contact_id,
                        ),
                    )
                    for field in ("public_email", "public_phone", "professional_url"):
                        incoming_value = row.get(field, "").strip()
                        existing_value = existing[field]
                        if not incoming_value:
                            continue
                        if existing_value in (None, ""):
                            connection.execute(
                                f"UPDATE contacts SET {field} = ? WHERE contact_id = ?",
                                (incoming_value, contact_id),
                            )
                        elif str(existing_value) != incoming_value:
                            conflict_group = stable_id("conflict", contact_id, field)
                            contact_conflicts[field] = conflict_group
                            connection.execute(
                                """
                                UPDATE source_assertions
                                SET validation_status = 'conflicting', conflict_group = ?
                                WHERE entity_type = 'contact' AND entity_id = ?
                                  AND field_name = ? AND asserted_value != ?
                                """,
                                (conflict_group, contact_id, field, incoming_value),
                            )
                            connection.execute(
                                "UPDATE contacts SET verification_status = 'conflicting' WHERE contact_id = ?",
                                (contact_id,),
                            )
                            connection.execute(
                                "UPDATE organizations SET review_status = 'needs_review' WHERE organization_id = ?",
                                (organization_id,),
                            )
                    role = connection.execute(
                        "SELECT * FROM roles WHERE contact_id = ? AND LOWER(role_title) = LOWER(?)",
                        (contact_id, row["role_title"].strip()),
                    ).fetchone()
                    role_id = role["role_id"]
                    for field, incoming_value in {
                        "role_date": row["role_date"].strip() or None,
                        "filing_year": parse_int(row["filing_year"]),
                    }.items():
                        if incoming_value is None:
                            continue
                        existing_value = role[field]
                        if existing_value is None:
                            connection.execute(
                                f"UPDATE roles SET {field} = ? WHERE role_id = ?",
                                (incoming_value, role_id),
                            )
                        elif str(existing_value) != str(incoming_value):
                            conflict_group = stable_id("conflict", role_id, field)
                            role_conflicts[field] = conflict_group
                            connection.execute(
                                """
                                UPDATE source_assertions
                                SET validation_status = 'conflicting', conflict_group = ?
                                WHERE entity_type = 'role' AND entity_id = ?
                                  AND field_name = ? AND asserted_value != ?
                                """,
                                (conflict_group, role_id, field, str(incoming_value)),
                            )
                            connection.execute(
                                "UPDATE organizations SET review_status = 'needs_review' WHERE organization_id = ?",
                                (organization_id,),
                            )
                else:
                    connection.execute(
                        """
                        INSERT INTO contacts(
                            contact_id, organization_id, display_name, contact_scope,
                            public_email, public_phone, professional_url,
                            verification_status, last_verified_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            contact_id,
                            organization_id,
                            display_name,
                            row["contact_scope"].strip(),
                            row.get("public_email", "").strip() or None,
                            row.get("public_phone", "").strip() or None,
                            row.get("professional_url", "").strip() or None,
                            verification_status,
                            observed,
                            timestamp,
                            timestamp,
                        ),
                    )
                    role_id = new_id("role")
                    connection.execute(
                        """
                        INSERT INTO roles(
                            role_id, contact_id, organization_id, role_title,
                            role_date, filing_year, current_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            role_id,
                            contact_id,
                            organization_id,
                            row["role_title"].strip(),
                            row["role_date"].strip() or None,
                            parse_int(row["filing_year"]),
                            role_status,
                        ),
                    )
                assertion_id = create_assertion(
                    connection,
                    source_id=source_id,
                    entity_type="role",
                    entity_id=role_id,
                    field_name="role_title",
                    asserted_value=row["role_title"].strip(),
                    observed_at=observed,
                    source_type=str(metadata["source_type"]),
                    validation_status=str(metadata["validation_status"]),
                    structured_field_name="role_title",
                )
                effective_role_status = "unknown" if role_conflicts else role_status
                connection.execute(
                    "UPDATE roles SET source_assertion_id = ?, current_status = ? WHERE role_id = ?",
                    (assertion_id, effective_role_status, role_id),
                )
                for field, value in {
                    "role_date": row["role_date"].strip() or None,
                    "filing_year": parse_int(row["filing_year"]),
                }.items():
                    if value is not None:
                        create_assertion(
                            connection,
                            source_id=source_id,
                            entity_type="role",
                            entity_id=role_id,
                            field_name=field,
                            asserted_value=value,
                            observed_at=observed,
                            source_type=str(metadata["source_type"]),
                            validation_status=(
                                "conflicting"
                                if field in role_conflicts
                                else str(metadata["validation_status"])
                            ),
                            structured_field_name=field,
                            conflict_group=role_conflicts.get(field),
                        )
                for field in ("display_name", "public_email", "public_phone", "professional_url"):
                    row_field = "contact_name" if field == "display_name" else field
                    if row.get(row_field, "").strip():
                        create_assertion(
                            connection,
                            source_id=source_id,
                            entity_type="contact",
                            entity_id=contact_id,
                            field_name=field,
                            asserted_value=row[row_field].strip(),
                            observed_at=observed,
                            source_type=str(metadata["source_type"]),
                            validation_status=(
                                "conflicting"
                                if field in contact_conflicts
                                else str(metadata["validation_status"])
                            ),
                            structured_field_name=row_field,
                            conflict_group=contact_conflicts.get(field),
                        )
                _refresh_contact_verification(
                    connection,
                    contact_id,
                    has_conflict=bool(contact_conflicts or role_conflicts),
                )
                stats.rows_imported += 1
            except Exception as exc:
                queue_research_row(
                    connection,
                    ingestion_run_id=run_id,
                    entity_type="contact",
                    display_name=row.get("contact_name") or row.get("role_title"),
                    reason=f"Import validation failed: {exc}",
                    row=row,
                )
                stats.rows_rejected += 1
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
        )
        return stats
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
            error_message=str(exc),
        )
        raise
    finally:
        if handle:
            handle.close()


def import_events_csv(connection: sqlite3.Connection, path: str | Path) -> ImportStats:
    run_id = start_ingestion_run(connection, "event_csv", str(path))
    stats = ImportStats(run_id)
    handle = None
    try:
        handle, reader = _open_reader(path)
        _require_headers(
            reader.fieldnames,
            {"organization_name", "event_name", "starts_at"} | SOURCE_COLUMNS,
        )
        for raw_row in reader:
            stats.rows_seen += 1
            row = {key: (value or "") for key, value in raw_row.items()}
            try:
                metadata = source_metadata_from_row(row)
                if int(metadata["source_strength"]) == 1:
                    queue_research_row(
                        connection,
                        ingestion_run_id=run_id,
                        entity_type="event",
                        display_name=row.get("event_name"),
                        reason="Strength 1 event information is quarantined.",
                        row=row,
                    )
                    stats.rows_queued += 1
                    continue
                organization_id = _organization_id_for_name(
                    connection, row["organization_name"]
                )
                source_id = upsert_source(connection, metadata, run_id)
                event_id = stable_id(
                    "event", organization_id, row["event_name"], row["starts_at"]
                )
                timestamp = utc_now()
                freshness = event_freshness(row["starts_at"])
                event_assertions = {
                    "event_name": row["event_name"].strip(),
                    "event_type": row.get("event_type", "").strip(),
                    "starts_at": row["starts_at"].strip(),
                    "ends_at": row.get("ends_at", "").strip(),
                    "recurrence_text": row.get("recurrence_text", "").strip(),
                    "venue_name": row.get("venue_name", "").strip(),
                    "street": row.get("street", "").strip(),
                    "city": row.get("city", "").strip(),
                    "state": row.get("state", "").strip(),
                    "zip": row.get("zip", "").strip(),
                    "event_url": row.get("event_url", "").strip(),
                    "accessibility_status": row.get(
                        "accessibility_status", "permission_required"
                    ).strip(),
                    "permission_required": int(
                        parse_bool(row.get("permission_required"), True)
                    ),
                }
                event_conflicts: dict[str, str] = {}
                existing_event = connection.execute(
                    "SELECT * FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()
                if existing_event:
                    for field, incoming_value in event_assertions.items():
                        if incoming_value in (None, ""):
                            continue
                        existing_value = existing_event[field]
                        if existing_value in (None, ""):
                            connection.execute(
                                f"UPDATE events SET {field} = ? WHERE event_id = ?",
                                (incoming_value, event_id),
                            )
                        elif str(existing_value) != str(incoming_value):
                            conflict_group = stable_id("conflict", event_id, field)
                            event_conflicts[field] = conflict_group
                            connection.execute(
                                """
                                UPDATE source_assertions
                                SET validation_status = 'conflicting', conflict_group = ?
                                WHERE entity_type = 'event' AND entity_id = ?
                                  AND field_name = ? AND asserted_value != ?
                                """,
                                (conflict_group, event_id, field, str(incoming_value)),
                            )
                            connection.execute(
                                "UPDATE organizations SET review_status = 'needs_review' WHERE organization_id = ?",
                                (organization_id,),
                            )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, organization_id, event_name, event_type,
                        starts_at, ends_at, recurrence_text, venue_name,
                        street, city, state, zip, event_url, accessibility_status,
                        permission_required, freshness_status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        freshness_status = excluded.freshness_status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        event_id,
                        organization_id,
                        row["event_name"].strip(),
                        row.get("event_type", "").strip() or None,
                        row["starts_at"].strip() or None,
                        row.get("ends_at", "").strip() or None,
                        row.get("recurrence_text", "").strip() or None,
                        row.get("venue_name", "").strip() or None,
                        row.get("street", "").strip() or None,
                        row.get("city", "").strip() or None,
                        row.get("state", "").strip() or None,
                        row.get("zip", "").strip() or None,
                        row.get("event_url", "").strip() or None,
                        row.get("accessibility_status", "permission_required").strip(),
                        int(parse_bool(row.get("permission_required"), True)),
                        freshness,
                        timestamp,
                        timestamp,
                    ),
                )
                for field, value in event_assertions.items():
                    if value not in (None, ""):
                        create_assertion(
                            connection,
                            source_id=source_id,
                            entity_type="event",
                            entity_id=event_id,
                            field_name=field,
                            asserted_value=value,
                            observed_at=observed_date(metadata),
                            source_type=str(metadata["source_type"]),
                            validation_status=(
                                "conflicting"
                                if field in event_conflicts
                                else str(metadata["validation_status"])
                            ),
                            structured_field_name=field,
                            conflict_group=event_conflicts.get(field),
                        )
                stats.rows_imported += 1
            except Exception as exc:
                queue_research_row(
                    connection,
                    ingestion_run_id=run_id,
                    entity_type="event",
                    display_name=row.get("event_name"),
                    reason=f"Import validation failed: {exc}",
                    row=row,
                )
                stats.rows_rejected += 1
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
        )
        return stats
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=stats.rows_queued,
            rows_rejected=stats.rows_rejected,
            error_message=str(exc),
        )
        raise
    finally:
        if handle:
            handle.close()
