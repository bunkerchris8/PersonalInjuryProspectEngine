from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from src.config import Settings
from src.ingestion.common import (
    create_assertion,
    finish_ingestion_run,
    queue_research_row,
    start_ingestion_run,
    upsert_source,
    utc_now,
)
from src.ingestion.csv_importer import (
    _assert_and_update_organization,
    _candidate_from_row,
    _find_match,
    _insert_organization,
    _organization_values,
    _upsert_primary_location,
)


CONTRACT_AWARD_TYPE_CODES = ("A", "B", "C", "D")
RESULT_FIELDS = (
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Recipient Location",
    "Start Date",
    "End Date",
    "NAICS",
)


@dataclass
class USAspendingImportStats:
    run_id: str
    pages: int = 0
    rows_seen: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    rows_rejected: int = 0
    organizations_created: int = 0
    organizations_matched: int = 0
    sources_pruned: int = 0
    assertions_pruned: int = 0
    result_cap_reached: bool = False


def default_start_date(settings: Settings, *, as_of: date | None = None) -> date:
    today = as_of or date.today()
    return date(today.year - settings.usaspending_lookback_years, 1, 1)


def _canonical_cities(cities: tuple[str, ...]) -> dict[str, str]:
    return {city.strip().casefold(): city.strip() for city in cities if city.strip()}


def _iso_date(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text[:10]).isoformat()


def _naics(result: dict[str, Any]) -> tuple[str | None, str | None]:
    value = result.get("NAICS")
    if isinstance(value, dict):
        return (
            str(value.get("code") or "").strip() or None,
            str(value.get("description") or "").strip() or None,
        )
    return str(value or "").strip() or None, None


def _recipient_row(
    result: dict[str, Any], city_names: dict[str, str]
) -> tuple[dict[str, str], str, str, str]:
    name = str(result.get("Recipient Name") or "").strip()
    uei = str(result.get("Recipient UEI") or "").strip()
    award_id = str(result.get("Award ID") or "").strip()
    generated_id = str(result.get("generated_internal_id") or "").strip()
    if not name or not uei or not award_id or not generated_id:
        raise ValueError("Award is missing recipient name, UEI, or award identifier.")

    location = result.get("Recipient Location")
    if not isinstance(location, dict):
        raise ValueError("Award is missing its structured recipient location.")
    state = str(location.get("state_code") or "").strip().upper()
    raw_city = str(location.get("city_name") or "").strip()
    city = city_names.get(raw_city.casefold())
    if state != "MA" or not city:
        raise ValueError("Award recipient falls outside the configured Massachusetts cities.")
    street = ", ".join(
        str(location.get(field) or "").strip()
        for field in ("address_line1", "address_line2", "address_line3")
        if str(location.get(field) or "").strip()
    )
    if not street:
        raise ValueError("Award recipient is missing a public business street address.")
    naics_code, industry = _naics(result)
    row = {
        "canonical_name": name,
        "organization_type": "workplace",
        "industry": industry or "",
        "union_affiliation": "",
        "local_number": "",
        # Identifier schemes differ across public systems. UEI is retained as a
        # sourced assertion rather than overloading the legacy identifier column.
        "official_identifier": "",
        "website": "",
        "public_phone": "",
        "public_email": "",
        "street": street,
        "city": city,
        "state": state,
        "zip": str(location.get("zip5") or "").strip(),
        "latitude": "",
        "longitude": "",
        "estimated_reach": "",
        "active_status": "reported",
        "public_accessibility": "0",
        "active_program": "0",
    }
    return row, uei, award_id, naics_code or ""


