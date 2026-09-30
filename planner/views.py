"""The API endpoint: GET /api/route/?start=City, ST&finish=City, ST[&format=map]."""
import re
from urllib.parse import urlencode

from django import forms
from django.core.cache import cache
from django.db.models import Case, Value, When
from django.db.models.functions import Length
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .corridor import resample_route, stations_near_route
from .geocode import CityNotFound, city_key, find_city
from .models import City
from .optimizer import UnreachableError, plan_fuel_stops
from .routing import NoRouteError, RoutingError, get_route

CACHE_SECONDS = 60 * 60  # keep a finished trip for one hour
CITY_STATE = re.compile(r"^\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2})\s*$")


def parse_city_state(value: str) -> tuple[str, str]:
    """Split "Chicago, IL" into ("Chicago", "IL"). Raises a form error if it doesn't look like that."""
    match = CITY_STATE.match(value)
    if not match:
        raise forms.ValidationError(f'"{value}" should look like "City, ST", e.g. "Chicago, IL".')
    return match["city"], match["state"].upper()


class RouteForm(forms.Form):
    """Checks the query string: start and finish must both look like "City, ST"."""

    start = forms.CharField()
    finish = forms.CharField()

    def clean_start(self):
        """Parse the starting city and state."""
        return parse_city_state(self.cleaned_data["start"])

    def clean_finish(self):
        """Parse the finishing city and state."""
        return parse_city_state(self.cleaned_data["finish"])


def home(request):
    """Open the city form on an empty US map."""
    return render(request, "planner/map.html", {"trip": None})


@csrf_exempt
def cities(request):
    """Suggest up to ten cities from the local city table."""
    if request.method != "GET":
        response = error_response(405, "Only GET is supported.")
        response["Allow"] = "GET"
        return response
    name, _, state = request.GET.get("q", "").partition(",")
    prefix = city_key(name)
    if len(prefix) < 2:
        return JsonResponse({"cities": []})
    matches = City.objects.filter(key__startswith=prefix, state__istartswith=state.strip()).order_by(
        Case(When(key=prefix, then=Value(0)), default=Value(1)), Length("key"), "name", "state",
    )[:10]
    return JsonResponse({"cities": [f"{city.name}, {city.state}" for city in matches]})


class ApiError(Exception):
    """A trip error with the HTTP status used by JSON and map pages."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# CSRF is unnecessary here because this view only accepts GET and never changes state.
@csrf_exempt
def route(request):
    """Return the route, the cheapest fuel stops and the total fuel cost (JSON, or a map page)."""
    if request.method != "GET":
        response = error_response(405, "Only GET is supported.")
        response["Allow"] = "GET"
        return response
    trip, error, status = None, None, 200
    try:
        trip = requested_trip(request.GET)
    except ApiError as exc:
        error, status = str(exc), exc.status

    if trip is not None:
        trip["map_url"] = request.build_absolute_uri(
            reverse("route") + "?" + urlencode({"start": trip["start"]["query"], "finish": trip["finish"]["query"], "format": "map"})
        )
    if request.GET.get("format") == "map":
        return render(request, "planner/map.html", {
            "trip": trip, "error": error,
            "start": trip["start"]["query"] if trip else request.GET.get("start", ""),
            "finish": trip["finish"]["query"] if trip else request.GET.get("finish", ""),
        }, status=status)
    return error_response(status, error) if error is not None else JsonResponse(trip)


def requested_trip(params) -> dict:
    """Validate cities and return a cached or newly planned trip."""
    form = RouteForm(params)
    if not form.is_valid():
        field, errors = next(iter(form.errors.items()))
        raise ApiError(400, f"{field}: {errors[0]}")
    start, finish = form.cleaned_data["start"], form.cleaned_data["finish"]
    try:
        start_city, finish_city = find_city(*start), find_city(*finish)
    except CityNotFound as exc:
        raise ApiError(404, str(exc)) from exc

    # City aliases share coordinates, so they share one cache entry.
    cache_key = f"trip:{start_city.lat},{start_city.lon}:{finish_city.lat},{finish_city.lon}"
    trip = cache.get(cache_key)
    if trip is not None:
        trip = {**trip, "start": place_json(start_city), "finish": place_json(finish_city),
                "cached": True, "routing_api_calls": 0}
    else:
        try:
            trip = plan_trip(start_city, finish_city)
        except (NoRouteError, UnreachableError) as exc:
            raise ApiError(422, str(exc)) from exc
        except RoutingError as exc:
            raise ApiError(502, str(exc)) from exc
        cache.set(cache_key, trip, CACHE_SECONDS)

    return trip


def plan_trip(start_city: City, finish_city: City) -> dict:
    """Make at most one routing call, match stations, and pick fuel stops."""
    if (start_city.lat, start_city.lon) == (finish_city.lat, finish_city.lon):
        # Same city: a zero-length route, so no routing call is needed.
        coordinates, routing_api_calls = [[start_city.lon, start_city.lat]] * 2, 0
    else:
        coordinates = get_route((start_city.lat, start_city.lon), (finish_city.lat, finish_city.lon))
        routing_api_calls = 1
    line = resample_route(coordinates)
    plan = plan_fuel_stops(stations_near_route(line), line.total_miles)
    return {
        "start": place_json(start_city),
        "finish": place_json(finish_city),
        "total_distance_miles": round(line.total_miles, 1),
        "total_gallons": round(plan.total_gallons, 2),
        "total_fuel_cost": round(plan.total_cost, 2),
        "fuel_stops": [stop_json(stop) for stop in plan.stops],
        "route": line.as_geojson(),
        "routing_api_calls": routing_api_calls,
        "cached": False,
    }


def place_json(city) -> dict:
    """A start/finish city as JSON. The name comes from our city table, not raw user input."""
    return {"query": f"{city.name}, {city.state}", "lat": city.lat, "lon": city.lon}


def stop_json(stop) -> dict:
    """One fuel stop as JSON."""
    station = stop.candidate.station
    return {
        "name": station.name,
        "address": station.address,
        "city": station.city,
        "state": station.state,
        "lat": station.lat,
        "lon": station.lon,
        "mile_marker": round(stop.candidate.mile, 1),
        "price_per_gallon": round(station.price, 3),
        "gallons": round(stop.gallons, 2),
        "cost": round(stop.cost, 2),
    }


def error_response(status: int, message: str) -> JsonResponse:
    """Every error uses the same shape: {"error": "..."}."""
    return JsonResponse({"error": message}, status=status)
