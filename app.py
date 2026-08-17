from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from src.config import load_settings
from src.database import connect_database, initialize_schema
from src.database.deployment import (
    deployment_seed_fingerprint,
    deployment_seed_is_current,
    materialize_deployment_seed,
)
from src.database.repository import (
    DEFAULT_EXPORT_FIELDS,
    EXPORT_FIELD_LABELS,
    approved_prospects_csv,
    fetch_approved_prospects_for_export,
    fetch_contacts,
    fetch_events,
    fetch_latest_score_components,
    fetch_organization_detail,
    fetch_provenance,
    fetch_ranked_prospects,
    record_review,
    set_organization_suppression,
)
from src.presentation import (
    CRITERIA_LEVELS,
    build_prospect_table,
    criteria_breakdown,
    criteria_fulfilled_label,
    filter_prospects,
    format_address,
    prospect_type_breakdown,
)


settings = load_settings()
st.set_page_config(
    page_title=settings.app_name,
    page_icon=":material/assured_workload:",
    layout="wide",
)


@st.cache_resource(max_entries=1)
def database_connection(
    database_path: str,
    seed_archive_path: str | None,
    seed_fingerprint: str | None,
):
    loaded_from_seed = materialize_deployment_seed(
        database_path,
        seed_archive_path,
        seed_fingerprint=seed_fingerprint,
    )
    connection = connect_database(database_path)
    initialize_schema(connection)
    return connection, loaded_from_seed


def reset_prospect_filters() -> None:
    st.session_state.minimum_criteria = "Not much"
    st.session_state.prospect_search = ""


def has_value(value: object) -> bool:
    return value is not None and not pd.isna(value) and bool(str(value).strip())


st.title(settings.app_name)
st.caption(
    "Research and human-reviewed relationship development only. No automated outreach, "
    "victim targeting, or legal-ethics determination is performed."
)

flash_message = st.session_state.pop("flash_message", None)
if flash_message:
    st.toast(flash_message, icon=":material/check_circle:")

with st.sidebar:
    st.header("Find prospects")
    prospect_search = st.text_input(
        "Search",
        key="prospect_search",
        placeholder="Name, city, ZIP, or contact",
        icon=":material/search:",
    )
    minimum_criteria = st.select_slider(
        "Minimum criteria fulfilled",
        options=CRITERIA_LEVELS,
        value="Not much",
        key="minimum_criteria",
        help=(
            "This combines verified-source strength, sourced-field coverage, freshness, "
            "identity confidence, and source conflicts."
        ),
    )
    st.caption("Not much means fewer criteria are documented; many means nearly all are.")

seed_archive_path = (
    str(settings.deployment_seed_path) if settings.deployment_seed_path else None
)
seed_fingerprint = deployment_seed_fingerprint(seed_archive_path)
try:
    connection, loaded_from_seed = database_connection(
        str(settings.database_path), seed_archive_path, seed_fingerprint
    )
except (OSError, sqlite3.DatabaseError, ValueError) as exc:
    st.error(
        "The prospect database could not be prepared or opened.",
        icon=":material/error:",
    )
    st.caption(str(exc))
    st.stop()

prospects = pd.DataFrame(fetch_ranked_prospects(connection))
if prospects.empty:
    st.error("No prospect data is loaded.", icon=":material/error:")
    st.write(
        "This deployment connected to an empty database. A Git deployment only receives "
        "tracked repository files; it does not receive the ignored local SQLite database."
    )
    if settings.deployment_seed_path and not settings.deployment_seed_path.is_file():
        st.warning(
            "The configured deployment seed is missing from this checkout. Build it with "
            "`python -m src.cli build-deployment-seed`, commit the resulting archive, and "
            "redeploy.",
            icon=":material/warning:",
        )
    else:
        st.caption(
            "Check the Streamlit deployment logs and confirm the app is running from the "
            "repository root."
        )
    st.stop()

