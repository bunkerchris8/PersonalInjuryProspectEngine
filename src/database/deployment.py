from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


SQLITE_HEADER = b"SQLite format 3\x00"
OPERATIONAL_TABLES = ("outreach_reviews", "outreach_history", "suppressions")


@dataclass(frozen=True)
class DeploymentSeedStats:
    organizations: int
    contacts: int
    events: int
    sources: int
    assertions: int


@dataclass(frozen=True)
class _OperationalState:
    organization_states: tuple[dict[str, object], ...]
    contact_states: tuple[dict[str, object], ...]
    outreach_reviews: tuple[dict[str, object], ...]
    outreach_history: tuple[dict[str, object], ...]
    suppressions: tuple[dict[str, object], ...]


def deployment_seed_fingerprint(
    seed_archive_path: str | Path | None,
) -> str | None:
    """Return a content fingerprint that can safely participate in a cache key."""
    if seed_archive_path is None:
        return None
    archive = Path(seed_archive_path)
    if not archive.is_file():
        return None

    digest = hashlib.sha256()
    with archive.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_deployment_seed(
    database_path: str | Path,
    seed_archive_path: str | Path | None,
    *,
    seed_fingerprint: str | None = None,
) -> bool:
    """Expand a new bundled database into a managed deployment runtime.

    Community Cloud starts from the files tracked by Git. The writable SQLite
    database is intentionally ignored, so a clean deployment needs a tracked,
    read-only seed artifact from which to create its runtime copy. A sidecar
    fingerprint lets a data-only Git deployment refresh a previously expanded
    runtime. Human review and suppression records are carried into the new copy.
    """
    if str(database_path) == ":memory:" or seed_archive_path is None:
        return False

    target = Path(database_path)
    archive = Path(seed_archive_path)
    fingerprint = seed_fingerprint or deployment_seed_fingerprint(archive)
    if fingerprint is None:
        return False

    marker = _seed_marker_path(target)
    target_has_organizations = _contains_organizations(target)
    installed_fingerprint = _read_seed_marker(marker)
    if target_has_organizations and installed_fingerprint == fingerprint:
        return False

    # A developer's working database shares the configured path with the app.
    # Only replace populated databases that we can identify as seed-derived.
    if (
        target_has_organizations
        and installed_fingerprint is None
        and not _looks_like_managed_runtime(target)
    ):
        return False

    operational_state = (
        _capture_operational_state(target)
        if target_has_organizations
        else _OperationalState((), (), (), (), ())
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            with gzip.open(archive, "rb") as source:
                shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())

        with temporary_path.open("rb") as candidate:
            if candidate.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
                raise ValueError(f"Deployment seed is not a valid SQLite database: {archive}")

        _restore_operational_state(temporary_path, operational_state)
        os.replace(temporary_path, target)
        _write_seed_marker(marker, fingerprint)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return True


def deployment_seed_is_current(
    database_path: str | Path,
    seed_fingerprint: str | None,
) -> bool:
    """Return whether a runtime database was expanded from the current seed."""
    if not seed_fingerprint or str(database_path) == ":memory:":
        return False
    target = Path(database_path)
    return (
        _contains_organizations(target)
        and _read_seed_marker(_seed_marker_path(target)) == seed_fingerprint
    )


def build_deployment_seed(
    source_database_path: str | Path,
    seed_archive_path: str | Path,
) -> DeploymentSeedStats:
    """Build a compact deployment seed without review or quarantined records."""
    source_path = Path(source_database_path)
    archive_path = Path(seed_archive_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    database_descriptor, database_name = tempfile.mkstemp(
        prefix=".deployment-seed-", suffix=".db", dir=archive_path.parent
    )
    os.close(database_descriptor)
    snapshot_path = Path(database_name)
    archive_descriptor, archive_name = tempfile.mkstemp(
        prefix=f".{archive_path.name}.", suffix=".tmp", dir=archive_path.parent
    )
    os.close(archive_descriptor)
    temporary_archive_path = Path(archive_name)

    source: sqlite3.Connection | None = None
    snapshot: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        snapshot = sqlite3.connect(snapshot_path)
        operational_counts = {
            table: int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in OPERATIONAL_TABLES
        }
        populated_operational_tables = {
            table: count for table, count in operational_counts.items() if count
        }
        if populated_operational_tables:
            details = ", ".join(
                f"{table}={count}"
                for table, count in populated_operational_tables.items()
            )
            raise ValueError(
                "Deployment seed was not built because the source contains "
                f"operational review data ({details})."
            )

        source.backup(snapshot)
        snapshot.execute("PRAGMA foreign_keys = ON")
        snapshot.execute("DELETE FROM research_queue")
        snapshot.execute("UPDATE sources SET ingestion_run_id = NULL")
        snapshot.execute("DELETE FROM ingestion_runs")
        snapshot.commit()
        snapshot.execute("PRAGMA journal_mode = DELETE")
        snapshot.execute("VACUUM")

        integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Deployment seed failed SQLite integrity check: {integrity}")

        stats = DeploymentSeedStats(
            organizations=_table_count(snapshot, "organizations"),
            contacts=_table_count(snapshot, "contacts"),
            events=_table_count(snapshot, "events"),
            sources=_table_count(snapshot, "sources"),
            assertions=_table_count(snapshot, "source_assertions"),
        )
        if stats.organizations == 0:
            raise ValueError("Deployment seed was not built because the source has no organizations.")

        snapshot.close()
        snapshot = None
        source.close()
        source = None

        with temporary_archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_archive,
                mtime=0,
            ) as compressed:
                with snapshot_path.open("rb") as snapshot_file:
                    shutil.copyfileobj(snapshot_file, compressed)
            raw_archive.flush()
            os.fsync(raw_archive.fileno())
        os.replace(temporary_archive_path, archive_path)
        return stats
    finally:
        if snapshot is not None:
            snapshot.close()
        if source is not None:
            source.close()
        temporary_archive_path.unlink(missing_ok=True)
        snapshot_path.unlink(missing_ok=True)


