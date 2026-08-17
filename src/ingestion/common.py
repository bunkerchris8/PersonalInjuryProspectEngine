from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any

from src.validation.sources import evaluate_freshness, validate_source_metadata


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def stable_id(prefix: str, *parts: object) -> str:
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(joined.encode()).hexdigest()[:24]}"


def start_ingestion_run(
    connection: sqlite3.Connection,
    source_kind: str,
    source_reference: str,
    parameters: dict[str, object] | None = None,
) -> str:
    run_id = new_id("run")
    connection.execute(
        """
        INSERT INTO ingestion_runs(
            ingestion_run_id, source_kind, source_reference, started_at,
            status, parameters_json
        ) VALUES (?, ?, ?, ?, 'running', ?)
        """,
        (run_id, source_kind, source_reference, utc_now(), json.dumps(parameters or {})),
    )
    connection.commit()
    return run_id


def finish_ingestion_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    rows_seen: int,
    rows_imported: int,
    rows_queued: int,
    rows_rejected: int,
    error_message: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE ingestion_runs
        SET completed_at = ?, status = ?, rows_seen = ?, rows_imported = ?,
            rows_queued = ?, rows_rejected = ?, error_message = ?
        WHERE ingestion_run_id = ?
        """,
        (
            utc_now(),
            status,
            rows_seen,
            rows_imported,
            rows_queued,
            rows_rejected,
            error_message,
            run_id,
        ),
    )
    connection.commit()


def upsert_source(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    ingestion_run_id: str,
) -> str:
    validate_source_metadata(metadata)
    raw_identifier = str(metadata.get("raw_source_identifier") or "")
    source_id = stable_id(
        "src",
        metadata["source_url"],
        metadata["retrieval_date"],
        raw_identifier,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO sources(
            source_id, source_url, publisher, dataset_or_page_title,
            retrieval_date, publication_or_filing_date, source_strength,
            source_type, raw_source_identifier, extraction_method,
            validation_status, ingestion_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            metadata["source_url"],
            metadata["publisher"],
            metadata["source_title"],
            metadata["retrieval_date"],
            metadata.get("publication_date") or None,
            int(metadata["source_strength"]),
            metadata["source_type"],
            raw_identifier,
            metadata.get("extraction_method") or "structured_import",
            metadata["validation_status"],
            ingestion_run_id,
        ),
    )
    return source_id


def create_assertion(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    entity_type: str,
    entity_id: str,
    field_name: str,
    asserted_value: object,
    observed_at: str,
    source_type: str,
    validation_status: str,
    relevant_source_excerpt: str | None = None,
    structured_field_name: str | None = None,
    conflict_group: str | None = None,
) -> str:
    value = "" if asserted_value is None else str(asserted_value)
    assertion_id = stable_id(
        "ast", source_id, entity_type, entity_id, field_name, value
    )
    freshness = evaluate_freshness(source_type, observed_at)
    connection.execute(
        """
        INSERT OR IGNORE INTO source_assertions(
            assertion_id, source_id, entity_type, entity_id, field_name,
            asserted_value, relevant_source_excerpt, structured_field_name,
            validation_status, observed_at, freshness_expires_at, conflict_group
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assertion_id,
            source_id,
            entity_type,
            entity_id,
            field_name,
            value,
            relevant_source_excerpt,
            structured_field_name or field_name,
            validation_status,
            observed_at,
            freshness.expires_at.isoformat(),
            conflict_group,
        ),
    )
    return assertion_id


def queue_research_row(
    connection: sqlite3.Connection,
    *,
    ingestion_run_id: str,
    entity_type: str,
    display_name: str | None,
    reason: str,
    row: dict[str, Any],
) -> None:
    payload = json.dumps(row, sort_keys=True)
    connection.execute(
        """
        INSERT INTO research_queue(
            research_queue_id, entity_type, display_name, reason,
            raw_payload_json, source_url, source_strength, ingestion_run_id
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1 FROM research_queue
            WHERE entity_type = ? AND display_name IS ? AND reason = ?
              AND raw_payload_json = ? AND status = 'unverified'
        )
        """,
        (
            new_id("rq"),
            entity_type,
            display_name,
            reason,
            payload,
            row.get("source_url"),
            int(row["source_strength"]) if row.get("source_strength") else None,
            ingestion_run_id,
            entity_type,
            display_name,
            reason,
            payload,
        ),
    )


def source_metadata_from_row(row: dict[str, str]) -> dict[str, object]:
    return {
        "source_url": (row.get("source_url") or "").strip(),
        "publisher": (row.get("publisher") or "").strip(),
        "source_title": (row.get("source_title") or "").strip(),
        "retrieval_date": (row.get("retrieval_date") or "").strip(),
        "publication_date": (row.get("publication_date") or "").strip() or None,
        "source_strength": int((row.get("source_strength") or "0").strip()),
        "source_type": (row.get("source_type") or "").strip(),
        "raw_source_identifier": (row.get("raw_source_identifier") or "").strip(),
        "extraction_method": (row.get("extraction_method") or "manual_csv").strip(),
        "validation_status": (row.get("validation_status") or "unverified").strip(),
    }


def observed_date(metadata: dict[str, object]) -> str:
    value = metadata.get("publication_date") or metadata["retrieval_date"]
    return str(value)[:10]


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def parse_int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value).replace(",", "")))


def parse_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(",", ""))


def today_iso() -> str:
    return date.today().isoformat()
