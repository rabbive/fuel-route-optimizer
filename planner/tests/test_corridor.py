import math

from django.test import SimpleTestCase, TestCase

from planner.corridor import nearest_route_point, resample_route, stations_near_route
from planner.models import FuelStation

MILES_PER_DEG_LAT = 69.09  # with Earth radius 3958.8 miles
# A straight east-west route along latitude 40, from longitude -100 to -99 (about 53 miles).
ROUTE = [[-100.0, 40.0], [-99.0, 40.0]]
ROUTE_MILES = MILES_PER_DEG_LAT * math.cos(math.radians(40))  # ~52.9


def north_of(lat, miles):
    """Return a latitude that lies the requested miles north of lat."""
    return lat + miles / MILES_PER_DEG_LAT


class ResampleRouteTests(SimpleTestCase):
    """Check route resampling and GeoJSON output."""

    def test_points_one_mile_apart_ending_at_finish(self):
        """Resampling uses one-mile intervals and includes the finish."""
        line = resample_route(ROUTE)
        self.assertAlmostEqual(line.total_miles, ROUTE_MILES, delta=0.1)
        self.assertEqual(line.miles[0], 0.0)
        self.assertEqual(line.miles[1], 1.0)
        self.assertAlmostEqual(line.miles[-1], line.total_miles)
        self.assertAlmostEqual(line.lons[-1], -99.0)
        self.assertEqual(len(line.miles), math.ceil(line.total_miles) + 1)

    def test_resample_single_point_route(self):
        """A zero-length route keeps one coordinate."""
        line = resample_route([[-100.0, 40.0], [-100.0, 40.0]])
        self.assertEqual(line.total_miles, 0.0)
        self.assertEqual(line.as_geojson()["coordinates"], [[-100.0, 40.0]])

    def test_geojson_is_lon_lat(self):
        """GeoJSON coordinates are ordered longitude then latitude."""
        coords = resample_route(ROUTE).as_geojson()["coordinates"]
        self.assertEqual(coords[0], [-100.0, 40.0])


class NearestRoutePointTests(SimpleTestCase):
    """Check distance and mile-marker lookup against a route."""

    def test_distance_and_mile_marker(self):
        """Nearest-point lookup returns distance and route progress."""
        line = resample_route(ROUTE)
        distances, miles = nearest_route_point(line, [north_of(40, 3), north_of(40, 30)], [-99.5, -99.5])
        self.assertAlmostEqual(distances[0], 3.0, delta=0.1)
        self.assertAlmostEqual(distances[1], 30.0, delta=0.3)
        self.assertAlmostEqual(miles[0], ROUTE_MILES / 2, delta=1.0)


class StationsNearRouteTests(TestCase):
    """Check that only stations within the route corridor are returned."""

    def add_station(self, opis_id, lat, lon, price=3.0):
        """Create a station for corridor tests."""
        return FuelStation.objects.create(
            opis_id=opis_id, name=f"S{opis_id}", address="", city="X", state="KS", price=price, lat=lat, lon=lon
        )

    def test_keeps_only_stations_inside_corridor(self):
        """The exact distance check excludes nearby bounding-box false positives."""
        near = self.add_station(1, north_of(40, 3), -99.5, price=3.25)
        # Inside the bounding box, but about 12.7 miles from the start corner of the route.
        self.add_station(2, north_of(40, 9), -100 - 9 / (MILES_PER_DEG_LAT * math.cos(math.radians(40))))
        self.add_station(3, north_of(40, 30), -99.5)  # far away

        found = stations_near_route(resample_route(ROUTE))

        self.assertEqual([c.station for c in found], [near])
        self.assertEqual(found[0].price, 3.25)
        self.assertAlmostEqual(found[0].mile, ROUTE_MILES / 2, delta=1.0)
