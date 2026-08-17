from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_reference TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    rows_seen INTEGER NOT NULL DEFAULT 0,
    rows_imported INTEGER NOT NULL DEFAULT 0,
    rows_queued INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    parameters_json TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    publisher TEXT NOT NULL,
    dataset_or_page_title TEXT NOT NULL,
    retrieval_date TEXT NOT NULL,
    publication_or_filing_date TEXT,
    source_strength INTEGER NOT NULL CHECK (source_strength BETWEEN 1 AND 5),
    source_type TEXT NOT NULL,
    raw_source_identifier TEXT,
    extraction_method TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    ingestion_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_url, retrieval_date, raw_source_identifier),
    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_runs(ingestion_run_id)
);

CREATE TABLE IF NOT EXISTS census_geographies (
    census_geography_id TEXT PRIMARY KEY,
    geography_type TEXT NOT NULL,
    geography_name TEXT NOT NULL,
    state_fips TEXT,
    county_fips TEXT,
    place_fips TEXT,
    tract_fips TEXT,
    zcta TEXT,
    latitude REAL,
    longitude REAL,
    acs_vintage INTEGER,
    population INTEGER,
    labor_force_size INTEGER,
    median_age REAL,
    population_age_40_plus_pct REAL,
    workers_age_45_plus_pct REAL,
    construction_maintenance_pct REAL,
    production_transportation_pct REAL,
    relevant_workforce_pct REAL,
    margins_of_error_json TEXT,
    estimate_stability TEXT,
    source_id TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    UNIQUE(geography_type, state_fips, county_fips, place_fips, tract_fips, zcta, acs_vintage),
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS organizations (
    organization_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    organization_type TEXT NOT NULL,
    industry TEXT,
    union_affiliation TEXT,
    local_number TEXT,
    official_identifier TEXT,
    website TEXT,
    public_phone TEXT,
    public_email TEXT,
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    latitude REAL,
    longitude REAL,
    straight_line_distance REAL,
    estimated_driving_distance REAL,
    distance_method TEXT,
    geographic_tier TEXT CHECK (geographic_tier IN ('A', 'B', 'C', 'D', 'OUTSIDE') OR geographic_tier IS NULL),
    estimated_reach INTEGER,
    active_status TEXT NOT NULL DEFAULT 'unknown',
    public_accessibility INTEGER NOT NULL DEFAULT 0 CHECK (public_accessibility IN (0, 1)),
    active_program INTEGER NOT NULL DEFAULT 0 CHECK (active_program IN (0, 1)),
    entity_match_confidence REAL CHECK (entity_match_confidence BETWEEN 0 AND 1),
    census_geography_id TEXT,
    data_quality_score REAL CHECK (data_quality_score BETWEEN 0 AND 1),
    raw_prospect_score REAL CHECK (raw_prospect_score BETWEEN 0 AND 100),
    adjusted_priority REAL CHECK (adjusted_priority BETWEEN 0 AND 100),
    score_explanation TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'research_only', 'needs_review', 'approved', 'rejected', 'suppressed')),
    do_not_contact INTEGER NOT NULL DEFAULT 0 CHECK (do_not_contact IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (census_geography_id) REFERENCES census_geographies(census_geography_id)
);

