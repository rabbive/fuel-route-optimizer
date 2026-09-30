"""Check route API responses, caching, errors, and the map page."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase

from planner.models import City, FuelStation
from planner.routing import NoRouteError, RoutingError

CHICAGO = (41.88, -87.63)
DALLAS = (32.78, -96.80)
# A fake straight route Chicago -> Dallas ([lon, lat] like ORS returns). About 800 miles.
ROUTE = [[CHICAGO[1], CHICAGO[0]], [DALLAS[1], DALLAS[0]]]
URL = "/api/route/"


@patch("planner.views.get_route", return_value=ROUTE)
class RouteViewTests(TestCase):
    """Check route responses and errors."""
    def setUp(self):
        """Create cities and fuel stations for each test."""
        cache.clear()
        City.objects.create(name="Chicago", key="chicago", state="IL", lat=CHICAGO[0], lon=CHICAGO[1])
        City.objects.create(name="Dallas", key="dallas", state="TX", lat=DALLAS[0], lon=DALLAS[1])
        # One pricey station at the start and one cheap station halfway, both on the fake route.
        mid = ((CHICAGO[0] + DALLAS[0]) / 2, (CHICAGO[1] + DALLAS[1]) / 2)
        FuelStation.objects.create(opis_id=1, name="START STOP", address="I-55", city="Chicago", state="IL",
                                   price=3.50, lat=CHICAGO[0], lon=CHICAGO[1])
        FuelStation.objects.create(opis_id=2, name="MID STOP", address="I-44", city="Mid", state="MO",
                                   price=3.00, lat=mid[0], lon=mid[1])

    def get(self, **params):
        """Request a Chicago to Dallas trip with optional query values."""
        return self.client.get(URL, {"start": "Chicago, IL", "finish": "Dallas, TX", **params})

    def test_returns_trip_json(self, get_route):
        """Returns trip json."""
        data = self.get().json()

        self.assertEqual(data["start"], {"query": "Chicago, IL", "lat": CHICAGO[0], "lon": CHICAGO[1]})
        self.assertEqual([s["name"] for s in data["fuel_stops"]], ["START STOP", "MID STOP"])
        self.assertAlmostEqual(data["total_gallons"], data["total_distance_miles"] / 10, delta=0.01)
        self.assertAlmostEqual(data["total_fuel_cost"], sum(s["cost"] for s in data["fuel_stops"]), delta=0.02)
        self.assertEqual(data["route"]["type"], "LineString")
        self.assertEqual(data["route"]["coordinates"][0], [CHICAGO[1], CHICAGO[0]])
        self.assertIn("format=map", data["map_url"])
        self.assertEqual((data["routing_api_calls"], data["cached"]), (1, False))
        stop = data["fuel_stops"][0]
        self.assertEqual(
            set(stop),
            {"name", "address", "city", "state", "lat", "lon", "mile_marker", "price_per_gallon", "gallons", "cost"},
        )

    def test_second_request_is_cached(self, get_route):
        """Second request is cached."""
        first = self.get().json()
        second = self.get().json()

        self.assertEqual(get_route.call_count, 1)
        self.assertEqual((second["routing_api_calls"], second["cached"]), (0, True))
        self.assertEqual(second["total_fuel_cost"], first["total_fuel_cost"])

    def test_casual_input_formatting_works_and_shares_cache(self, get_route):
        """Casual input formatting works and shares cache."""
        self.get()
        response = self.client.get(URL, {"start": "  chicago ,  il ", "finish": "DALLAS, tx"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cached"])
        self.assertEqual(get_route.call_count, 1)

    def test_alias_coordinates_share_cache(self, get_route):
        """Different city rows at the same coordinates share a trip cache entry."""
        City.objects.create(name="Chi Town", key="chitown", state="IL", lat=CHICAGO[0], lon=CHICAGO[1])
        self.get()
        second = self.client.get(URL, {"start": "Chi Town, IL", "finish": "Dallas, TX"})

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["cached"])
        self.assertEqual(second.json()["start"]["query"], "Chi Town, IL")
        self.assertEqual(get_route.call_count, 1)

    def test_reversed_trip_is_not_served_from_cache(self, get_route):
        """Reversed trip is not served from cache."""
        self.get()
        get_route.return_value = list(reversed(ROUTE))
        response = self.client.get(URL, {"start": "Dallas, TX", "finish": "Chicago, IL"})

        self.assertFalse(response.json()["cached"])
        self.assertEqual(get_route.call_count, 2)

    def test_same_start_and_finish(self, get_route):
        """Same start and finish."""
        get_route.return_value = [ROUTE[0], ROUTE[0]]
        data = self.client.get(URL, {"start": "Chicago, IL", "finish": "Chicago, IL"}).json()

        self.assertEqual((data["total_distance_miles"], data["total_fuel_cost"], data["fuel_stops"]), (0, 0, []))

    def test_same_city_makes_no_routing_call(self, get_route):
        """Same city needs no routing call, even if routing is down."""
        get_route.side_effect = RoutingError("down")
        response = self.client.get(URL, {"start": "Chicago, IL", "finish": "Chicago, IL"})
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual((data["total_fuel_cost"], data["fuel_stops"], data["routing_api_calls"]), (0, [], 0))
        get_route.assert_not_called()

    def test_alias_coordinates_count_as_same_city(self, get_route):
        """Different city rows at the same coordinates need no routing call."""
        City.objects.create(name="Chi Town", key="chitown", state="IL", lat=CHICAGO[0], lon=CHICAGO[1])
        response = self.client.get(URL, {"start": "Chicago, IL", "finish": "Chi Town, IL"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["routing_api_calls"], 0)
        get_route.assert_not_called()

    def test_map_format_returns_html(self, get_route):
        """Map format returns html."""
        response = self.get(format="map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "leaflet")
        self.assertContains(response, "trip-data")
        self.assertContains(response, "function esc(")
        self.assertContains(response, "strict-origin-when-cross-origin")
        self.assertContains(response, "getSize().x")

    def test_home_page_shows_empty_form(self, get_route):
        """Open an empty map and city form without calling routing."""
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        for text in ('id="route-form"', 'name="start"', 'name="finish"', 'city-options'):
            self.assertContains(response, text)
        self.assertContains(response, 'id="trip-data" type="application/json">null</script>')
        self.assertContains(response, 'value=""', count=2)
        get_route.assert_not_called()

    def test_map_page_prefills_form(self, get_route):
        """Keep the selected trip cities in the form."""
        response = self.get(format="map")

        self.assertContains(response, 'value="Chicago, IL"')
        self.assertContains(response, 'value="Dallas, TX"')

    def test_post_is_405_json(self, get_route):
        """Reject POST with JSON even when CSRF checks are enabled."""
        response = Client(enforce_csrf_checks=True).post(URL)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        self.assertEqual(response.json(), {"error": "Only GET is supported."})
        get_route.assert_not_called()

    def test_map_unknown_city_renders_page_with_error(self, get_route):
        """Show an unknown city error and keep both typed cities."""
        response = self.get(start="Atlantis, IL", format="map")

        self.assertIn("text/html", response["Content-Type"])
        for text in ("City not found", 'value="Atlantis, IL"', 'value="Dallas, TX"'):
            self.assertContains(response, text, status_code=404)
        get_route.assert_not_called()

    def test_map_bad_input_renders_page_with_error(self, get_route):
        """Explain the city format on the map page."""
        response = self.client.get(URL, {"start": "Chicago", "format": "map"})

        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "City, ST", status_code=400)
        self.assertContains(response, 'value="Chicago"', status_code=400)
        get_route.assert_not_called()

    def test_map_routing_failure_renders_page_with_error(self, get_route):
        """Keep the form visible when the routing service fails."""
        get_route.side_effect = RoutingError("Could not reach the routing service.")
        response = self.get(format="map")

        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "Could not reach the routing service.", status_code=502)
        self.assertContains(response, 'value="Chicago, IL"', status_code=502)

    def test_map_error_escapes_user_text(self, get_route):
        """Escape markup in both the error message and retained input."""
        response = self.get(start='<b>x</b>, IL', format="map")

        self.assertIn("text/html", response["Content-Type"])
        self.assertNotContains(response, "<b>x</b>", status_code=404)
        self.assertContains(response, 'value="&lt;b&gt;x&lt;/b&gt;, IL"', status_code=404)
        get_route.assert_not_called()

    def test_map_unreachable_trip_renders_page_with_error(self, get_route):
        """Show both missing roads and missing fuel stations on the page."""
        get_route.side_effect = NoRouteError("No driving route found: island")
        response = self.get(format="map")
        self.assertContains(response, "No driving route found", status_code=422)
        self.assertIn("text/html", response["Content-Type"])

        get_route.side_effect = None
        FuelStation.objects.all().delete()
        response = self.get(format="map")
        self.assertContains(response, "No fuel station", status_code=422)
        self.assertIn("text/html", response["Content-Type"])

    def test_map_post_stays_405_json(self, get_route):
        """Map format never changes the response to a non-GET request."""
        response = Client(enforce_csrf_checks=True).post(URL + "?format=map")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        self.assertEqual(response.json(), {"error": "Only GET is supported."})
        get_route.assert_not_called()

    def test_city_suggestions(self, get_route):
        """Suggest local cities by name, length, and optional state prefix."""
        City.objects.create(name="Chico", key="chico", state="CA", lat=0, lon=0)
        City.objects.create(name="Chicagoland", key="chicagoland", state="IL", lat=0, lon=0)

        response = self.client.get("/api/cities/", {"q": "chi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cities": ["Chico, CA", "Chicago, IL", "Chicagoland, IL"]})
        self.assertEqual(
            self.client.get("/api/cities/", {"q": "chicago, i"}).json(),
            {"cities": ["Chicago, IL", "Chicagoland, IL"]},
        )
        for q in ("c", "", " . c "):
            self.assertEqual(self.client.get("/api/cities/", {"q": q}).json(), {"cities": []})
        get_route.assert_not_called()

    def test_city_suggestions_normalization_order_and_limit(self, get_route):
        """Normalize city names, rank exact matches first, and stop at ten."""
        City.objects.create(name="New York", key="newyork", state="NY", lat=0, lon=0)
        response = self.client.get("/api/cities/", {"q": "new yo, n"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"cities": ["New York, NY"]},
        )
        City.objects.create(name="Chi", key="chi", state="IL", lat=0, lon=0)
        for i in range(12):
            City.objects.create(name=f"Chi{i:02}", key=f"chi{i:02}", state="IL", lat=0, lon=0)
        self.assertEqual(
            self.client.get("/api/cities/", {"q": "CHI, I"}).json(),
            {"cities": ["Chi, IL"] + [f"Chi{i:02}, IL" for i in range(9)]},
        )
        get_route.assert_not_called()

    def test_city_suggestions_post_is_405_json(self, get_route):
        """Only GET can request city suggestions."""
        response = Client(enforce_csrf_checks=True).post("/api/cities/?q=chi")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        self.assertEqual(response.json(), {"error": "Only GET is supported."})
        get_route.assert_not_called()

    def test_missing_finish_is_400(self, get_route):
        """Missing finish is 400."""
        response = self.client.get(URL, {"start": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json()["error"])

    def test_bad_format_is_400(self, get_route):
        """Bad format is 400."""
        response = self.client.get(URL, {"start": "Chicago", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("City, ST", response.json()["error"])

    def test_unknown_city_is_404(self, get_route):
        """Unknown city is 404."""
        response = self.client.get(URL, {"start": "Atlantis, IL", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 404)
        get_route.assert_not_called()

    def test_no_route_is_422(self, get_route):
        """No route is 422."""
        get_route.side_effect = NoRouteError("No driving route found: island")
        self.assertEqual(self.get().status_code, 422)

    def test_no_stations_is_422(self, get_route):
        """No stations is 422."""
        FuelStation.objects.all().delete()
        response = self.get()
        self.assertEqual(response.status_code, 422)
        self.assertIn("No fuel station", response.json()["error"])

    def test_routing_failure_is_502(self, get_route):
        """Routing failure is 502."""
        get_route.side_effect = RoutingError("Could not reach the routing service.")
        self.assertEqual(self.get().status_code, 502)
