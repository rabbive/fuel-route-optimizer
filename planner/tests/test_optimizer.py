"""Check the public fuel-stop optimizer behavior."""

from django.test import SimpleTestCase

from planner.optimizer import Candidate, UnreachableError, plan_fuel_stops


def summary(plan):
    """(mile, gallons) for each stop, rounded, so the tests are easy to read."""
    return [(s.candidate.mile, round(s.gallons, 2)) for s in plan.stops]


class PlanFuelStopsTests(SimpleTestCase):
    """Verify route planning, costs, and unreachable routes."""

    def test_short_trip_buys_at_first_station_only(self):
        """Check that a short trip buys only the fuel it needs."""
        plan = plan_fuel_stops([Candidate(0, 3.0)], total_miles=200)
        self.assertEqual(summary(plan), [(0, 20.0)])
        self.assertAlmostEqual(plan.total_cost, 60.0)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        """Check that fuel is bought at the cheaper station when reachable."""
        plan = plan_fuel_stops([Candidate(0, 4.0), Candidate(100, 3.0)], total_miles=300)
        # 100 miles at $4, then the remaining 200 miles at the cheaper $3.
        self.assertEqual(summary(plan), [(0, 10.0), (100, 20.0)])
        self.assertAlmostEqual(plan.total_cost, 10 * 4.0 + 20 * 3.0)

    def test_fills_up_when_nothing_ahead_is_cheaper(self):
        """Check that the truck fills up when no cheaper station is reachable."""
        stations = [Candidate(0, 3.0), Candidate(300, 4.0), Candidate(450, 3.5)]
        plan = plan_fuel_stops(stations, total_miles=800)
        # Mile 0 is cheapest and the finish is out of range: fill up (500 miles).
        # Drive to the cheapest station in range (mile 450) with 50 miles left, then buy 300 to finish.
        self.assertEqual(summary(plan), [(0, 50.0), (450, 30.0)])
        self.assertAlmostEqual(plan.total_cost, 50 * 3.0 + 30 * 3.5)

    def test_drive_before_first_station_is_paid_at_its_price(self):
        """Check that fuel for the initial stretch is bought at the first station."""
        plan = plan_fuel_stops([Candidate(40, 3.0)], total_miles=240)
        self.assertEqual(summary(plan), [(40, 24.0)])
        self.assertAlmostEqual(plan.total_cost, 72.0)

    def test_gallons_always_equal_miles_divided_by_mpg(self):
        """Check that total gallons match distance divided by efficiency."""
        prices = [3.9, 3.1, 3.6, 2.9, 3.3, 3.8, 3.0, 3.4, 3.2, 3.7, 3.5, 3.05, 3.15]
        stations = [Candidate(7 + 120 * k, p) for k, p in enumerate(prices)]
        plan = plan_fuel_stops(stations, total_miles=1500)
        self.assertAlmostEqual(plan.total_gallons, 150.0)

    def test_station_exactly_at_range_is_reachable(self):
        """Check that a station at the maximum range can be reached."""
        plan = plan_fuel_stops([Candidate(0, 3.0), Candidate(500, 3.0)], total_miles=900)
        self.assertEqual(summary(plan), [(0, 50.0), (500, 40.0)])

    def test_gap_longer_than_range_raises(self):
        """Check that an overlong station gap is rejected."""
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([Candidate(0, 3.0), Candidate(600, 3.0)], total_miles=900)

    def test_no_station_near_start_raises(self):
        """Check that a route without a reachable first station is rejected."""
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([Candidate(501, 3.0)], total_miles=900)

    def test_no_stations_at_all_raises(self):
        """Check that a route with no stations is rejected."""
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([], total_miles=100)

    def test_zero_mile_trip_costs_nothing(self):
        """Check that a zero-mile trip requires no fuel."""
        plan = plan_fuel_stops([], total_miles=0)
        self.assertEqual((plan.stops, plan.total_gallons, plan.total_cost), ([], 0.0, 0.0))

    def test_candidates_in_any_order(self):
        """Check that candidates may arrive in any order."""
        plan = plan_fuel_stops([Candidate(100, 3.0), Candidate(0, 4.0)], total_miles=300)
        self.assertEqual(summary(plan), [(0, 10.0), (100, 20.0)])

    def test_station_object_is_handed_back(self):
        """Check that each stop retains its station object."""
        plan = plan_fuel_stops([Candidate(0, 3.0, station="PILOT #1")], total_miles=50)
        self.assertEqual(plan.stops[0].candidate.station, "PILOT #1")
