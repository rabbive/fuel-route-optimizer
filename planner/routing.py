"""Get a driving route from OpenRouteService (ORS). This is the only external API we call."""
import math

import requests
from django.conf import settings

TIMEOUT_SECONDS = 10


class RoutingError(Exception):
    """The routing service failed: network problem, bad API key, or a server error."""


class NoRouteError(Exception):
    """The routing service says there is no driving route between the two points."""


def get_route(start: tuple[float, float], finish: tuple[float, float]) -> list[list[float]]:
    """Ask ORS for the driving route between two (lat, lon) points.

    Returns the route line as a list of [lon, lat] points (the GeoJSON order).
    """
    url = f"{settings.ORS_BASE_URL}/v2/directions/driving-car/geojson"
    body = {"coordinates": [[start[1], start[0]], [finish[1], finish[0]]]}  # ORS wants [lon, lat]
    try:
        response = requests.post(
            url, json=body, headers={"Authorization": settings.ORS_API_KEY}, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        raise RoutingError("Could not reach the routing service.") from exc

    if response.status_code in (400, 404):
        # ORS answers 400/404 when a point can't be reached by road or no route exists.
        raise NoRouteError(f"No driving route found: {error_message(response)}")
    if not response.ok:
        raise RoutingError(f"Routing service error ({response.status_code}): {error_message(response)}")
    try:
        coordinates = response.json()["features"][0]["geometry"]["coordinates"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        # A successful response must contain route coordinates.
        raise RoutingError("Unexpected response from the routing service.") from exc
    if not _valid_coordinates(coordinates):
        raise RoutingError("Unexpected response from the routing service.")
    return coordinates


def _valid_coordinates(coords) -> bool:
    """True if coords is a list of at least 2 points, each with 2+ real numbers."""
    return (
        isinstance(coords, list)
        and len(coords) >= 2
        and all(
            isinstance(p, (list, tuple))
            and len(p) >= 2
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in p)
            for p in coords
        )
    )


def error_message(response) -> str:
    """Pull the readable message out of an ORS error response.

    ORS sends either {"error": "text"} or {"error": {"code": 2010, "message": "text"}}.
    """
    try:
        error = response.json().get("error", "")
    except (ValueError, AttributeError):
        return response.text[:200]
    return error.get("message", str(error)) if isinstance(error, dict) else str(error)
