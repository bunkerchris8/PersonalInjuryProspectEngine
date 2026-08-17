from __future__ import annotations

import math
from dataclasses import dataclass

from src.config import Settings


EARTH_RADIUS_MILES = 3958.7613


@dataclass(frozen=True)
class DistanceResult:
    straight_line_miles: float
    estimated_driving_miles: float
    geographic_tier: str
    within_configured_radius: bool
    method: str = "haversine_times_configured_road_factor"


def haversine_miles(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(value))


def _tier(estimated_driving_miles: float, settings: Settings) -> str:
    if estimated_driving_miles <= settings.tier_a_max_miles:
        return "A"
    if estimated_driving_miles <= settings.tier_b_max_miles:
        return "B"
    if estimated_driving_miles <= settings.tier_c_max_miles:
        return "C"
    if estimated_driving_miles <= settings.tier_d_max_miles:
        return "D"
    return "OUTSIDE"


def calculate_distance(
    latitude: float, longitude: float, settings: Settings
) -> DistanceResult:
    straight = haversine_miles(
        settings.center_latitude,
        settings.center_longitude,
        latitude,
        longitude,
    )
    driving = straight * settings.estimated_driving_distance_factor
    return DistanceResult(
        straight_line_miles=round(straight, 2),
        estimated_driving_miles=round(driving, 2),
        geographic_tier=_tier(driving, settings),
        within_configured_radius=driving <= settings.default_max_radius_miles,
    )

