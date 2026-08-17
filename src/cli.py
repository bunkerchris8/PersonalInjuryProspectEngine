from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.config import PROJECT_ROOT, load_settings
from src.database import connect_database, initialize_schema
from src.ingestion.census_acs import import_acs_massachusetts_places
from src.ingestion.census_geocoder import (
    geocode_pending_organizations,
    geocode_pending_organizations_batch,
)
from src.ingestion.csv_importer import import_contacts_csv, import_events_csv, import_organizations_csv
from src.ingestion.osha_ita import import_osha_summary
from src.ingestion.usaspending import (
    import_usaspending_contract_recipients,
    prune_usaspending_sources,
)
from src.scoring.service import score_all_organizations


SAMPLE_ORGANIZATIONS = PROJECT_ROOT / "data" / "raw" / "sample_organizations.csv"
SAMPLE_CONTACTS = PROJECT_ROOT / "data" / "raw" / "sample_contacts.csv"
SAMPLE_EVENTS = PROJECT_ROOT / "data" / "raw" / "sample_events.csv"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local, human-reviewed organizational prospect research engine."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create or validate the SQLite schema.")
    subparsers.add_parser(
        "bootstrap", help="Initialize and import the included official-page sample records."
    )

    for name in ("import-organizations", "import-contacts", "import-events"):
        command = subparsers.add_parser(name)
        command.add_argument("path", type=Path)

    geocode = subparsers.add_parser("geocode")
    geocode.add_argument("--limit", type=int, default=50)
    geocode.add_argument(
        "--batch",
        action="store_true",
        help="Use one official Census batch request (up to 10,000 addresses).",
    )

    osha = subparsers.add_parser("import-osha")
    source = osha.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--url")
    osha.add_argument("--city", action="append", dest="cities")

    usaspending = subparsers.add_parser("import-usaspending")
    usaspending.add_argument("--start-date", type=date.fromisoformat)
    usaspending.add_argument("--end-date", type=date.fromisoformat)
    usaspending.add_argument("--city", action="append", dest="cities")
    usaspending.add_argument("--max-pages", type=int, default=100)
    usaspending.add_argument("--awards-per-recipient", type=int, default=3)

    compact = subparsers.add_parser("compact-usaspending-sources")
    compact.add_argument("--keep-per-recipient", type=int, default=3)

    subparsers.add_parser("import-acs")
    subparsers.add_parser("score")
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = load_settings()
    connection = connect_database(settings.database_path)
    initialize_schema(connection)
    try:
        if args.command == "init-db":
            print(f"Initialized {settings.database_path}")
        elif args.command == "bootstrap":
            organizations = import_organizations_csv(
                connection, SAMPLE_ORGANIZATIONS, settings
            )
            contacts = import_contacts_csv(connection, SAMPLE_CONTACTS)
            events = import_events_csv(connection, SAMPLE_EVENTS)
            scored = score_all_organizations(connection, settings)
            print(
                "Bootstrap complete: "
                f"{organizations.rows_imported} organization rows, "
                f"{contacts.rows_imported} contact rows, "
                f"{events.rows_imported} event rows, {scored} organizations scored."
            )
        elif args.command == "import-organizations":
            stats = import_organizations_csv(connection, args.path, settings)
            print(vars(stats))
        elif args.command == "import-contacts":
            stats = import_contacts_csv(connection, args.path)
            print(vars(stats))
        elif args.command == "import-events":
            stats = import_events_csv(connection, args.path)
            print(vars(stats))
        elif args.command == "geocode":
            geocoder = (
                geocode_pending_organizations_batch
                if args.batch
                else geocode_pending_organizations
            )
            matched, unmatched = geocoder(connection, settings, limit=args.limit)
            scored = score_all_organizations(connection, settings)
            print(f"Geocoded {matched}; unmatched {unmatched}; rescored {scored}.")
        elif args.command == "import-osha":
            cities = tuple(args.cities) if args.cities else None
            imported, rejected = import_osha_summary(
                connection,
                settings,
                file_path=args.file,
                url=args.url,
                cities=cities,
            )
            scored = score_all_organizations(connection, settings)
            print(f"Imported {imported} OSHA rows; rejected {rejected}; rescored {scored}.")
        elif args.command == "import-acs":
            imported = import_acs_massachusetts_places(connection, settings)
            scored = score_all_organizations(connection, settings)
            print(f"Imported {imported} ACS place records; rescored {scored}.")
        elif args.command == "import-usaspending":
            cities = tuple(args.cities) if args.cities else None
            stats = import_usaspending_contract_recipients(
                connection,
                settings,
                start_date=args.start_date,
                end_date=args.end_date,
                cities=cities,
                max_pages=args.max_pages,
                max_awards_per_recipient=args.awards_per_recipient,
            )
            scored = score_all_organizations(connection, settings)
            print(
                f"Imported {stats.rows_imported} federal contract award records "
                f"from {stats.pages} pages; created {stats.organizations_created} "
                f"organizations, matched {stats.organizations_matched}, rejected "
                f"{stats.rows_rejected}, skipped {stats.rows_skipped} repetitive awards, "
                f"pruned {stats.sources_pruned} older award sources; rescored {scored}."
            )
            if stats.result_cap_reached:
                print(
                    "WARNING: USAspending reached the configured result cap. "
                    "Repeat with a narrower date range or one --city at a time."
                )
        elif args.command == "compact-usaspending-sources":
            sources, assertions = prune_usaspending_sources(
                connection,
                keep_per_recipient=args.keep_per_recipient,
            )
            connection.commit()
            scored = score_all_organizations(connection, settings)
            connection.execute("VACUUM")
            print(
                f"Removed {sources} repetitive federal award sources and "
                f"{assertions} assertions; rescored {scored} organizations."
            )
        elif args.command == "score":
            print(f"Scored {score_all_organizations(connection, settings)} organizations.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
