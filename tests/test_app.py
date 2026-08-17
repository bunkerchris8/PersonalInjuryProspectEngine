from __future__ import annotations

from streamlit.testing.v1 import AppTest

from src.config import PROJECT_ROOT
from src.database.repository import record_review
from src.ingestion.csv_importer import import_contacts_csv, import_events_csv, import_organizations_csv
from src.scoring.service import score_all_organizations


SAMPLES = PROJECT_ROOT / "data" / "raw"


def test_streamlit_empty_database_explains_missing_deployment_data(
    settings, monkeypatch
):
    monkeypatch.setenv("PROSPECT_ENGINE_DATABASE_PATH", str(settings.database_path))
    monkeypatch.setenv("PROSPECT_ENGINE_DEPLOYMENT_SEED_PATH", "")

    app = AppTest.from_file(PROJECT_ROOT / "app.py", default_timeout=15).run()

    assert not app.exception
    assert app.error[0].value == "No prospect data is loaded."
    assert "ignored local SQLite database" in app.markdown[0].value


def test_streamlit_app_features_and_interactions(
    connection, settings, monkeypatch
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    import_events_csv(connection, SAMPLES / "sample_events.csv")
    score_all_organizations(connection, settings)

    ids = {
        row["canonical_name"]: row["organization_id"]
        for row in connection.execute(
            "SELECT canonical_name, organization_id FROM organizations"
        )
    }
    connection.execute(
        "UPDATE organizations SET data_quality_score = 0.60 WHERE canonical_name = ?",
        ("Laborers Local 876",),
    )
    connection.commit()
    record_review(
        connection,
        ids["IBEW Local 223"],
        "approved",
        reviewer="UI test reviewer",
        ethics_review_completed=True,
    )

    monkeypatch.setenv("PROSPECT_ENGINE_DATABASE_PATH", str(settings.database_path))
    app = AppTest.from_file(PROJECT_ROOT / "app.py", default_timeout=15).run()

    assert not app.exception
    assert app.metric[0].label == "Prospects"
    assert app.metric[0].value == "3"
    assert app.metric[1].label == "Complete addresses"
    assert app.metric[1].value == "3"
    assert app.metric[3].label == "Approved"
    assert app.metric[3].value == "1"
    assert len(app.select_slider) == 1
    assert app.select_slider[0].options == [
        "Not much",
        "A little",
        "Some",
        "Most",
        "Many",
    ]
    assert not app.slider
    assert len(app.selectbox[0].options) == 3
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "Contact information",
        "Events",
        "Sources",
        "Human review",
    ]

    prospect_table = app.dataframe[0].value
    assert len(prospect_table) == 3
    assert "Address" in prospect_table
    assert "Organization phone" in prospect_table
    assert "Contact email" in prospect_table
    assert prospect_table["Address"].str.len().gt(0).all()

    app.select_slider[0].set_value("Many").run()
    assert not app.exception
    assert app.metric[0].value == "2"
    assert len(app.selectbox[0].options) == 2

    reviewer = next(widget for widget in app.text_input if widget.label == "Reviewer")
    reviewer.set_value("Should not carry over").run()
    assert next(
        widget for widget in app.text_input if widget.label == "Reviewer"
    ).value == "Should not carry over"

    app.selectbox[0].set_value(ids["Teamsters Local 653"]).run()
    assert not app.exception
    assert "Scoring breakdown for Teamsters Local 653" in [
        subheader.value for subheader in app.subheader
    ]
    assert next(
        widget for widget in app.text_input if widget.label == "Reviewer"
    ).value == ""

    app.multiselect[0].set_value(["prospect_name", "organization_phone"]).run()
    assert app.multiselect[0].value == ["prospect_name", "organization_phone"]
    assert not app.exception

    app.sidebar.text_input[0].set_value("no matching prospect").run()
    assert app.info[0].value == "No prospects match the current search and criteria filter."
    next(button for button in app.button if button.label == "Clear filters").click().run()
    assert not app.exception
    assert app.metric[0].value == "3"


def test_streamlit_review_suppression_and_export_controls(
    connection, settings, monkeypatch
):
    import_organizations_csv(connection, SAMPLES / "sample_organizations.csv", settings)
    import_contacts_csv(connection, SAMPLES / "sample_contacts.csv")
    score_all_organizations(connection, settings)
    selected_id = connection.execute(
        "SELECT organization_id FROM organizations ORDER BY adjusted_priority DESC LIMIT 1"
    ).fetchone()[0]

    monkeypatch.setenv("PROSPECT_ENGINE_DATABASE_PATH", str(settings.database_path))
    app = AppTest.from_file(PROJECT_ROOT / "app.py", default_timeout=15).run()
    download = app.get("download_button")[0]
    assert download.disabled is True

    next(widget for widget in app.text_input if widget.label == "Reviewer").set_value(
        "UI reviewer"
    )
    app.checkbox[0].set_value(True)
    next(
        button for button in app.button if button.label == "Approve for human outreach"
    ).click().run()

    assert not app.exception
    assert connection.execute(
        "SELECT review_status FROM organizations WHERE organization_id = ?",
        (selected_id,),
    ).fetchone()[0] == "approved"
    assert app.get("download_button")[0].disabled is False

    next(
        widget for widget in app.text_input if widget.label == "Suppression reason"
    ).set_value("Do not contact request")
    next(button for button in app.button if button.label == "Add suppression").click().run()

    assert not app.exception
    suppressed = connection.execute(
        "SELECT review_status, do_not_contact FROM organizations WHERE organization_id = ?",
        (selected_id,),
    ).fetchone()
    assert tuple(suppressed) == ("suppressed", 1)
    assert app.get("download_button")[0].disabled is True

    next(button for button in app.button if button.label == "Lift suppression").click().run()

    assert not app.exception
    lifted = connection.execute(
        "SELECT review_status, do_not_contact FROM organizations WHERE organization_id = ?",
        (selected_id,),
    ).fetchone()
    assert tuple(lifted) == ("pending", 0)

    next(button for button in app.button if button.label == "Reject").click().run()

    assert not app.exception
    assert connection.execute(
        "SELECT review_status FROM organizations WHERE organization_id = ?",
        (selected_id,),
    ).fetchone()[0] == "rejected"
