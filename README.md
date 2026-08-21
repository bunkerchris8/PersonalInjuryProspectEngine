# Personal Injury Prospect Engine

A local, explainable research and human-review application for organizational relationship development around Bridgewater, Massachusetts.

The application ranks organizations, not accident victims or people believed to have legal claims. It stores only sourced organizational facts and public professional contact information tied to a current role. It does not send outreach or decide whether any outreach method is ethically permissible.

## MVP capabilities

- SQLite schema with organizations, contacts, roles, events, provenance assertions, scores, review decisions, suppressions, OSHA metrics, ACS geographies, and ingestion audit runs.
- Configurable 45-mile default scope and 60-mile maximum, centered on Bridgewater.
- Conservative matching with no name-only automatic merges.
- Strength 1 quarantine and Strength 3 minimum for automated scoring.
- Separate transparent prospect and data-quality scores.
- Credential-free U.S. Census single-address and 10,000-row batch geocoder integration.
- Incremental OSHA ITA 300A summary importer for current CSV and historical ZIP files that never reads case-detail narratives.
- Paginated USAspending importer for recent local federal contract recipients, with exact award provenance and bounded per-recipient retention.
- Optional ACS place-level importer with margins-of-error stability flags.
- Streamlit Criteria fulfilled filtering, complete address and contact views, prospect breakdowns, map, provenance, human review, suppression, and customizable approved-only CSV export.
- A small bootstrap dataset from official union pages for interface validation. It is not a production prospect list.

## Setup

Python 3.12 is already used by the repository's `.venv` and is suitable for a 2019 Intel MacBook Air.

```bash
cd /Users/chrischris/Desktop/XCode/PersonalInjuryProspectEngine
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.cli bootstrap
```

The bootstrap command creates `data/processed/prospects.db`, imports the included official-page sample rows, and calculates scores. It is idempotent for organizations.

The bootstrap remains intentionally small. The bulk workflow below has been verified with more than 1,200 deduplicated, scored organizations in the configured Bridgewater-area cities.

Geocode records through the official Census geocoder, then rescore:

```bash
python -m src.cli geocode --limit 50
```

For hundreds or thousands of addresses, use the official Census batch endpoint instead of one request per address:

```bash
python -m src.cli geocode --batch --limit 10000
```

Run the review dashboard:

```bash
streamlit run app.py
```

Run tests:

```bash
pytest -q
```

## Deploy on Streamlit Community Cloud

Community Cloud receives the files tracked by Git. The working database at
`data/processed/prospects.db` is intentionally ignored, so deploying the repository without
a seed archive creates a valid but empty database.

After refreshing the local data, build the sanitized deployment snapshot:

```bash
python -m src.cli build-deployment-seed
git add data/deployment/prospects.db.gz
git commit -m "Refresh deployment prospect data"
git push
```

The app fingerprints that archive and expands it into the ignored runtime database when the
database is missing or the committed snapshot changes. This makes data-only Git pushes
visible without manually deleting the old runtime database. Any in-session human review,
outreach, and suppression records are carried forward when the snapshot refreshes. A local
working database with ingestion history is never replaced. The snapshot builder excludes
quarantined research rows and ingestion audit records. It also refuses to build if reviews,
outreach history, or suppressions exist, preventing operational review data from being
published accidentally.

To test the exact clean-deployment path locally, point the app at a new runtime file:

```bash
PROSPECT_ENGINE_DATABASE_PATH=/tmp/prospect-engine/prospects.db streamlit run app.py
```

The bundled snapshot makes the prospect dashboard available after each deployment, but
review and suppression changes still write to local SQLite. Streamlit Community Cloud does
not guarantee persistence for local files. Use durable external storage before relying on
those changes as the system of record.

The public dashboard runs in preview mode and exposes at most 20 matching records. Entering
the owner-provided access code unlocks all matching records for that browser session, still
paginated at 20 records per page. The code is compared server-side and is never placed in the
URL. To rotate it without changing the repository, add this root-level value in Streamlit
Community Cloud under **App settings → Secrets**:

```toml
PROSPECT_ENGINE_PREVIEW_UNLOCK_CODE = "replace-with-a-new-code"
```

For local development, the same setting can be added to the ignored `.env` file. This preview
gate is a lightweight content control, not user authentication; use an identity-aware hosting
or authentication layer if the underlying records become confidential.

## Import commands

