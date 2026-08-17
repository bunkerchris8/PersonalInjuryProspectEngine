from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

import requests

from src.config import Settings
from src.ingestion.common import (
    create_assertion,
    finish_ingestion_run,
    parse_float,
    parse_int,
    stable_id,
    start_ingestion_run,
    upsert_source,
)
from src.ingestion.csv_importer import (
    ORGANIZATION_FIELDS,
    _assert_and_update_organization,
    _candidate_from_row,
    _find_match,
    _insert_organization,
    _organization_values,
    _upsert_primary_location,
)
from src.validation.privacy import validate_headers


OSHA_REQUIRED_FIELDS = {
    "establishment_name",
    "street_address",
    "city",
    "state",
    "zip_code",
    "year_filing_for",
    "annual_average_employees",
    "total_hours_worked",
    "total_dafw_cases",
    "total_djtr_cases",
    "total_other_cases",
}


@contextmanager
def _zip_csv_stream(archive: str | Path | BinaryIO) -> Iterator[Iterable[str]]:
    with zipfile.ZipFile(archive) as bundle:
        csv_members = [
            member
            for member in bundle.infolist()
            if not member.is_dir() and member.filename.lower().endswith(".csv")
        ]
        if not csv_members:
            raise ValueError("OSHA ZIP archive does not contain a CSV file.")
        summary_members = [
            member
            for member in csv_members
            if "summary" in member.filename.casefold()
            and "case" not in member.filename.casefold()
        ]
        member = max(summary_members or csv_members, key=lambda item: item.file_size)
        with bundle.open(member) as binary_handle:
            with io.TextIOWrapper(
                binary_handle, encoding="utf-8-sig", newline=""
            ) as text_handle:
                yield text_handle


@contextmanager
def _osha_text_stream(
    *, file_path: str | Path | None, url: str | None
) -> Iterator[Iterable[str]]:
    if bool(file_path) == bool(url):
        raise ValueError("Provide exactly one OSHA CSV file path or URL.")
    if file_path:
        path = Path(file_path)
        if path.suffix.casefold() == ".zip":
            with _zip_csv_stream(path) as handle:
                yield handle
            return
        handle = path.open("r", encoding="utf-8-sig", newline="")
        try:
            yield handle
        finally:
            handle.close()
        return

    response = requests.get(
        str(url),
        stream=True,
        timeout=(15, 120),
        headers={"User-Agent": "BridgewaterProspectEngine/0.1 (local research tool)"},
    )
    response.raise_for_status()
    if str(url).split("?", 1)[0].casefold().endswith(".zip"):
        try:
            with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as archive:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        archive.write(chunk)
                archive.seek(0)
                with _zip_csv_stream(archive) as handle:
                    yield handle
        finally:
            response.close()
        return
    response.encoding = "utf-8-sig"
    try:
        # Wrapping urllib3's raw response in TextIOWrapper is tempting, but the raw
        # object auto-closes at EOF and TextIOWrapper then raises ``ValueError``
        # instead of ending iteration. Requests' line iterator owns that lifecycle
        # correctly and still lets csv.DictReader stream the national file.
        yield response.iter_lines(decode_unicode=True)
    finally:
        response.close()


def _rate(cases: int | None, hours: float | None) -> float | None:
    if cases is None or hours is None or hours <= 0:
        return None
    return round((cases * 200_000) / hours, 2)