def _contains_organizations(database_path: Path) -> bool:
    if not database_path.is_file() or database_path.stat().st_size == 0:
        return False
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'organizations'"
        ).fetchone()
        if not table_exists:
            return False
        return bool(connection.execute("SELECT 1 FROM organizations LIMIT 1").fetchone())
    finally:
        connection.close()


def _seed_marker_path(database_path: Path) -> Path:
    return database_path.with_name(f".{database_path.name}.seed-sha256")


def _read_seed_marker(marker_path: Path) -> str | None:
    if not marker_path.is_file():
        return None
    value = marker_path.read_text(encoding="utf-8").strip()
    return value or None


def _write_seed_marker(marker_path: Path, fingerprint: str) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{marker_path.name}.", suffix=".tmp", dir=marker_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker_file:
            marker_file.write(f"{fingerprint}\n")
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temporary_path, marker_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _looks_like_managed_runtime(database_path: Path) -> bool:
    """Recognize an older seed-derived runtime that predates fingerprint markers."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        for table in ("ingestion_runs", "research_queue"):
            if not _table_exists(connection, table):
                return False
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return False
        return True
    finally:
        connection.close()


def _capture_operational_state(database_path: Path) -> _OperationalState:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return _OperationalState(
            organization_states=tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT organization_id, review_status, do_not_contact FROM organizations"
                )
            ),
            contact_states=tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT contact_id, do_not_contact FROM contacts"
                )
            ),
            outreach_reviews=_table_rows(connection, "outreach_reviews"),
            outreach_history=_table_rows(connection, "outreach_history"),
            suppressions=_table_rows(connection, "suppressions"),
        )
    finally:
        connection.close()


def _restore_operational_state(
    database_path: Path,
    state: _OperationalState,
) -> None:
    if not any(
        (state.outreach_reviews, state.outreach_history, state.suppressions)
    ):
        return

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        organization_ids = {
            row[0] for row in connection.execute("SELECT organization_id FROM organizations")
        }
        contact_ids = {
            row[0] for row in connection.execute("SELECT contact_id FROM contacts")
        }

        for row in state.outreach_reviews:
            if row["organization_id"] in organization_ids:
                _insert_row(connection, "outreach_reviews", row)

        review_ids = {
            row[0] for row in connection.execute("SELECT review_id FROM outreach_reviews")
        }
        for original in state.outreach_history:
            if (
                original["organization_id"] not in organization_ids
                or original["review_id"] not in review_ids
            ):
                continue
            row = dict(original)
            if row["contact_id"] not in contact_ids:
                row["contact_id"] = None
            _insert_row(connection, "outreach_history", row)

        for row in state.suppressions:
            valid_entity = (
                row["entity_type"] == "organization"
                and row["entity_id"] in organization_ids
            ) or (
                row["entity_type"] == "contact" and row["entity_id"] in contact_ids
            )
            if valid_entity:
                _insert_row(connection, "suppressions", row)

        connection.executemany(
            """
            UPDATE organizations
            SET review_status = ?, do_not_contact = ?
            WHERE organization_id = ?
            """,
            (
                (row["review_status"], row["do_not_contact"], row["organization_id"])
                for row in state.organization_states
                if row["organization_id"] in organization_ids
            ),
        )
        connection.executemany(
            "UPDATE contacts SET do_not_contact = ? WHERE contact_id = ?",
            (
                (row["do_not_contact"], row["contact_id"])
                for row in state.contact_states
                if row["contact_id"] in contact_ids
            ),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(
                f"Refreshed deployment database failed SQLite integrity check: {integrity}"
            )
    finally:
        connection.close()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    )


def _table_rows(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[dict[str, object], ...]:
    if not _table_exists(connection, table):
        return ()
    return tuple(dict(row) for row in connection.execute(f"SELECT * FROM {table}"))


def _insert_row(
    connection: sqlite3.Connection,
    table: str,
    row: dict[str, object],
) -> None:
    columns = tuple(row)
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    connection.execute(
        f"INSERT OR IGNORE INTO {table} ({column_list}) VALUES ({placeholders})",
        tuple(row[column] for column in columns),
    )


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
