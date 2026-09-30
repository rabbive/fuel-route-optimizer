"""Find which fuel stations sit close to the route, and how far along the route each one is."""
import math
from dataclasses import dataclass

import numpy as np

from .models import FuelStation
from .optimizer import Candidate

EARTH_RADIUS_MILES = 3958.8
CORRIDOR_MILES = 10  # a station counts as "on the route" if its city is this close to the route
STEP_MILES = 1.0  # spacing between route points after resampling
MILES_PER_DEG_LAT = 69.0  # roughly how many miles one degree of latitude is


@dataclass(frozen=True)
class RouteLine:
    """The route as evenly spaced points: point k is at mile k (the last point is the finish)."""

    lats: np.ndarray
    lons: np.ndarray
    miles: np.ndarray  # mile marker of each point
    total_miles: float

    def as_geojson(self) -> dict:
        """The route as a GeoJSON LineString ([lon, lat] pairs), rounded to keep the response small."""
        coords = [[round(lon, 5), round(lat, 5)] for lat, lon in zip(self.lats.tolist(), self.lons.tolist())]
        return {"type": "LineString", "coordinates": coords}


def haversine_miles(lat1, lon1, lat2, lon2):
    """Straight-line distance over the Earth's surface, in miles. Works on numbers or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def resample_route(coordinates) -> RouteLine:
    """Turn the routing API's [lon, lat] points into points spaced STEP_MILES apart."""
    points = np.asarray(coordinates, dtype=float)
    lons, lats = points[:, 0], points[:, 1]

    # How far along the route each original point is.
    segment_miles = haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    along = np.concatenate([[0.0], np.cumsum(segment_miles)])
    total = float(along[-1])

    # New mile markers every STEP_MILES plus the exact finish, then find the lat/lon at each one.
    miles = np.append(np.arange(0.0, total, STEP_MILES), total)
    return RouteLine(
        lats=np.interp(miles, along, lats),
        lons=np.interp(miles, along, lons),
        miles=miles,
        total_miles=total,
    )


def to_unit_vectors(lats, lons):
    """Turn lat/lon (degrees) into 3D points on a globe of radius 1, one row per point."""
    lat, lon = np.radians(lats), np.radians(lons)
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def nearest_route_point(line: RouteLine, lats, lons, chunk_size=1000):
    """For each place, find the closest route point.

    Returns two arrays: the distance to the route (miles) and the mile marker of the closest point.
    Trick: on a globe of radius 1, the closest route point is the one with the biggest dot
    product, and the angle between the two points times the Earth's radius is the distance.
    One matrix multiply checks every place against every route point at once.
    """
    route = to_unit_vectors(line.lats, line.lons)
    places = to_unit_vectors(np.asarray(lats, dtype=float), np.asarray(lons, dtype=float))
    distances = np.empty(len(places))
    nearest = np.empty(len(places), dtype=int)

    # Work in chunks so a coast-to-coast route doesn't build one giant matrix in memory.
    for start in range(0, len(places), chunk_size):
        dots = places[start : start + chunk_size] @ route.T
        best = dots.argmax(axis=1)
        best_dots = dots[np.arange(len(best)), best]
        nearest[start : start + chunk_size] = best
        distances[start : start + chunk_size] = EARTH_RADIUS_MILES * np.arccos(np.clip(best_dots, -1.0, 1.0))

    return distances, line.miles[nearest]


def stations_near_route(line: RouteLine) -> list[Candidate]:
    """Return every fuel station within CORRIDOR_MILES of the route, ready for the optimizer."""
    # Quick first cut in the database: only stations inside the route's bounding box plus a margin.
    lat_margin = CORRIDOR_MILES / MILES_PER_DEG_LAT
    lon_margin = CORRIDOR_MILES / (MILES_PER_DEG_LAT * math.cos(math.radians(float(np.abs(line.lats).max()))))
    stations = list(
        FuelStation.objects.filter(
            lat__range=(float(line.lats.min()) - lat_margin, float(line.lats.max()) + lat_margin),
            lon__range=(float(line.lons.min()) - lon_margin, float(line.lons.max()) + lon_margin),
        )
    )
    if not stations:
        return []

    # Exact check: distance from each station to the closest point on the route.
    distances, miles = nearest_route_point(line, [s.lat for s in stations], [s.lon for s in stations])
    return [
        Candidate(mile=float(mile), price=station.price, station=station)
        for station, distance, mile in zip(stations, distances, miles)
        if distance <= CORRIDOR_MILES
    ]