updated_values = (
    pd.to_datetime(prospects["updated_at"], errors="coerce", utc=True)
    if "updated_at" in prospects
    else pd.Series(dtype="datetime64[ns, UTC]")
)
latest_update = updated_values.max() if not updated_values.empty else pd.NaT
data_status = f"{len(prospects):,} prospects loaded"
if not pd.isna(latest_update):
    data_status += f" · latest record update {latest_update.date().isoformat()}"
if deployment_seed_is_current(settings.database_path, seed_fingerprint):
    data_status += f" · bundled snapshot {seed_fingerprint[:8]}"
    if loaded_from_seed:
        data_status += " refreshed"
st.caption(data_status)

filtered = filter_prospects(prospects, minimum_criteria, prospect_search)
filtered_ids = filtered.get("organization_id", pd.Series(dtype=str)).tolist()

if filtered.empty:
    st.info(
        "No prospects match the current search and criteria filter.",
        icon=":material/search:",
    )
    st.button(
        "Clear filters",
        icon=":material/refresh:",
        on_click=reset_prospect_filters,
    )
    st.stop()

approved_rows = fetch_approved_prospects_for_export(connection)
visible_ids = set(filtered_ids)
visible_approved_count = sum(
    row["organization_id"] in visible_ids for row in approved_rows
)

with st.sidebar:
    st.divider()
    st.subheader("Build a CSV list")
    st.caption(
        "Choose any combination of fields. Exports include only approved, "
        "non-suppressed prospects currently shown."
    )
    export_fields = st.multiselect(
        "CSV columns",
        options=list(EXPORT_FIELD_LABELS),
        default=list(DEFAULT_EXPORT_FIELDS),
        format_func=lambda field: EXPORT_FIELD_LABELS[field],
        key="export_fields",
        placeholder="Choose at least one column",
    )
    st.caption(f"{visible_approved_count:,} approved prospects available for this export.")
    if not export_fields:
        st.warning("Choose at least one CSV column.")
        export_data = b""
    else:
        export_data = approved_prospects_csv(
            connection,
            export_fields,
            organization_ids=filtered_ids,
        )
    if visible_approved_count == 0:
        st.info("Approve a prospect after human ethics review to make it exportable.")
    st.download_button(
        "Download custom CSV",
        data=export_data,
        file_name="approved_prospects_custom.csv",
        mime="text/csv",
        disabled=not export_fields or visible_approved_count == 0,
        on_click="ignore",
        icon=":material/download:",
        width="stretch",
    )

complete_addresses = filtered.apply(
    lambda row: all(
        has_value(row.get(field)) for field in ("street", "city", "state", "zip")
    ),
    axis=1,
)
contact_channels = filtered.apply(
    lambda row: any(
        has_value(row.get(field))
        for field in (
            "public_phone",
            "public_email",
            "primary_contact_phone",
            "primary_contact_email",
        )
    ),
    axis=1,
)

metric_columns = st.columns(4)
metric_columns[0].metric("Prospects", f"{len(filtered):,}")
metric_columns[1].metric("Complete addresses", f"{int(complete_addresses.sum()):,}")
metric_columns[2].metric("With contact information", f"{int(contact_channels.sum()):,}")
metric_columns[3].metric("Approved", f"{visible_approved_count:,}")

st.subheader("Prospect breakdown")
st.caption(
    f"These summaries include all {len(filtered):,} prospects currently shown—not only "
    "the prospect selected below."
)
breakdown_columns = st.columns(2)
with breakdown_columns[0]:
    st.markdown("**By Criteria fulfilled**")
    st.bar_chart(
        criteria_breakdown(filtered),
        x="Criteria fulfilled",
        y="Prospects",
        height=260,
    )
with breakdown_columns[1]:
    st.markdown("**By prospect type**")
    st.bar_chart(
        prospect_type_breakdown(filtered),
        x="Prospect type",
        y="Prospects",
        height=260,
    )