def import_osha_summary(
    connection: sqlite3.Connection,
    settings: Settings,
    *,
    file_path: str | Path | None = None,
    url: str | None = None,
    cities: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    source_reference = str(file_path or url)
    target_cities = {city.casefold() for city in (cities or settings.priority_cities)}
    run_id = start_ingestion_run(
        connection,
        "osha_ita_summary",
        source_reference,
        {"state": "MA", "cities": sorted(target_cities)},
    )
    seen = imported = rejected = 0
    retrieval_date = date.today().isoformat()
    source_url = url or settings.osha_ita_page_url
    metadata = {
        "source_url": source_url,
        "publisher": "U.S. Department of Labor, Occupational Safety and Health Administration",
        "source_title": "Injury Tracking Application 300A Summary Data",
        "retrieval_date": retrieval_date,
        "publication_date": None,
        "source_strength": 5,
        "source_type": "osha_ita_summary",
        "raw_source_identifier": Path(file_path).name if file_path else str(url).rsplit("/", 1)[-1],
        "extraction_method": "streamed_official_csv",
        "validation_status": "verified",
    }
    source_id = upsert_source(connection, metadata, run_id)
    try:
        with _osha_text_stream(file_path=file_path, url=url) as handle:
            reader = csv.DictReader(handle)
            validate_headers(reader.fieldnames or [])
            missing = OSHA_REQUIRED_FIELDS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"OSHA CSV is missing fields: {', '.join(sorted(missing))}")
            for source_row in reader:
                seen += 1
                if (source_row.get("state") or "").upper() != "MA":
                    continue
                if target_cities and (source_row.get("city") or "").casefold() not in target_cities:
                    continue
                try:
                    name = (source_row.get("establishment_name") or source_row.get("company_name") or "").strip()
                    if not name:
                        raise ValueError("OSHA establishment name is missing.")
                    annual_employees = parse_int(source_row.get("annual_average_employees"))
                    row = {
                        "canonical_name": name,
                        "organization_type": "workplace",
                        "industry": (source_row.get("industry_description") or "").strip(),
                        "union_affiliation": "",
                        "local_number": "",
                        "official_identifier": (source_row.get("establishment_id") or "").strip(),
                        "website": "",
                        "public_phone": "",
                        "public_email": "",
                        "street": (source_row.get("street_address") or "").strip(),
                        "city": (source_row.get("city") or "").strip(),
                        "state": "MA",
                        "zip": (source_row.get("zip_code") or "").strip(),
                        "latitude": "",
                        "longitude": "",
                        "estimated_reach": str(annual_employees or ""),
                        "active_status": "reported",
                        "public_accessibility": "0",
                        "active_program": "0",
                    }
                    values = _organization_values(row, settings)
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
                    else:
                        organization_id = match["organization_id"]
                    asserted_fields = {
                        field
                        for field in ORGANIZATION_FIELDS
                        if row.get(field, "").strip()
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

                    dafw = parse_int(source_row.get("total_dafw_cases")) or 0
                    djtr = parse_int(source_row.get("total_djtr_cases")) or 0
                    other = parse_int(source_row.get("total_other_cases")) or 0
                    total_cases = dafw + djtr + other
                    dart_cases = dafw + djtr
                    hours = parse_float(source_row.get("total_hours_worked"))
                    filing_year = parse_int(source_row.get("year_filing_for"))
                    if filing_year is None:
                        raise ValueError("OSHA filing year is missing.")
                    establishment_id = (source_row.get("establishment_id") or source_row.get("ID") or "").strip()
                    metric_id = stable_id(
                        "osha", establishment_id or organization_id, filing_year
                    )
                    anchor_assertion = create_assertion(
                        connection,
                        source_id=source_id,
                        entity_type="osha_metric",
                        entity_id=metric_id,
                        field_name="annual_average_employees",
                        asserted_value=annual_employees,
                        observed_at=retrieval_date,
                        source_type="osha_ita_summary",
                        validation_status="verified",
                        structured_field_name="annual_average_employees",
                    )
                    connection.execute(
                        """
                        INSERT INTO osha_metrics(
                            osha_metric_id, organization_id, establishment_id,
                            filing_year, naics_code, annual_average_employees,
                            total_hours_worked, total_recordable_cases, dart_cases,
                            total_case_rate, dart_rate, source_assertion_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(establishment_id, filing_year) DO UPDATE SET
                            organization_id = excluded.organization_id,
                            annual_average_employees = excluded.annual_average_employees,
                            total_hours_worked = excluded.total_hours_worked,
                            total_recordable_cases = excluded.total_recordable_cases,
                            dart_cases = excluded.dart_cases,
                            total_case_rate = excluded.total_case_rate,
                            dart_rate = excluded.dart_rate,
                            source_assertion_id = excluded.source_assertion_id
                        """,
                        (
                            metric_id,
                            organization_id,
                            establishment_id or metric_id,
                            filing_year,
                            (source_row.get("naics_code") or "").strip() or None,
                            annual_employees,
                            hours,
                            total_cases,
                            dart_cases,
                            _rate(total_cases, hours),
                            _rate(dart_cases, hours),
                            anchor_assertion,
                        ),
                    )
                    for field, value in {
                        "establishment_id": establishment_id or metric_id,
                        "filing_year": filing_year,
                        "naics_code": (source_row.get("naics_code") or "").strip()
                        or None,
                        "total_hours_worked": hours,
                        "total_recordable_cases": total_cases,
                        "dart_cases": dart_cases,
                        "total_case_rate": _rate(total_cases, hours),
                        "dart_rate": _rate(dart_cases, hours),
                    }.items():
                        if value is not None:
                            create_assertion(
                                connection,
                                source_id=source_id,
                                entity_type="osha_metric",
                                entity_id=metric_id,
                                field_name=field,
                                asserted_value=value,
                                observed_at=retrieval_date,
                                source_type="osha_ita_summary",
                                validation_status="verified",
                                structured_field_name=field,
                            )
                    imported += 1
                except Exception:
                    rejected += 1
                if imported and imported % 250 == 0:
                    connection.commit()
        connection.commit()
        finish_ingestion_run(
            connection,
            run_id,
            status="completed",
            rows_seen=seen,
            rows_imported=imported,
            rows_queued=0,
            rows_rejected=rejected,
        )
        return imported, rejected
    except Exception as exc:
        connection.rollback()
        finish_ingestion_run(
            connection,
            run_id,
            status="failed",
            rows_seen=seen,
            rows_imported=imported,
            rows_queued=0,
            rows_rejected=rejected,
            error_message=str(exc),
        )
        raise
