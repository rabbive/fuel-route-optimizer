"""Check the OpenRouteService client's requests and error handling."""
import json
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from planner.routing import NoRouteError, RoutingError, get_route


def fake_response(status, body):
    """A real requests.Response with the given status code and JSON body."""
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


GEOJSON = {"features": [{"geometry": {"coordinates": [[-87.6, 41.9], [-96.8, 32.8]]}}]}


@override_settings(ORS_API_KEY="test-key", ORS_BASE_URL="https://ors.example")
class GetRouteTests(SimpleTestCase):
    """Check route construction and common ORS failures."""

    @patch("planner.routing.requests.post", return_value=fake_response(200, GEOJSON))
    def test_returns_coordinates_and_sends_lon_lat(self, post):
        """Return GeoJSON coordinates and send coordinates in ORS order."""
        coords = get_route((41.9, -87.6), (32.8, -96.8))

        self.assertEqual(coords, [[-87.6, 41.9], [-96.8, 32.8]])
        url = post.call_args.args[0]
        self.assertEqual(url, "https://ors.example/v2/directions/driving-car/geojson")
        self.assertEqual(post.call_args.kwargs["json"], {"coordinates": [[-87.6, 41.9], [-96.8, 32.8]]})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "test-key")
        self.assertEqual(post.call_args.kwargs["timeout"], 10)

    @patch(
        "planner.routing.requests.post",
        return_value=fake_response(404, {"error": {"code": 2010, "message": "Could not find routable point"}}),
    )
    def test_no_route(self, post):
        """Report unreachable points as a no-route error."""
        with self.assertRaisesRegex(NoRouteError, "Could not find routable point"):
            get_route((21.3, -157.8), (41.9, -87.6))

    @patch(
        "planner.routing.requests.post",
        return_value=fake_response(403, {"error": "Access to this API has been disallowed"}),
    )
    def test_bad_key_is_routing_error(self, post):
        """Report authorization failures as routing errors."""
        with self.assertRaisesRegex(RoutingError, "403.*disallowed"):
            get_route((41.9, -87.6), (32.8, -96.8))

    @patch("planner.routing.requests.post", side_effect=requests.Timeout("slow"))
    def test_timeout_is_routing_error(self, post):
        """Translate network timeouts to routing errors."""
        with self.assertRaises(RoutingError):
            get_route((41.9, -87.6), (32.8, -96.8))

    @patch("planner.routing.requests.post", return_value=fake_response(200, {"unexpected": True}))
    def test_malformed_success_response_is_routing_error(self, post):
        """Report an unexpected successful response as a routing error."""
        with self.assertRaisesRegex(RoutingError, "Unexpected response from the routing service"):
            get_route((41.9, -87.6), (32.8, -96.8))