st.subheader("Prospects")
st.caption(
    "Addresses and available organization or professional contact channels appear first. "
    "Blank contact fields mean the information has not yet been verified and stored."
)
table = build_prospect_table(filtered)
st.dataframe(
    table,
    hide_index=True,
    height=520,
    placeholder="—",
    key="prospect_table",
    column_config={
        "Organization": st.column_config.TextColumn("Organization", pinned=True),
        "Address": st.column_config.TextColumn("Address", pinned=True, width="large"),
        "Organization phone": st.column_config.TextColumn("Organization phone"),
        "Organization email": st.column_config.TextColumn("Organization email", width="medium"),
        "Primary contact": st.column_config.TextColumn("Primary contact"),
        "Contact role": st.column_config.TextColumn("Contact role", width="medium"),
        "Contact phone": st.column_config.TextColumn("Contact phone"),
        "Contact email": st.column_config.TextColumn("Contact email", width="medium"),
        "Criteria fulfilled": st.column_config.TextColumn("Criteria fulfilled"),
        "Adjusted priority": st.column_config.ProgressColumn(
            "Adjusted priority", min_value=0, max_value=100, format="%.1f"
        ),
    },
)

map_rows = filtered.dropna(subset=["latitude", "longitude"])
st.subheader("Prospect locations")
if map_rows.empty:
    st.info("No mapped coordinates are available for the prospects currently shown.")
else:
    st.caption(
        f"{len(map_rows):,} of {len(filtered):,} visible prospects have verified coordinates. "
        "Their complete mailing addresses remain available in the table and detail view."
    )
    st.map(
        map_rows[["latitude", "longitude"]],
        latitude="latitude",
        longitude="longitude",
    )

st.divider()
st.header("Prospect details and scoring breakdown")
labels = {
    row["organization_id"]: (
        f"{row['canonical_name']} — "
        f"{format_address(row.get('street'), row.get('city'), row.get('state'), row.get('zip'))}"
    )
    for _, row in filtered.iterrows()
}
selected_id = st.selectbox(
    "Choose a prospect",
    options=list(labels),
    format_func=lambda value: labels[value],
    key="selected_prospect",
    help=f"All {len(filtered):,} prospects currently shown are available here.",
)

detail = fetch_organization_detail(connection, selected_id)
if detail is None:
    st.error("The selected prospect could not be loaded. Refresh the page and try again.")
    st.stop()

contacts = fetch_contacts(connection, selected_id)
events = fetch_events(connection, selected_id)
provenance = fetch_provenance(connection, selected_id)
score = fetch_latest_score_components(connection, selected_id)
address = format_address(detail["street"], detail["city"], detail["state"], detail["zip"])
contact_information_available = any(
    has_value(value)
    for value in (
        detail["public_phone"],
        detail["public_email"],
        *(contact.get(field) for contact in contacts for field in ("public_phone", "public_email")),
    )
)

st.subheader(detail["canonical_name"])
detail_metrics = st.columns(4)
detail_metrics[0].metric(
    "Criteria fulfilled",
    criteria_fulfilled_label(detail["data_quality_score"]),
    help=(
        f"Underlying score: {detail['data_quality_score']:.0%}"
        if detail["data_quality_score"] is not None
        else "Not yet assessed"
    ),
)
detail_metrics[1].metric(
    "Adjusted priority",
    f"{detail['adjusted_priority']:.1f}"
    if detail["adjusted_priority"] is not None
    else "Not scored",
)
detail_metrics[2].metric("Address", "Included" if address else "Missing")
detail_metrics[3].metric(
    "Contact information", "Available" if contact_information_available else "Missing"
)
st.caption(
    f"Review status: {detail['review_status'].replace('_', ' ').title()} · "
    f"Distance tier: {detail['geographic_tier'] or 'Unknown'}"
)
st.write(detail["score_explanation"] or "No score explanation is available.")

