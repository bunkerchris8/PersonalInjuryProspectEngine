from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from src.config import Settings
from src.ingestion.common import (
    create_assertion,
    finish_ingestion_run,
    stable_id,
    start_ingestion_run,
    upsert_source,
)
from src.normalization.entities import normalize_name


AGE_40_PLUS_VARIABLES = tuple(
    [f"B01001_{index:03d}" for index in range(14, 26)]
    + [f"B01001_{index:03d}" for index in range(38, 50)]
)
EMPLOYED_45_PLUS_VARIABLES = (
    "B23001_047",
    "B23001_054",
    "B23001_061",
    "B23001_068",
    "B23001_075",
    "B23001_082",
    "B23001_089",
    "B23001_135",
    "B23001_142",
    "B23001_149",
    "B23001_156",
    "B23001_163",
    "B23001_170",
    "B23001_177",
)


def _get_rows(
    session: requests.Session,
    url: str,
    variables: list[str],
    api_key: str,
) -> tuple[list[dict[str, str]], str]:
    response = session.get(
        url,
        params={
            "get": ",".join(variables),
            "for": "place:*",
            "in": "state:25",
            "key": api_key,
        },
        timeout=(10, 60),
        headers={"User-Agent": "BridgewaterProspectEngine/0.1 (local research tool)"},
    )
    response.raise_for_status()
    payload = response.json()
    headers = payload[0]
    parts = urlsplit(response.url)
    redacted_query = urlencode(
        [(key, "REDACTED" if key.lower() == "key" else value) for key, value in parse_qsl(parts.query)]
    )
    safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))
    return [dict(zip(headers, values, strict=True)) for values in payload[1:]], safe_url


def _number(row: dict[str, str], field: str) -> float | None:
    value = row.get(field)
    if value in (None, "", "-666666666", "-999999999"):
        return None
    return float(value)


def _sum_estimates_and_moe(
    row: dict[str, str], variable_bases: tuple[str, ...]
) -> tuple[float | None, float | None]:
    estimates = [_number(row, f"{base}E") for base in variable_bases]
    margins = [_number(row, f"{base}M") for base in variable_bases]
    if any(value is None for value in estimates):
        return None, None
    estimate = sum(value for value in estimates if value is not None)
    available_margins = [value for value in margins if value is not None]
    margin = math.sqrt(sum(value**2 for value in available_margins)) if available_margins else None
    return estimate, margin


def _place_label(name: str) -> str:
    first = name.split(",", 1)[0]
    for suffix in (" city", " town", " CDP"):
        if first.lower().endswith(suffix.lower()):
            return first[: -len(suffix)]
    return first


def _stability(margins: dict[str, float | None]) -> str:
    ratios = [
        margins.get("population_age_40_plus_relative_moe"),
        margins.get("workers_age_45_plus_relative_moe"),
    ]
    percentage_margins = [
        margins.get("construction_maintenance_pct_moe"),
        margins.get("production_transportation_pct_moe"),
    ]
    if all(value is not None and value <= 0.20 for value in ratios) and all(
        value is not None and value <= 5.0 for value in percentage_margins
    ):
        return "stable"
    if all(value is not None and value <= 0.40 for value in ratios) and all(
        value is not None and value <= 10.0 for value in percentage_margins
    ):
        return "caution"
    return "unstable"


