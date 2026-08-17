from __future__ import annotations

import gzip
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


def materialize_deployment_seed(
    database_path: str | Path,
    seed_archive_path: str | Path | None,
) -> bool:
    """Expand a bundled database when the runtime database has no organizations.

    Community Cloud starts from the files tracked by Git. The writable SQLite
    database is intentionally ignored, so a clean deployment needs a tracked,
    read-only seed artifact from which to create its runtime copy.
    """
    if str(database_path) == ":memory:" or seed_archive_path is None:
        return False

    target = Path(database_path)
    archive = Path(seed_archive_path)
    if not archive.is_file() or _contains_organizations(target):
        return False

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

        os.replace(temporary_path, target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return True


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


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
