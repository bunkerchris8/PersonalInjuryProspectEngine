from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import date

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
from src.normalization.geography import calculate_distance


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    matched_address: str
    tiger_line_id: str
    response_url: str


def _batch_url(settings: Settings) -> str:
    single_suffix = "/locations/address"
    if settings.census_geocoder_url.endswith(single_suffix):
        return settings.census_geocoder_url[: -len(single_suffix)] + "/locations/addressbatch"
    return settings.census_geocoder_url.rstrip("/") + "/addressbatch"


def geocode_address(
    settings: Settings,
    *,
    street: str,
    city: str,
    state: str,
    zip_code: str | None = None,
    session: requests.Session | None = None,
) -> GeocodeResult | None:
    client = session or requests.Session()
    response = client.get(
        settings.census_geocoder_url,
        params={
            "street": street,
            "city": city,
            "state": state,
            "zip": zip_code or "",
            "benchmark": "Public_AR_Current",
            "format": "json",
        },
        timeout=(10, 30),
        headers={"User-Agent": "BridgewaterProspectEngine/0.1 (local research tool)"},
    )
    response.raise_for_status()
    matches = response.json().get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    match = matches[0]
    coordinates = match["coordinates"]
    return GeocodeResult(
        latitude=float(coordinates["y"]),
        longitude=float(coordinates["x"]),
        matched_address=match["matchedAddress"],
        tiger_line_id=str(match.get("tigerLine", {}).get("tigerLineId", "")),
        response_url=response.url,
    )


