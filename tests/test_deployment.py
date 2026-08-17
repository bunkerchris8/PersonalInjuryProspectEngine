from __future__ import annotations

import sqlite3

import pytest

from src.config import PROJECT_ROOT
from src.database import connect_database, initialize_schema
from src.database.deployment import (
    build_deployment_seed,
    deployment_seed_fingerprint,
    deployment_seed_is_current,
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
    fingerprint = deployment_seed_fingerprint(archive)
    assert fingerprint
    assert deployment_seed_is_current(runtime_path, fingerprint)


def test_changed_deployment_seed_refreshes_runtime_and_preserves_review_state(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    score_all_organizations(connection, settings)

    archive = tmp_path / "deployment" / "prospects.db.gz"
    build_deployment_seed(settings.database_path, archive)
    original_fingerprint = deployment_seed_fingerprint(archive)

    runtime_path = tmp_path / "runtime" / "prospects.db"
    assert materialize_deployment_seed(runtime_path, archive) is True
    runtime = connect_database(runtime_path)
    organization_id = runtime.execute(
        "SELECT organization_id FROM organizations ORDER BY organization_id LIMIT 1"
    ).fetchone()[0]
    record_review(
        runtime,
        organization_id,
        "approved",
        reviewer="Deployment test reviewer",
        ethics_review_completed=True,
    )
    runtime.close()

    connection.execute(
        "UPDATE organizations SET public_phone = ? WHERE organization_id = ?",
        ("508-555-0199", organization_id),
    )
    connection.commit()
    build_deployment_seed(settings.database_path, archive)
    refreshed_fingerprint = deployment_seed_fingerprint(archive)
    assert refreshed_fingerprint != original_fingerprint

    assert materialize_deployment_seed(runtime_path, archive) is True
    refreshed = connect_database(runtime_path)
    try:
        organization = refreshed.execute(
            """
            SELECT public_phone, review_status
            FROM organizations
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchone()
        assert tuple(organization) == ("508-555-0199", "approved")
        assert refreshed.execute("SELECT COUNT(*) FROM outreach_reviews").fetchone()[0] == 1
        assert deployment_seed_is_current(runtime_path, refreshed_fingerprint)
    finally:
        refreshed.close()


def test_populated_working_database_is_not_replaced_by_seed(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    score_all_organizations(connection, settings)
    archive = tmp_path / "deployment" / "prospects.db.gz"
    build_deployment_seed(settings.database_path, archive)

    connection.execute(
        "UPDATE organizations SET public_phone = 'local-unpublished-change'"
    )
    connection.commit()

    assert materialize_deployment_seed(settings.database_path, archive) is False
    value = connection.execute(
        "SELECT public_phone FROM organizations ORDER BY organization_id LIMIT 1"
    ).fetchone()[0]
    assert value == "local-unpublished-change"


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