overview_tab, contacts_tab, events_tab, sources_tab, review_tab = st.tabs(
    ["Overview", "Contact information", "Events", "Sources", "Human review"]
)

with overview_tab:
    overview_columns = st.columns(2)
    with overview_columns[0]:
        with st.container(border=True):
            st.subheader("Primary address")
            if address:
                st.write(f"**{address}**")
            else:
                st.warning("No complete address is stored for this prospect.")
            st.caption(
                f"Distance method: {detail['distance_method'] or 'Unknown'} · "
                f"Estimated driving distance: "
                f"{detail['estimated_driving_distance']:.1f} miles"
                if detail["estimated_driving_distance"] is not None
                else "Distance method and estimated driving distance are unavailable."
            )
    with overview_columns[1]:
        with st.container(border=True):
            st.subheader("Organization contact")
            st.write(f"**Phone:** {detail['public_phone'] or 'Not available'}")
            st.write(f"**Email:** {detail['public_email'] or 'Not available'}")
            if detail["website"]:
                st.link_button(
                    "Open organization website",
                    detail["website"],
                    icon=":material/open_in_new:",
                )
            else:
                st.caption("No verified organization website is stored.")

    facts = {
        "Prospect type": str(detail["organization_type"]).replace("_", " ").title(),
        "Industry": detail["industry"],
        "Union affiliation": detail["union_affiliation"],
        "Local number": detail["local_number"],
        "Active status": detail["active_status"],
    }
    st.subheader("Organization facts")
    st.dataframe(
        pd.DataFrame(
            [{"Field": key, "Value": value or "Not available"} for key, value in facts.items()]
        ),
        hide_index=True,
    )

    st.subheader(f"Scoring breakdown for {detail['canonical_name']}")
    if score:
        components = pd.DataFrame(
            {
                "Component": [
                    "Workforce relevance",
                    "Organizational reach",
                    "Role influence",
                    "Proximity",
                    "Public accessibility",
                    "Relationship potential",
                ],
                "Points": [
                    score["workforce_relevance"],
                    score["organizational_reach"],
                    score["role_influence"],
                    score["proximity"],
                    score["public_accessibility"],
                    score["relationship_potential"],
                ],
            }
        )
        st.bar_chart(components, x="Component", y="Points", horizontal=True, height=340)
    else:
        st.info("This prospect has not yet been scored.")

with contacts_tab:
    st.subheader("Organization-level contact")
    organization_contact = pd.DataFrame(
        [
            {
                "Organization": detail["canonical_name"],
                "Phone": detail["public_phone"] or "",
                "Email": detail["public_email"] or "",
                "Website": detail["website"] or "",
                "Address": address,
            }
        ]
    )
    st.dataframe(
        organization_contact,
        hide_index=True,
        placeholder="—",
        column_config={"Website": st.column_config.LinkColumn("Website")},
    )

    st.subheader("Public professional contacts")
    if contacts:
        contact_frame = pd.DataFrame(contacts)[
            [
                "display_name",
                "role_title",
                "public_phone",
                "public_email",
                "professional_url",
                "current_status",
                "verification_status",
                "do_not_contact",
            ]
        ].rename(
            columns={
                "display_name": "Name",
                "role_title": "Role",
                "public_phone": "Phone",
                "public_email": "Email",
                "professional_url": "Professional profile",
                "current_status": "Role status",
                "verification_status": "Verification",
                "do_not_contact": "Do not contact",
            }
        )
        st.dataframe(
            contact_frame,
            hide_index=True,
            placeholder="—",
            column_config={
                "Professional profile": st.column_config.LinkColumn("Professional profile")
            },
        )
    else:
        st.info(
            "No verified person-level professional contacts are stored for this prospect. "
            "Organization-level contact information, when available, appears above."
        )

