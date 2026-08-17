from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT
from src.database.repository import (
    approved_prospects_csv,
    record_review,
    set_organization_suppression,
)
from src.ingestion.census_acs import import_acs_massachusetts_places
from src.ingestion.census_geocoder import (
    geocode_address,
    geocode_pending_organizations_batch,
)
from src.ingestion.csv_importer import import_contacts_csv, import_events_csv, import_organizations_csv
from src.ingestion.osha_ita import import_osha_summary
from src.scoring.service import score_all_organizations


SAMPLES = PROJECT_ROOT / "data" / "raw"


def test_sample_vertical_slice_is_idempotent(connection, settings):
    first = import_organizations_csv(
        connection, SAMPLES / "sample_organizations.csv", settings
    )
    second = import_organizations_csv(
        connection, SAMPLES / "sample_organizations.csv", settings
    )
    contacts = import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    events = import_events_csv(connection, SAMPLES / "sample_events.csv")
    scored = score_all_organizations(connection, settings)

    assert first.rows_imported == 3
    assert second.rows_imported == 3
    assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 3
    assert contacts.rows_imported == 4
    assert events.rows_imported == 1
    assert scored == 3
    assert connection.execute(
        "SELECT COUNT(*) FROM organizations WHERE adjusted_priority IS NOT NULL"
    ).fetchone()[0] == 3
    assert connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions a
        JOIN sources s ON s.source_id = a.source_id
        WHERE s.source_url = '' OR s.retrieval_date = ''
        """
    ).fetchone()[0] == 0
    assert connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions
        WHERE entity_type = 'role' AND field_name = 'role_date'
        """
    ).fetchone()[0] == 4
    assert connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions
        WHERE entity_type = 'event'
          AND field_name IN ('venue_name', 'street', 'permission_required')
        """
    ).fetchone()[0] == 3


def test_strength_one_rows_are_quarantined(connection, settings, tmp_path):
    template = SAMPLES / "templates" / "organizations.csv"
    headers = next(csv.reader(template.open()))
    path = tmp_path / "weak.csv"
    row = {header: "" for header in headers}
    row.update(
        {
            "canonical_name": "Unverified Example",
            "organization_type": "workplace",
            "source_url": "https://example.invalid/unverified",
            "publisher": "Unknown",
            "source_title": "Unverified list",
            "retrieval_date": "2026-08-16",
            "source_strength": "1",
            "source_type": "weak_directory",
            "validation_status": "unverified",
        }
    )
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerow(row)
    stats = import_organizations_csv(connection, path, settings)
    assert stats.rows_queued == 1
    assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM research_queue").fetchone()[0] == 1


def test_approval_and_suppression_are_enforced(connection, settings):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    organization_id = connection.execute(
        "SELECT organization_id FROM organizations ORDER BY canonical_name LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(ValueError, match="ethics review"):
        record_review(connection, organization_id, "approved")
    record_review(
        connection,
        organization_id,
        "approved",
        reviewer="Test Reviewer",
        ethics_review_completed=True,
    )
    assert b"IBEW Local 223" in approved_prospects_csv(connection)
    set_organization_suppression(
        connection, organization_id, active=True, reason="Do not contact request"
    )
    with pytest.raises(ValueError, match="suppressed"):
        record_review(
            connection,
            organization_id,
            "approved",
            ethics_review_completed=True,
        )
    assert b"IBEW Local 223" not in approved_prospects_csv(connection)


def test_conflicting_contact_fact_is_preserved_for_review(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    sample_path = SAMPLES / "sample_contacts.csv"
    with sample_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        source_row = next(reader)
        headers = reader.fieldnames
    source_row["public_phone"] = "508-000-0000"
    source_row["source_url"] = "https://example.invalid/conflicting-official-fixture"
    source_row["raw_source_identifier"] = "contact-conflict-fixture"
    path = tmp_path / "contact_conflict.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerow(source_row)

    stats = import_contacts_csv(connection, path)
    contact = connection.execute(
        "SELECT * FROM contacts WHERE display_name = 'Steven M. Barry'"
    ).fetchone()
    organization = connection.execute(
        "SELECT * FROM organizations WHERE canonical_name = 'IBEW Local 223'"
    ).fetchone()
    assert stats.rows_imported == 1
    assert contact["public_phone"] == "508-880-2690"
    assert contact["verification_status"] == "conflicting"
    assert organization["review_status"] == "needs_review"
    assert connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions
        WHERE entity_type = 'contact' AND entity_id = ?
          AND field_name = 'public_phone' AND conflict_group IS NOT NULL
        """,
        (contact["contact_id"],),
    ).fetchone()[0] == 2


