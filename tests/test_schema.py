from __future__ import annotations


def test_required_tables_exist(connection):
    expected = {
        "organizations",
        "locations",
        "contacts",
        "roles",
        "sources",
        "source_assertions",
        "organization_aliases",
        "events",
        "workforce_metrics",
        "osha_metrics",
        "census_geographies",
        "scores",
        "outreach_reviews",
        "outreach_history",
        "suppressions",
        "ingestion_runs",
    }
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    actual = {row["name"] for row in rows}
    assert expected <= actual


def test_outreach_history_requires_human_flag(connection):
    definition = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outreach_history'"
    ).fetchone()["sql"]
    assert "initiated_by_human = 1" in definition