with events_tab:
    st.subheader("Events and program windows")
    if events:
        event_frame = pd.DataFrame(events)[
            [
                "event_name",
                "starts_at",
                "event_type",
                "venue_name",
                "street",
                "city",
                "accessibility_status",
                "permission_required",
                "freshness_status",
                "event_url",
            ]
        ].rename(
            columns={
                "event_name": "Event",
                "starts_at": "Starts",
                "event_type": "Type",
                "venue_name": "Venue",
                "street": "Street",
                "city": "City",
                "accessibility_status": "Accessibility",
                "permission_required": "Permission required",
                "freshness_status": "Freshness",
                "event_url": "Event URL",
            }
        )
        st.dataframe(
            event_frame,
            hide_index=True,
            placeholder="—",
            column_config={"Event URL": st.column_config.LinkColumn("Event URL")},
        )
    else:
        st.info("No current events are stored for this prospect.")

with sources_tab:
    if provenance:
        provenance_frame = pd.DataFrame(provenance)[
            [
                "field_name",
                "asserted_value",
                "publisher",
                "dataset_or_page_title",
                "source_strength",
                "retrieval_date",
                "validation_status",
                "freshness_expires_at",
                "conflict_group",
                "source_url",
            ]
        ].rename(
            columns={
                "field_name": "Field",
                "asserted_value": "Value",
                "publisher": "Publisher",
                "dataset_or_page_title": "Source title",
                "source_strength": "Source strength",
                "retrieval_date": "Retrieved",
                "validation_status": "Validation",
                "freshness_expires_at": "Freshness expires",
                "conflict_group": "Conflict group",
                "source_url": "Source URL",
            }
        )
        st.dataframe(
            provenance_frame,
            hide_index=True,
            placeholder="—",
            column_config={"Source URL": st.column_config.LinkColumn("Source URL")},
        )
    else:
        st.warning("No provenance assertions are attached to this record.")

with review_tab:
    st.warning(
        "Review and suppression changes are stored in the configured SQLite file. "
        "Streamlit Community Cloud does not guarantee persistence for local files, so "
        "configure durable external storage before treating these decisions as a system "
        "of record.",
        icon=":material/warning:",
    )
    if detail["do_not_contact"]:
        st.error("This organization is suppressed and must not be contacted.")
        if st.button(
            "Lift suppression",
            type="secondary",
            key=f"lift_suppression_{selected_id}",
        ):
            set_organization_suppression(
                connection,
                selected_id,
                active=False,
                reason="Human reviewer lifted suppression",
            )
            st.session_state.flash_message = "Suppression lifted."
            st.rerun()
    else:
        with st.form(f"suppression_form_{selected_id}"):
            suppression_reason = st.text_input("Suppression reason")
            add_suppression = st.form_submit_button("Add suppression", type="secondary")
        if add_suppression:
            try:
                set_organization_suppression(
                    connection,
                    selected_id,
                    active=True,
                    reason=suppression_reason,
                )
                st.session_state.flash_message = "Suppression added."
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    with st.form(f"review_form_{selected_id}"):
        reviewer = st.text_input("Reviewer")
        notes = st.text_area("Review notes")
        ethics_review = st.checkbox(
            "I completed a human ethics review, including Massachusetts Rule 7.3 and "
            "relevant advertising rules, for the proposed relationship-development approach."
        )
        review_columns = st.columns(2)
        approve = review_columns[0].form_submit_button(
            "Approve for human outreach", type="primary", width="stretch"
        )
        reject = review_columns[1].form_submit_button("Reject", width="stretch")

    if approve:
        try:
            record_review(
                connection,
                selected_id,
                "approved",
                reviewer=reviewer,
                notes=notes,
                ethics_review_completed=ethics_review,
            )
            st.session_state.flash_message = "Prospect approved after human review."
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if reject:
        record_review(
            connection,
            selected_id,
            "rejected",
            reviewer=reviewer,
            notes=notes,
            ethics_review_completed=ethics_review,
        )
        st.session_state.flash_message = "Prospect rejected."
        st.rerun()
