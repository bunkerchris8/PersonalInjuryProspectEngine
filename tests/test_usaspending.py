from __future__ import annotations

from datetime import date

from src.ingestion.usaspending import import_usaspending_contract_recipients


def _award(generated_id: str, award_id: str) -> dict[str, object]:
    return {
        "generated_internal_id": generated_id,
        "Award ID": award_id,
        "Recipient Name": "Bridgewater Manufacturing LLC",
        "Recipient UEI": "ABC123TESTUEI",
        "Recipient Location": {
            "state_code": "MA",
            "city_name": "BRIDGEWATER",
            "address_line1": "100 INDUSTRIAL PARK RD",
            "address_line2": None,
            "address_line3": None,
            "zip5": "02324",
        },
        "Start Date": "2026-01-15",
        "End Date": "2026-12-31",
        "NAICS": {"code": "332710", "description": "Machine Shops"},
        "Description": "Public contract fixture",
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeUSAspendingSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        page = kwargs["json"]["page"]
        if page == 1:
            return _FakeResponse(
                {
                    "results": [_award("CONT_AWD_FIXTURE_1", "FIXTURE-1")],
                    "page_metadata": {"hasNext": True},
                }
            )
        return _FakeResponse(
            {
                "results": [_award("CONT_AWD_FIXTURE_2", "FIXTURE-2")],
                "page_metadata": {"hasNext": False},
            }
        )


def test_usaspending_import_paginates_and_deduplicates_recipients(
    connection, settings
):
    session = _FakeUSAspendingSession()

    stats = import_usaspending_contract_recipients(
        connection,
        settings,
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 16),
        cities=("Bridgewater",),
        session=session,
    )

    assert stats.pages == 2
    assert stats.rows_imported == 2
    assert stats.rows_rejected == 0
    assert stats.organizations_created == 1
    assert stats.organizations_matched == 1
    assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
    assert connection.execute(
        """
        SELECT COUNT(*) FROM source_assertions
        WHERE field_name = 'recipient_uei' AND asserted_value = 'ABC123TESTUEI'
        """
    ).fetchone()[0] == 2
    assert session.requests[0][1]["json"]["filters"]["recipient_locations"] == [
        {"country": "USA", "state": "MA", "city": "Bridgewater"}
    ]


def test_usaspending_rejects_rows_outside_requested_city(connection, settings):
    result = _award("CONT_AWD_OUTSIDE", "OUTSIDE-1")
    result["Recipient Location"]["city_name"] = "BOSTON"

    class OutsideSession:
        def post(self, *args, **kwargs):
            return _FakeResponse(
                {"results": [result], "page_metadata": {"hasNext": False}}
            )

    stats = import_usaspending_contract_recipients(
        connection,
        settings,
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 16),
        cities=("Bridgewater",),
        session=OutsideSession(),
    )

    assert stats.rows_imported == 0
    assert stats.rows_rejected == 1
    assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM research_queue").fetchone()[0] == 1


def test_usaspending_bounds_repetitive_awards_per_recipient(connection, settings):
    class RepetitiveSession:
        def post(self, *args, **kwargs):
            return _FakeResponse(
                {
                    "results": [
                        _award(f"CONT_AWD_REPEAT_{index}", f"REPEAT-{index}")
                        for index in range(5)
                    ],
                    "page_metadata": {"hasNext": False},
                }
            )

    stats = import_usaspending_contract_recipients(
        connection,
        settings,
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 16),
        cities=("Bridgewater",),
        max_awards_per_recipient=3,
        session=RepetitiveSession(),
    )

    assert stats.rows_imported == 3
    assert stats.rows_skipped == 2
    assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3