Blank templates live in `data/raw/templates/`. Every imported material fact requires source metadata, including URL, publisher, title, retrieval date, source strength, source type, raw identifier, and validation status.

```bash
python -m src.cli import-organizations data/raw/templates/organizations.csv
python -m src.cli import-contacts data/raw/templates/contacts.csv
python -m src.cli import-events data/raw/templates/events.csv
python -m src.cli score
```

The current gap-enrichment bundle adds 40 verified organization-level records spanning
municipal and state veteran services, municipal disability commissions, county and regional
agriculture networks, tradeswomen organizations, women-in-construction chapters, and state or
regional building-trades councils. It contains only public organizational and role-based
contact channels; it does not identify or characterize individual veterans, people with
disabilities, farmers, students, union members, workers, or community participants.

```bash
python -m src.cli import-organizations data/curated/verified_organization_contacts_2026-08-18_round24.csv
python -m src.cli import-contacts data/curated/verified_role_contacts_2026-08-18_round24.csv
python -m src.cli geocode --batch --limit 10000
python -m src.cli score
python -m src.cli build-deployment-seed
```

For a manually downloaded OSHA 300A summary CSV:

```bash
python -m src.cli import-osha --file /absolute/path/to/ITA_300A_Summary.csv
```

For the current configured official OSHA URL:

```bash
python -m src.cli import-osha --url "https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2025_through_03-15-2026_v2.csv"
```

The importer streams rows and defaults to Massachusetts priority cities from `config/settings.toml`. Repeat `--city` to set an explicit city filter.

Historical official ZIP archives are supported directly and are read from temporary storage rather than committed to the repository:

```bash
python -m src.cli import-osha \
  --url "https://www.osha.gov/sites/default/files/ITA_300A_Summary_Data_2024_through_12-31-2025.zip"
```

Import recent federal contract recipients from the official USAspending API:

```bash
python -m src.cli import-usaspending
```

The default query covers the previous three calendar years and all configured priority cities. It retains the three newest award sources per UEI, omits award values, and deduplicates recipients against existing organizations. If the command reports the API result cap, narrow the query by date or run one city at a time:

```bash
python -m src.cli import-usaspending \
  --start-date 2025-01-01 \
  --city Bridgewater
```

Older repetitive USAspending snapshots can be compacted without deleting any prospect:

```bash
python -m src.cli compact-usaspending-sources --keep-per-recipient 3
```

The current Census Data API requires a free key. Add it to `.env` without committing the file:

```bash
cp .env.example .env
python -m src.cli import-acs
```

## Geographic method

The center and tiers are configured in `config/settings.toml`. The MVP stores straight-line distance and a clearly labeled driving-distance estimate calculated with a configurable road factor. This estimate controls tiers until a dependable routing source is added. It must not be represented as a routed mileage calculation.

- Tier A: up to 15 estimated driving miles
- Tier B: over 15 through 30 miles
- Tier C: over 30 through 45 miles
- Tier D: over 45 through 60 miles

## Review controls

Approval requires a human ethics-review acknowledgement. A suppression immediately sets `do_not_contact`, prevents approval, and removes the organization from approved export. Lifting a suppression returns the record to pending review. No outreach transport exists in this version.

The dashboard presents the technical data-quality score as **Criteria fulfilled** on a plain-language scale from **Not much** through **Many**. The single prospect filter uses that scale. CSV exports can include any selected combination of prospect, address, organization-contact, professional-contact, scoring, and review fields, but they remain restricted to approved, non-suppressed prospects in the current Criteria fulfilled view.

Read these documents before using the output:

- [Source catalog](docs/SOURCE_CATALOG.md)
- [Scoring model](docs/SCORING_MODEL.md)
- [Compliance and limitations](docs/COMPLIANCE_AND_LIMITATIONS.md)

## Repository layout

```text
app.py                         Streamlit review application
config/settings.toml           geography, scoring, and source configuration
data/raw/                      committed samples/templates; bulk files ignored
data/processed/                local databases and outputs; ignored
src/database/                  schema and review repository
src/ingestion/                 CSV, Census, ACS, and OSHA adapters
src/normalization/             entity matching and geographic calculations
src/scoring/                   deterministic prospect and quality scores
src/validation/                privacy, source, and freshness rules
tests/                         unit and vertical-slice tests
docs/                          source, scoring, and compliance documentation
```

Downloaded bulk datasets, local databases, `.env`, and secrets are excluded by `.gitignore`.
