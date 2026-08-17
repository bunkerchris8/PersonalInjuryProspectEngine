from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.toml"


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_path: Path
    default_max_radius_miles: float
    allowed_max_radius_miles: float
    center_name: str
    center_latitude: float
    center_longitude: float
    estimated_driving_distance_factor: float
    tier_a_max_miles: float
    tier_b_max_miles: float
    tier_c_max_miles: float
    tier_d_max_miles: float
    priority_cities: tuple[str, ...]
    scoring_version: str
    minimum_scoring_source_strength: int
    massachusetts_rule_7_3_url: str
    olms_disclosure_url: str
    osha_ita_page_url: str
    osha_summary_url: str
    census_acs_year: int
    census_acs_base_url: str
    census_geocoder_url: str
    usaspending_api_url: str
    usaspending_lookback_years: int
    census_api_key: str | None


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    path = config_path or DEFAULT_CONFIG_PATH
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    app = raw["app"]
    geography = raw["geography"]
    scoring = raw["scoring"]
    sources = raw["official_sources"]

    radius = float(
        os.getenv(
            "PROSPECT_ENGINE_MAX_RADIUS_MILES",
            app["default_max_radius_miles"],
        )
    )
    allowed_radius = float(app["allowed_max_radius_miles"])
    if not 0 < radius <= allowed_radius:
        raise ValueError(
            f"Maximum radius must be greater than 0 and no more than {allowed_radius:g} miles."
        )

    database_value = os.getenv(
        "PROSPECT_ENGINE_DATABASE_PATH", app["database_path"]
    )
    return Settings(
        app_name=app["name"],
        database_path=_resolve_path(database_value),
        default_max_radius_miles=radius,
        allowed_max_radius_miles=allowed_radius,
        center_name=geography["center_name"],
        center_latitude=float(geography["center_latitude"]),
        center_longitude=float(geography["center_longitude"]),
        estimated_driving_distance_factor=float(
            geography["estimated_driving_distance_factor"]
        ),
        tier_a_max_miles=float(geography["tier_a_max_miles"]),
        tier_b_max_miles=float(geography["tier_b_max_miles"]),
        tier_c_max_miles=float(geography["tier_c_max_miles"]),
        tier_d_max_miles=float(geography["tier_d_max_miles"]),
        priority_cities=tuple(geography["priority_cities"]),
        scoring_version=scoring["version"],
        minimum_scoring_source_strength=int(
            scoring["minimum_scoring_source_strength"]
        ),
        massachusetts_rule_7_3_url=sources["massachusetts_rule_7_3_url"],
        olms_disclosure_url=sources["olms_disclosure_url"],
        osha_ita_page_url=sources["osha_ita_page_url"],
        osha_summary_url=sources["osha_summary_url"],
        census_acs_year=int(sources["census_acs_year"]),
        census_acs_base_url=sources["census_acs_base_url"],
        census_geocoder_url=sources["census_geocoder_url"],
        usaspending_api_url=sources["usaspending_api_url"],
        usaspending_lookback_years=int(sources["usaspending_lookback_years"]),
        census_api_key=os.getenv("CENSUS_API_KEY") or None,
    )
