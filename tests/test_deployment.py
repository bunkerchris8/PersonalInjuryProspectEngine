from __future__ import annotations

import sqlite3

import pytest

from src.config import PROJECT_ROOT
from src.database import connect_database, initialize_schema
from src.database.deployment import (
    build_deployment_seed,
    materialize_deployment_seed,
)
from src.database.repository import record_review
from src.ingestion.csv_importer import (
    import_contacts_csv,
    import_events_csv,
    import_organizations_csv,
)
from src.scoring.service import score_all_organizations


SAMPLES = PROJECT_ROOT / "data" / "raw"


def test_deployment_seed_restores_data_into_schema_only_database(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    import_events_csv(connection, SAMPLES / "sample_events.csv")
    score_all_organizations(connection, settings)

    archive = tmp_path / "deployment" / "prospects.db.gz"
    stats = build_deployment_seed(settings.database_path, archive)
    assert stats.organizations == 3
    assert stats.contacts == 4
    assert stats.events == 1

    runtime_path = tmp_path / "runtime" / "prospects.db"
    empty_runtime = connect_database(runtime_path)
    initialize_schema(empty_runtime)
    empty_runtime.close()

    assert materialize_deployment_seed(runtime_path, archive) is True
    deployed = sqlite3.connect(runtime_path)
    try:
        assert deployed.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 3
        assert deployed.execute("SELECT COUNT(*) FROM research_queue").fetchone()[0] == 0
        assert deployed.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 0
        assert deployed.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        deployed.close()

    assert materialize_deployment_seed(runtime_path, archive) is False


def test_deployment_seed_refuses_operational_review_data(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    organization_id = connection.execute(
        "SELECT organization_id FROM organizations LIMIT 1"
    ).fetchone()[0]
    record_review(connection, organization_id, "rejected", reviewer="Reviewer")

    with pytest.raises(ValueError, match="operational review data"):
        build_deployment_seed(settings.database_path, tmp_path / "prospects.db.gz")