CREATE TABLE IF NOT EXISTS locations (
    location_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    location_type TEXT NOT NULL DEFAULT 'primary',
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    latitude REAL,
    longitude REAL,
    straight_line_distance REAL,
    estimated_driving_distance REAL,
    distance_method TEXT,
    geographic_tier TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    source_assertion_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    display_name TEXT,
    contact_scope TEXT NOT NULL CHECK (contact_scope IN ('role_based', 'named_professional')),
    public_email TEXT,
    public_phone TEXT,
    professional_url TEXT,
    verification_status TEXT NOT NULL,
    last_verified_at TEXT,
    do_not_contact INTEGER NOT NULL DEFAULT 0 CHECK (do_not_contact IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    role_title TEXT NOT NULL,
    role_date TEXT,
    filing_year INTEGER,
    current_status TEXT NOT NULL CHECK (current_status IN ('current', 'stale', 'unknown')),
    source_assertion_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS organization_aliases (
    alias_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    source_assertion_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, normalized_alias),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_type TEXT,
    starts_at TEXT,
    ends_at TEXT,
    recurrence_text TEXT,
    venue_name TEXT,
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    event_url TEXT,
    accessibility_status TEXT NOT NULL,
    permission_required INTEGER NOT NULL DEFAULT 1 CHECK (permission_required IN (0, 1)),
    freshness_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workforce_metrics (
    workforce_metric_id TEXT PRIMARY KEY,
    organization_id TEXT,
    census_geography_id TEXT,
    metric_date TEXT,
    workforce_size INTEGER,
    relevant_workforce_pct REAL,
    workers_age_45_plus_pct REAL,
    metric_context TEXT,
    source_assertion_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE,
    FOREIGN KEY (census_geography_id) REFERENCES census_geographies(census_geography_id)
);

CREATE TABLE IF NOT EXISTS osha_metrics (
    osha_metric_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    establishment_id TEXT,
    filing_year INTEGER NOT NULL,
    naics_code TEXT,
    annual_average_employees INTEGER,
    total_hours_worked REAL,
    total_recordable_cases INTEGER,
    dart_cases INTEGER,
    total_case_rate REAL,
    dart_rate REAL,
    inspection_count INTEGER,
    latest_inspection_date TEXT,
    violation_present INTEGER,
    case_status TEXT,
    source_assertion_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(establishment_id, filing_year),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_assertions (
    assertion_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    asserted_value TEXT,
    relevant_source_excerpt TEXT,
    structured_field_name TEXT,
    validation_status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    freshness_expires_at TEXT,
    conflict_group TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS scores (
    score_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    scoring_version TEXT NOT NULL,
    workforce_relevance REAL NOT NULL,
    organizational_reach REAL NOT NULL,
    role_influence REAL NOT NULL,
    proximity REAL NOT NULL,
    public_accessibility REAL NOT NULL,
    relationship_potential REAL NOT NULL,
    raw_prospect_score REAL NOT NULL,
    data_quality_score REAL NOT NULL,
    adjusted_priority REAL NOT NULL,
    explanation TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outreach_reviews (
    review_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('pending', 'approved', 'rejected')),
    reviewer TEXT,
    review_notes TEXT,
    ethics_review_completed INTEGER NOT NULL DEFAULT 0 CHECK (ethics_review_completed IN (0, 1)),
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS outreach_history (
    outreach_history_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    contact_id TEXT,
    review_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    channel TEXT,
    outcome TEXT,
    notes TEXT,
    occurred_at TEXT NOT NULL,
    recorded_by TEXT,
    initiated_by_human INTEGER NOT NULL DEFAULT 1 CHECK (initiated_by_human = 1),
    FOREIGN KEY (organization_id) REFERENCES organizations(organization_id) ON DELETE CASCADE,
    FOREIGN KEY (contact_id) REFERENCES contacts(contact_id),
    FOREIGN KEY (review_id) REFERENCES outreach_reviews(review_id)
);

CREATE TABLE IF NOT EXISTS suppressions (
    suppression_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('organization', 'contact')),
    entity_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human_review',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    suppressed_at TEXT NOT NULL,
    expires_at TEXT,
    lifted_at TEXT
);

CREATE TABLE IF NOT EXISTS research_queue (
    research_queue_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    display_name TEXT,
    reason TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    source_url TEXT,
    source_strength INTEGER,
    ingestion_run_id TEXT,
    status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_runs(ingestion_run_id)
);

CREATE INDEX IF NOT EXISTS idx_organizations_priority ON organizations(adjusted_priority DESC);
CREATE INDEX IF NOT EXISTS idx_organizations_normalized ON organizations(normalized_name, city, state);
CREATE INDEX IF NOT EXISTS idx_organizations_review ON organizations(review_status, do_not_contact);
CREATE INDEX IF NOT EXISTS idx_aliases_normalized ON organization_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_assertions_entity ON source_assertions(entity_type, entity_id, field_name);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_contacts_org ON contacts(organization_id);
CREATE INDEX IF NOT EXISTS idx_suppressions_entity ON suppressions(entity_type, entity_id, active);
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT OR IGNORE INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
    )
    connection.commit()
