from __future__ import annotations

import csv
import io

import pytest

from src.config import PROJECT_ROOT
from src.database.repository import (
    approved_prospects_csv,
    fetch_ranked_prospects,
    record_review,
)
from src.ingestion.csv_importer import import_contacts_csv, import_organizations_csv
from src.scoring.service import score_all_organizations


SAMPLES = PROJECT_ROOT / "data" / "raw"


def seed_approved_sample(connection, settings):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    score_all_organizations(connection, settings)
    ids = {
        row["canonical_name"]: row["organization_id"]
        for row in connection.execute(
            "SELECT canonical_name, organization_id FROM organizations"
        )
    }
    for name in ("IBEW Local 223", "Teamsters Local 653"):
        record_review(
            connection,
            ids[name],
            "approved",
            reviewer="Test reviewer",
            ethics_review_completed=True,
        )
    return ids


def parse_csv(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def test_custom_export_can_generate_name_and_phone_list(connection, settings):
    ids = seed_approved_sample(connection, settings)

    payload = approved_prospects_csv(
        connection,
        ["prospect_name", "organization_phone"],
        organization_ids=[ids["IBEW Local 223"]],
    )
    rows = parse_csv(payload)

    assert payload.decode("utf-8").splitlines()[0] == "Prospect name,Organization phone"
    assert rows == [
        {
            "Prospect name": "IBEW Local 223",
            "Organization phone": "508-880-2690",
        }
    ]


def test_custom_export_uses_one_primary_verified_contact_per_prospect(
    connection, settings
):
    ids = seed_approved_sample(connection, settings)

    rows = parse_csv(
        approved_prospects_csv(
            connection,
            ["prospect_name", "contact_name", "contact_phone", "contact_email"],
            organization_ids=[ids["IBEW Local 223"]],
        )
    )

    assert len(rows) == 1
    assert rows[0]["Prospect name"] == "IBEW Local 223"
    assert rows[0]["Contact name"] == "Steven M. Barry"
    assert rows[0]["Contact phone"] == "508-880-2690"


def test_custom_export_validates_fields_and_keeps_headers_when_empty(
    connection, settings
):
    seed_approved_sample(connection, settings)

    empty = approved_prospects_csv(
        connection,
        ["prospect_name", "full_address"],
        organization_ids=[],
    ).decode("utf-8")

    assert empty == "Prospect name,Full address\n"
    with pytest.raises(ValueError, match="at least one"):
        approved_prospects_csv(connection, [])
    with pytest.raises(ValueError, match="Unknown CSV fields"):
        approved_prospects_csv(connection, ["private_notes"])


def test_ranked_prospects_include_address_and_primary_contact(connection, settings):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    score_all_organizations(connection, settings)

    rows = fetch_ranked_prospects(connection)
    ibew = next(row for row in rows if row["canonical_name"] == "IBEW Local 223")

    assert ibew["street"] == "475 Myles Standish Blvd"
    assert ibew["primary_contact_name"] == "Steven M. Barry"
    assert ibew["primary_contact_phone"] == "508-880-2690"