def geocode_pending_organizations(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    limit: int = 50,
    session: requests.Session | None = None,
) -> tuple[int, int]:
    run_id = start_ingestion_run(
        connection,
        "census_geocoder",
        settings.census_geocoder_url,
        {"limit": limit},
    )
    rows = connection.execute(
        """
        SELECT organization_id, canonical_name, street, city, state, zip
        FROM organizations
        WHERE latitude IS NULL AND longitude IS NULL
          AND street IS NOT NULL AND city IS NOT NULL AND state IS NOT NULL
        ORDER BY created_at
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    matched = 0
    unmatched = 0
    try:
        for organization in rows:
            result = geocode_address(
                settings,
                street=organization["street"],
                city=organization["city"],
                state=organization["state"],
                zip_code=organization["zip"],
                session=session,
            )
            if result is None:
                queue_research_row(
                    connection,
                    ingestion_run_id=run_id,
                    entity_type="organization",
                    display_name=organization["canonical_name"],
                    reason="Census geocoder returned no address match; location needs review.",
                    row={
                        "source_url": settings.census_geocoder_url,
                        "source_strength": "5",
                        "street": organization["street"],
                        "city": organization["city"],
                        "state": organization["state"],
                        "zip": organization["zip"],
                    },
                )
                unmatched += 1
                continue
            distance = calculate_distance(result.latitude, result.longitude, settings)
            retrieval_date = date.today().isoformat()
            metadata = {
                "source_url": result.response_url,
                "publisher": "U.S. Census Bureau",
                "source_title": "Census Geocoding Services address match",
                "retrieval_date": retrieval_date,
                "publication_date": None,
                "source_strength": 5,
                "source_type": "census_geocoder",
                "raw_source_identifier": result.tiger_line_id,
                "extraction_method": "official_api_json",
                "validation_status": "verified",
            }
            source_id = upsert_source(connection, metadata, run_id)
            values = {
                "latitude": result.latitude,
                "longitude": result.longitude,
                "straight_line_distance": distance.straight_line_miles,
                "estimated_driving_distance": distance.estimated_driving_miles,
                "distance_method": distance.method,
                "geographic_tier": distance.geographic_tier,
            }
            connection.execute(
                """
                UPDATE organizations
                SET latitude = ?, longitude = ?, straight_line_distance = ?,
                    estimated_driving_distance = ?, distance_method = ?,
                    geographic_tier = ?, updated_at = ?
                WHERE organization_id = ?
                """,
                (
                    result.latitude,
                    result.longitude,
                    distance.straight_line_miles,
                    distance.estimated_driving_miles,
                    distance.method,
                    distance.geographic_tier,
                    utc_now(),
                    organization["organization_id"],
                ),
            )
            connection.execute(
                """
                UPDATE locations
                SET latitude = ?, longitude = ?, straight_line_distance = ?,
                    estimated_driving_distance = ?, distance_method = ?,
                    geographic_tier = ?, updated_at = ?
                WHERE organization_id = ? AND is_primary = 1
                """,
                (
                    result.latitude,
                    result.longitude,
                    distance.straight_line_miles,
                    distance.estimated_driving_miles,
                    distance.method,
                    distance.geographic_tier,
                    utc_now(),
                    organization["organization_id"],
                ),
            )
            for field, value in values.items():
                create_assertion(
                    connection,
                    source_id=source_id,
                    entity_type="organization",
                    entity_id=organization["organization_id"],
                    field_name=field,
                    asserted_value=value,
                    observed_at=retrieval_date,
                    source_type="census_geocoder",
                    validation_status="verified",
                    relevant_source_excerpt=result.matched_address,
                    structured_field_name=(
                        "coordinates"
                        if field in {"latitude", "longitude"}
                        else "derived_from_coordinates"
                    ),
                )
            matched += 1
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=len(rows),
            rows_imported=matched,
            rows_queued=unmatched,
            rows_rejected=0,
        )
        return matched, unmatched
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=len(rows),
            rows_imported=matched,
            rows_queued=unmatched,
            rows_rejected=0,
            error_message=str(exc),
        )
        raise


def geocode_pending_organizations_batch(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    limit: int = 1000,
    session: requests.Session | None = None,
) -> tuple[int, int]:
    if limit < 1 or limit > 10_000:
        raise ValueError("Census batch geocoding limit must be from 1 through 10,000.")
    url = _batch_url(settings)
    run_id = start_ingestion_run(
        connection,
        "census_geocoder_batch",
        url,
        {"limit": limit, "benchmark": "Public_AR_Current"},
    )
    rows = connection.execute(
        """
        SELECT organization_id, canonical_name, street, city, state, zip
        FROM organizations
        WHERE latitude IS NULL AND longitude IS NULL
          AND street IS NOT NULL AND city IS NOT NULL AND state IS NOT NULL
        ORDER BY created_at, organization_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    matched = 0
    unmatched = 0
    try:
        request_file = io.StringIO(newline="")
        writer = csv.writer(request_file)
        for organization in rows:
            writer.writerow(
                [
                    organization["organization_id"],
                    organization["street"],
                    organization["city"],
                    organization["state"],
                    organization["zip"] or "",
                ]
            )
        client = session or requests.Session()
        response = client.post(
            url,
            files={
                "addressFile": (
                    "prospect_addresses.csv",
                    request_file.getvalue().encode("utf-8"),
                    "text/csv",
                )
            },
            data={"benchmark": "Public_AR_Current"},
            timeout=(15, 180),
            headers={"User-Agent": "BridgewaterProspectEngine/0.2 (local research tool)"},
        )
        response.raise_for_status()
        result_rows: dict[str, list[str]] = {}
        for result in csv.reader(io.StringIO(response.content.decode("utf-8-sig"))):
            if result:
                result_rows[result[0]] = result

        retrieval_date = date.today().isoformat()
        for organization in rows:
            result = result_rows.get(organization["organization_id"])
            is_match = bool(
                result
                and len(result) >= 7
                and result[2].strip().casefold() == "match"
                and "," in result[5]
            )
            if not is_match:
                queue_research_row(
                    connection,
                    ingestion_run_id=run_id,
                    entity_type="organization",
                    display_name=organization["canonical_name"],
                    reason="Census batch geocoder returned no address match; location needs review.",
                    row={
                        "source_url": url,
                        "source_strength": "5",
                        "street": organization["street"],
                        "city": organization["city"],
                        "state": organization["state"],
                        "zip": organization["zip"],
                    },
                )
                unmatched += 1
                continue

            longitude_text, latitude_text = result[5].split(",", 1)
            geocode = GeocodeResult(
                latitude=float(latitude_text),
                longitude=float(longitude_text),
                matched_address=result[4].strip(),
                tiger_line_id=result[6].strip(),
                response_url=url,
            )
            distance = calculate_distance(geocode.latitude, geocode.longitude, settings)
            metadata = {
                "source_url": geocode.response_url,
                "publisher": "U.S. Census Bureau",
                "source_title": "Census Geocoding Services batch address match",
                "retrieval_date": retrieval_date,
                "publication_date": None,
                "source_strength": 5,
                "source_type": "census_geocoder",
                "raw_source_identifier": geocode.tiger_line_id,
                "extraction_method": "official_batch_api_csv",
                "validation_status": "verified",
            }
            source_id = upsert_source(connection, metadata, run_id)
            values = {
                "latitude": geocode.latitude,
                "longitude": geocode.longitude,
                "straight_line_distance": distance.straight_line_miles,
                "estimated_driving_distance": distance.estimated_driving_miles,
                "distance_method": distance.method,
                "geographic_tier": distance.geographic_tier,
            }
            connection.execute(
                """
                UPDATE organizations
                SET latitude = ?, longitude = ?, straight_line_distance = ?,
                    estimated_driving_distance = ?, distance_method = ?,
                    geographic_tier = ?, updated_at = ?
                WHERE organization_id = ?
                """,
                (
                    geocode.latitude,
                    geocode.longitude,
                    distance.straight_line_miles,
                    distance.estimated_driving_miles,
                    distance.method,
                    distance.geographic_tier,
                    utc_now(),
                    organization["organization_id"],
                ),
            )
            connection.execute(
                """
                UPDATE locations
                SET latitude = ?, longitude = ?, straight_line_distance = ?,
                    estimated_driving_distance = ?, distance_method = ?,
                    geographic_tier = ?, updated_at = ?
                WHERE organization_id = ? AND is_primary = 1
                """,
                (
                    geocode.latitude,
                    geocode.longitude,
                    distance.straight_line_miles,
                    distance.estimated_driving_miles,
                    distance.method,
                    distance.geographic_tier,
                    utc_now(),
                    organization["organization_id"],
                ),
            )
            for field, value in values.items():
                create_assertion(
                    connection,
                    source_id=source_id,
                    entity_type="organization",
                    entity_id=organization["organization_id"],
                    field_name=field,
                    asserted_value=value,
                    observed_at=retrieval_date,
                    source_type="census_geocoder",
                    validation_status="verified",
                    relevant_source_excerpt=geocode.matched_address,
                    structured_field_name=(
                        "coordinates"
                        if field in {"latitude", "longitude"}
                        else "derived_from_coordinates"
                    ),
                )
            matched += 1

        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=len(rows),
            rows_imported=matched,
            rows_queued=unmatched,
            rows_rejected=0,
        )
        return matched, unmatched
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=len(rows),
            rows_imported=matched,
            rows_queued=unmatched,
            rows_rejected=0,
            error_message=str(exc),
        )
        raise