def import_acs_massachusetts_places(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    session: requests.Session | None = None,
) -> int:
    if not settings.census_api_key:
        raise ValueError(
            "CENSUS_API_KEY is required for ACS queries. Add it to .env; the geocoder remains key-free."
        )
    run_id = start_ingestion_run(
        connection,
        "census_acs",
        settings.census_acs_base_url,
        {"vintage": settings.census_acs_year, "geography": "Massachusetts places"},
    )
    client = session or requests.Session()
    imported = 0
    try:
        profile_variables = [
            "NAME",
            "DP03_0026E",
            "DP03_0030PE",
            "DP03_0030PM",
            "DP03_0031PE",
            "DP03_0031PM",
        ]
        age_variables = ["NAME", "B01001_001E"] + [
            f"{base}{suffix}"
            for base in AGE_40_PLUS_VARIABLES
            for suffix in ("E", "M")
        ]
        worker_variables = ["NAME", "B23025_004E", "B01002_001E"] + [
            f"{base}{suffix}"
            for base in EMPLOYED_45_PLUS_VARIABLES
            for suffix in ("E", "M")
        ]
        profile_rows, profile_url = _get_rows(
            client,
            f"{settings.census_acs_base_url}/profile",
            profile_variables,
            settings.census_api_key,
        )
        age_rows, age_url = _get_rows(
            client,
            settings.census_acs_base_url,
            age_variables,
            settings.census_api_key,
        )
        worker_rows, worker_url = _get_rows(
            client,
            settings.census_acs_base_url,
            worker_variables,
            settings.census_api_key,
        )
        retrieval_date = date.today().isoformat()
        metadata = {
            "source_url": settings.census_acs_base_url,
            "publisher": "U.S. Census Bureau",
            "source_title": f"{settings.census_acs_year} ACS 5-Year Estimates",
            "retrieval_date": retrieval_date,
            "publication_date": None,
            "source_strength": 5,
            "source_type": "census_acs",
            "raw_source_identifier": json.dumps(
                {"profile_query": profile_url, "age_query": age_url, "worker_query": worker_url},
                sort_keys=True,
            ),
            "extraction_method": "official_api_json",
            "validation_status": "verified",
        }
        source_id = upsert_source(connection, metadata, run_id)
        key = lambda row: (row["state"], row["place"])
        profile_map = {key(row): row for row in profile_rows}
        age_map = {key(row): row for row in age_rows}
        worker_map = {key(row): row for row in worker_rows}
        for geography_key in sorted(profile_map.keys() & age_map.keys() & worker_map.keys()):
            profile = profile_map[geography_key]
            age = age_map[geography_key]
            worker = worker_map[geography_key]
            age_count, age_moe = _sum_estimates_and_moe(age, AGE_40_PLUS_VARIABLES)
            worker_count, worker_moe = _sum_estimates_and_moe(
                worker, EMPLOYED_45_PLUS_VARIABLES
            )
            population = _number(age, "B01001_001E")
            employed = _number(worker, "B23025_004E")
            age_pct = age_count / population * 100 if age_count is not None and population else None
            worker_pct = worker_count / employed * 100 if worker_count is not None and employed else None
            construction_pct = _number(profile, "DP03_0030PE")
            production_pct = _number(profile, "DP03_0031PE")
            relevant_pct = (
                construction_pct + production_pct
                if construction_pct is not None and production_pct is not None
                else None
            )
            margins = {
                "population_age_40_plus_moe": age_moe,
                "population_age_40_plus_relative_moe": age_moe / age_count if age_moe is not None and age_count else None,
                "workers_age_45_plus_moe": worker_moe,
                "workers_age_45_plus_relative_moe": worker_moe / worker_count if worker_moe is not None and worker_count else None,
                "construction_maintenance_pct_moe": _number(profile, "DP03_0030PM"),
                "production_transportation_pct_moe": _number(profile, "DP03_0031PM"),
            }
            stability = _stability(margins)
            geography_id = stable_id(
                "geo", "place", geography_key[0], geography_key[1], settings.census_acs_year
            )
            values = {
                "population": int(population) if population is not None else None,
                "labor_force_size": int(employed) if employed is not None else None,
                "median_age": _number(worker, "B01002_001E"),
                "population_age_40_plus_pct": round(age_pct, 2) if age_pct is not None else None,
                "workers_age_45_plus_pct": round(worker_pct, 2) if worker_pct is not None else None,
                "construction_maintenance_pct": construction_pct,
                "production_transportation_pct": production_pct,
                "relevant_workforce_pct": relevant_pct,
            }
            connection.execute(
                """
                INSERT INTO census_geographies(
                    census_geography_id, geography_type, geography_name,
                    state_fips, place_fips, acs_vintage, population,
                    labor_force_size, median_age, population_age_40_plus_pct,
                    workers_age_45_plus_pct, construction_maintenance_pct,
                    production_transportation_pct, relevant_workforce_pct,
                    margins_of_error_json, estimate_stability, source_id, retrieved_at
                ) VALUES (?, 'place', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(census_geography_id) DO UPDATE SET
                    population = excluded.population,
                    labor_force_size = excluded.labor_force_size,
                    median_age = excluded.median_age,
                    population_age_40_plus_pct = excluded.population_age_40_plus_pct,
                    workers_age_45_plus_pct = excluded.workers_age_45_plus_pct,
                    construction_maintenance_pct = excluded.construction_maintenance_pct,
                    production_transportation_pct = excluded.production_transportation_pct,
                    relevant_workforce_pct = excluded.relevant_workforce_pct,
                    margins_of_error_json = excluded.margins_of_error_json,
                    estimate_stability = excluded.estimate_stability,
                    source_id = excluded.source_id,
                    retrieved_at = excluded.retrieved_at
                """,
                (
                    geography_id,
                    profile["NAME"],
                    geography_key[0],
                    geography_key[1],
                    settings.census_acs_year,
                    values["population"],
                    values["labor_force_size"],
                    values["median_age"],
                    values["population_age_40_plus_pct"],
                    values["workers_age_45_plus_pct"],
                    values["construction_maintenance_pct"],
                    values["production_transportation_pct"],
                    values["relevant_workforce_pct"],
                    json.dumps(margins, sort_keys=True),
                    stability,
                    source_id,
                    retrieval_date,
                ),
            )
            for field, value in values.items():
                if value is not None:
                    create_assertion(
                        connection,
                        source_id=source_id,
                        entity_type="census_geography",
                        entity_id=geography_id,
                        field_name=field,
                        asserted_value=value,
                        observed_at=retrieval_date,
                        source_type="census_acs",
                        validation_status="verified" if stability != "unstable" else "unverified",
                        structured_field_name=field,
                    )
            for field, value in {
                "margins_of_error_json": json.dumps(margins, sort_keys=True),
                "estimate_stability": stability,
                "acs_vintage": settings.census_acs_year,
            }.items():
                create_assertion(
                    connection,
                    source_id=source_id,
                    entity_type="census_geography",
                    entity_id=geography_id,
                    field_name=field,
                    asserted_value=value,
                    observed_at=retrieval_date,
                    source_type="census_acs",
                    validation_status="verified",
                    structured_field_name=field,
                )
            place_name = normalize_name(_place_label(profile["NAME"]))
            organizations = connection.execute(
                "SELECT organization_id, city FROM organizations WHERE state = 'MA' AND city IS NOT NULL"
            ).fetchall()
            for organization in organizations:
                if normalize_name(organization["city"]) == place_name:
                    connection.execute(
                        "UPDATE organizations SET census_geography_id = ? WHERE organization_id = ?",
                        (geography_id, organization["organization_id"]),
                    )
            imported += 1
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=len(profile_rows),
            rows_imported=imported,
            rows_queued=0,
            rows_rejected=len(profile_rows) - imported,
        )
        return imported
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=0,
            rows_imported=imported,
            rows_queued=0,
            rows_rejected=0,
            error_message=str(exc),
        )
        raise