def _match_by_uei(
    connection: sqlite3.Connection, uei: str
) -> sqlite3.Row | None:
    rows = connection.execute(
        """
        SELECT DISTINCT o.*
        FROM organizations o
        JOIN source_assertions a
          ON a.entity_type = 'organization' AND a.entity_id = o.organization_id
        WHERE a.field_name = 'recipient_uei' AND a.asserted_value = ?
        LIMIT 2
        """,
        (uei,),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def _request_page(
    client: requests.Session,
    settings: Settings,
    *,
    start_date: date,
    end_date: date,
    cities: tuple[str, ...],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    response = client.post(
        settings.usaspending_api_url,
        json={
            "filters": {
                "award_type_codes": list(CONTRACT_AWARD_TYPE_CODES),
                "time_period": [
                    {
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    }
                ],
                "recipient_locations": [
                    {"country": "USA", "state": "MA", "city": city}
                    for city in cities
                ],
            },
            "fields": list(RESULT_FIELDS),
            "limit": page_size,
            "page": page,
            "sort": "Start Date",
            "order": "desc",
        },
        timeout=(15, 90),
        headers={"User-Agent": "BridgewaterProspectEngine/0.2 (local research tool)"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("USAspending returned an unexpected response shape.")
    return payload


def prune_usaspending_sources(
    connection: sqlite3.Connection, *, keep_per_recipient: int = 3
) -> tuple[int, int]:
    """Keep the newest bounded set of award snapshots for each public UEI."""
    if keep_per_recipient < 1:
        raise ValueError("keep_per_recipient must be at least 1.")
    connection.execute("DROP TABLE IF EXISTS temp.prunable_usaspending_sources")
    connection.execute(
        """
        CREATE TEMP TABLE prunable_usaspending_sources AS
        WITH candidates AS (
            SELECT s.source_id, uei.asserted_value AS uei,
                   COALESCE(s.raw_source_identifier, s.source_id) AS award_key,
                   s.publication_or_filing_date, s.retrieval_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY uei.asserted_value,
                                    COALESCE(s.raw_source_identifier, s.source_id)
                       ORDER BY s.retrieval_date DESC, s.source_id DESC
                   ) AS snapshot_rank
            FROM sources s
            JOIN source_assertions uei ON uei.source_id = s.source_id
            WHERE s.source_type = 'public_contract'
              AND uei.entity_type = 'organization'
              AND uei.field_name = 'recipient_uei'
        ),
        ranked_awards AS (
            SELECT source_id, uei,
                   ROW_NUMBER() OVER (
                       PARTITION BY uei
                       ORDER BY COALESCE(
                           publication_or_filing_date, retrieval_date
                       ) DESC, source_id DESC
                   ) AS award_rank
            FROM candidates
            WHERE snapshot_rank = 1
        ),
        kept AS (
            SELECT source_id FROM ranked_awards WHERE award_rank <= ?
        )
        SELECT source_id FROM candidates
        WHERE source_id NOT IN (SELECT source_id FROM kept)
        """,
        (keep_per_recipient,),
    )
    source_count = connection.execute(
        "SELECT COUNT(*) FROM temp.prunable_usaspending_sources"
    ).fetchone()[0]
    assertion_count = connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions
        WHERE source_id IN (SELECT source_id FROM temp.prunable_usaspending_sources)
        """
    ).fetchone()[0]
    connection.execute(
        """
        DELETE FROM source_assertions
        WHERE source_id IN (SELECT source_id FROM temp.prunable_usaspending_sources)
        """
    )
    connection.execute(
        """
        DELETE FROM sources
        WHERE source_id IN (SELECT source_id FROM temp.prunable_usaspending_sources)
        """
    )
    connection.execute("DROP TABLE temp.prunable_usaspending_sources")
    return int(source_count), int(assertion_count)


def import_usaspending_contract_recipients(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    cities: tuple[str, ...] | None = None,
    max_pages: int = 100,
    page_size: int = 100,
    max_awards_per_recipient: int = 3,
    session: requests.Session | None = None,
) -> USAspendingImportStats:
    query_end = end_date or date.today()
    query_start = start_date or default_start_date(settings, as_of=query_end)
    if query_start > query_end:
        raise ValueError("USAspending start date must not be after its end date.")
    if max_pages < 1:
        raise ValueError("USAspending max_pages must be at least 1.")
    if page_size < 1 or page_size > 100:
        raise ValueError("USAspending page_size must be between 1 and 100.")
    if max_awards_per_recipient < 1:
        raise ValueError("max_awards_per_recipient must be at least 1.")
    target_cities = tuple(dict.fromkeys(cities or settings.priority_cities))
    city_names = _canonical_cities(target_cities)
    if not city_names:
        raise ValueError("At least one USAspending recipient city is required.")

    run_id = start_ingestion_run(
        connection,
        "usaspending_contracts",
        settings.usaspending_api_url,
        {
            "start_date": query_start.isoformat(),
            "end_date": query_end.isoformat(),
            "cities": list(target_cities),
            "award_type_codes": list(CONTRACT_AWARD_TYPE_CODES),
        },
    )
    stats = USAspendingImportStats(run_id=run_id)
    client = session or requests.Session()
    seen_awards: set[str] = set()
    recipient_award_counts: dict[str, int] = {}
    retrieval_date = date.today().isoformat()
    try:
        page = 1
        while True:
            payload = _request_page(
                client,
                settings,
                start_date=query_start,
                end_date=query_end,
                cities=target_cities,
                page=page,
                page_size=page_size,
            )
            stats.pages += 1
            for result in payload["results"]:
                stats.rows_seen += 1
                try:
                    generated_id = str(result.get("generated_internal_id") or "").strip()
                    if generated_id in seen_awards:
                        continue
                    row, uei, award_id, naics_code = _recipient_row(result, city_names)
                    seen_awards.add(generated_id)
                    award_count = recipient_award_counts.get(uei, 0)
                    if award_count >= max_awards_per_recipient:
                        stats.rows_skipped += 1
                        continue
                    recipient_award_counts[uei] = award_count + 1
                    award_start = _iso_date(result.get("Start Date"))
                    award_end = _iso_date(result.get("End Date"))
                    metadata = {
                        "source_url": f"https://www.usaspending.gov/award/{generated_id}",
                        "publisher": (
                            "U.S. Department of the Treasury, Bureau of the Fiscal Service"
                        ),
                        "source_title": f"USAspending federal contract {award_id}",
                        "retrieval_date": retrieval_date,
                        "publication_date": award_start,
                        "source_strength": 5,
                        "source_type": "public_contract",
                        "raw_source_identifier": generated_id,
                        "extraction_method": "official_api_json",
                        "validation_status": "verified",
                    }
                    source_id = upsert_source(connection, metadata, run_id)
                    values = _organization_values(row, settings)
                    match = _match_by_uei(connection, uei)
                    confidence = 1.0 if match else 0.0
                    auto_merge = match is not None
                    if match is None:
                        match, confidence, auto_merge = _find_match(
                            connection, _candidate_from_row(row)
                        )
                    is_new = match is None or not auto_merge
                    if is_new:
                        organization_id = _insert_organization(
                            connection,
                            values,
                            confidence if match else 1.0,
                            "needs_review" if match and confidence >= 0.60 else "pending",
                        )
                        stats.organizations_created += 1
                    else:
                        organization_id = match["organization_id"]
                        stats.organizations_matched += 1
                        connection.execute(
                            """
                            UPDATE organizations
                            SET entity_match_confidence = MAX(
                                    COALESCE(entity_match_confidence, 0), ?
                                ),
                                updated_at = ?
                            WHERE organization_id = ?
                            """,
                            (confidence, utc_now(), organization_id),
                        )
                    asserted_fields = {
                        field
                        for field in (
                            "canonical_name",
                            "organization_type",
                            "industry",
                            "street",
                            "city",
                            "state",
                            "zip",
                            "active_status",
                        )
                        if row.get(field)
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
                    observed_at = award_start or retrieval_date
                    for field_name, value in {
                        "recipient_uei": uei,
                        "federal_contract_award_id": award_id,
                        "federal_contract_start_date": award_start,
                        "federal_contract_end_date": award_end,
                        "federal_contract_naics": naics_code or None,
                    }.items():
                        if value:
                            create_assertion(
                                connection,
                                source_id=source_id,
                                entity_type="organization",
                                entity_id=organization_id,
                                field_name=field_name,
                                asserted_value=value,
                                observed_at=observed_at,
                                source_type="public_contract",
                                validation_status="verified",
                                structured_field_name=field_name,
                            )
                    stats.rows_imported += 1
                except Exception as exc:
                    queue_research_row(
                        connection,
                        ingestion_run_id=run_id,
                        entity_type="organization",
                        display_name=str(result.get("Recipient Name") or "") or None,
                        reason=f"USAspending import validation failed: {exc}",
                        row={
                            **result,
                            "source_url": settings.usaspending_api_url,
                            "source_strength": 5,
                        },
                    )
                    stats.rows_rejected += 1

            has_next = bool(payload.get("page_metadata", {}).get("hasNext"))
            if not has_next:
                if page >= max_pages and len(payload["results"]) == page_size:
                    stats.result_cap_reached = True
                break
            if page >= max_pages:
                stats.result_cap_reached = True
                break
            page += 1

        stats.sources_pruned, stats.assertions_pruned = prune_usaspending_sources(
            connection,
            keep_per_recipient=max_awards_per_recipient,
        )
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=stats.rows_seen,
            rows_imported=stats.rows_imported,
            rows_queued=0,
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
            rows_queued=0,
            rows_rejected=stats.rows_rejected,
            error_message=str(exc),
        )
        raise