def test_two_independent_strength_three_sources_are_required(
    connection, settings, tmp_path
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    template = SAMPLES / "templates" / "contacts.csv"
    headers = next(csv.reader(template.open()))
    base_row = {header: "" for header in headers}
    base_row.update(
        {
            "organization_name": "IBEW Local 223",
            "contact_name": "Secondary Sourced Leader",
            "contact_scope": "named_professional",
            "is_public_professional": "1",
            "role_title": "Membership Director",
            "role_date": "2026-08-16",
            "professional_url": "https://example.invalid/professional-role",
            "publisher": "Reliable secondary fixture",
            "source_title": "Leadership listing fixture",
            "retrieval_date": "2026-08-16",
            "source_strength": "3",
            "source_type": "reliable_secondary",
            "extraction_method": "manual_csv",
            "validation_status": "verified",
        }
    )

    first_path = tmp_path / "contact_source_one.csv"
    first_row = {**base_row, "source_url": "https://example.invalid/source-one", "raw_source_identifier": "source-one"}
    with first_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerow(first_row)
    import_contacts_csv(connection, first_path)
    score_all_organizations(connection, settings)
    contact = connection.execute(
        "SELECT * FROM contacts WHERE display_name = 'Secondary Sourced Leader'"
    ).fetchone()
    first_score = connection.execute(
        """
        SELECT role_influence FROM scores
        WHERE organization_id = ? ORDER BY rowid DESC LIMIT 1
        """,
        (contact["organization_id"],),
    ).fetchone()[0]
    assert contact["verification_status"] == "needs_corroboration"
    assert first_score == 0

    second_path = tmp_path / "contact_source_two.csv"
    second_row = {**base_row, "source_url": "https://example.invalid/source-two", "raw_source_identifier": "source-two"}
    with second_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerow(second_row)
    import_contacts_csv(connection, second_path)
    score_all_organizations(connection, settings)
    contact = connection.execute(
        "SELECT * FROM contacts WHERE display_name = 'Secondary Sourced Leader'"
    ).fetchone()
    second_score = connection.execute(
        """
        SELECT role_influence FROM scores
        WHERE organization_id = ? ORDER BY rowid DESC LIMIT 1
        """,
        (contact["organization_id"],),
    ).fetchone()[0]
    assert contact["verification_status"] == "verified"
    assert second_score == 16


def test_osha_import_is_aggregate_and_calculates_valid_rates(connection, settings):
    imported, rejected = import_osha_summary(
        connection,
        settings,
        file_path=PROJECT_ROOT / "tests" / "fixtures" / "osha_summary.csv",
    )
    assert imported == 1
    assert rejected == 0
    metric = connection.execute("SELECT * FROM osha_metrics").fetchone()
    assert metric["total_recordable_cases"] == 10
    assert metric["dart_cases"] == 5
    assert metric["total_case_rate"] == 10
    assert metric["dart_rate"] == 5


def test_osha_import_reads_official_zip_archives(connection, settings, tmp_path):
    archive = tmp_path / "osha-summary.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(
            PROJECT_ROOT / "tests" / "fixtures" / "osha_summary.csv",
            arcname="ITA_300A_Summary_Data.csv",
        )

    imported, rejected = import_osha_summary(
        connection,
        settings,
        file_path=archive,
    )

    assert imported == 1
    assert rejected == 0


def test_osha_url_import_uses_requests_stream_without_raw_text_wrapper(
    connection, settings, monkeypatch
):
    fixture = (PROJECT_ROOT / "tests" / "fixtures" / "osha_summary.csv").read_text()

    class FakeRaw(io.BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("The importer must not wrap response.raw directly.")

    class FakeResponse:
        url = "https://www.osha.gov/example.csv"
        encoding = None
        raw = FakeRaw()
        closed = False

        def raise_for_status(self):
            return None

        def iter_lines(self, *, decode_unicode):
            assert decode_unicode is True
            assert self.encoding == "utf-8-sig"
            return iter(fixture.splitlines())

        def close(self):
            self.closed = True

    response = FakeResponse()
    monkeypatch.setattr("src.ingestion.osha_ita.requests.get", lambda *args, **kwargs: response)

    imported, rejected = import_osha_summary(
        connection,
        settings,
        url=response.url,
    )

    assert imported == 1
    assert rejected == 0
    assert response.closed is True


class _FakeResponse:
    url = "https://geocoding.geo.census.gov/geocoder/locations/address?format=json"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "result": {
                "addressMatches": [
                    {
                        "coordinates": {"x": -71.13429, "y": 41.95676},
                        "matchedAddress": "475 MYLES STANDISH BLVD, TAUNTON, MA, 02780",
                        "tigerLine": {"tigerLineId": "46811527"},
                    }
                ]
            }
        }


class _FakeSession:
    def get(self, *args, **kwargs):
        return _FakeResponse()


def test_census_geocoder_parses_official_response(settings):
    result = geocode_address(
        settings,
        street="475 Myles Standish Blvd",
        city="Taunton",
        state="MA",
        zip_code="02780",
        session=_FakeSession(),
    )
    assert result is not None
    assert result.latitude == pytest.approx(41.95676)
    assert result.tiger_line_id == "46811527"


class _FakeBatchResponse:
    url = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


class _FakeBatchSession:
    def post(self, *args, **kwargs):
        request_bytes = kwargs["files"]["addressFile"][1]
        inputs = csv.reader(io.StringIO(request_bytes.decode("utf-8")))
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        for identifier, street, city, state, zip_code in inputs:
            writer.writerow(
                [
                    identifier,
                    f"{street}, {city}, {state}, {zip_code}",
                    "Match",
                    "Exact",
                    f"{street}, {city}, {state}, {zip_code}",
                    "-70.9750,41.9904",
                    "46811527",
                    "L",
                ]
            )
        return _FakeBatchResponse(output.getvalue().encode("utf-8"))


def test_census_batch_geocoder_updates_many_records_in_one_request(
    connection, settings
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)

    matched, unmatched = geocode_pending_organizations_batch(
        connection,
        settings,
        limit=10,
        session=_FakeBatchSession(),
    )

    assert matched == 3
    assert unmatched == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM organizations WHERE latitude IS NOT NULL"
    ).fetchone()[0] == 3
    assert connection.execute(
        "SELECT COUNT(*) FROM organizations WHERE geographic_tier = 'A'"
    ).fetchone()[0] == 3


def test_acs_connector_requires_key(connection, settings):
    with pytest.raises(ValueError, match="CENSUS_API_KEY"):
        import_acs_massachusetts_places(connection, settings)
