"""Decide where to buy fuel along a route so the whole trip costs as little as possible.

This is the classic "gas station problem" greedy method. At each station:
  1. If a cheaper station is within range, buy just enough fuel to reach the nearest one.
  2. Otherwise, if the finish is within range, buy just enough to reach the finish.
  3. Otherwise, fill the tank and drive to the cheapest station within range.
The truck starts with an empty tank, so every gallon used on the trip is paid for.
"""
from dataclasses import dataclass
from typing import Any, Iterable

MAX_RANGE_MILES = 500  # how far the truck can drive on a full tank
MPG = 10  # miles per gallon


class UnreachableError(Exception):
    """The trip can't be done: somewhere the stations are too far apart for the tank."""


@dataclass(frozen=True)
class Candidate:
    """A fuel station somewhere along the route."""

    mile: float  # how far along the route it is, from the start
    price: float  # dollars per gallon
    station: Any = None  # the caller's own station object, handed back in the result


@dataclass(frozen=True)
class FuelStop:
    """A station where the truck buys fuel, and how much it buys."""

    candidate: Candidate
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    """The chosen fuel stops plus trip totals."""

    stops: list[FuelStop]
    total_gallons: float
    total_cost: float


def plan_fuel_stops(
    candidates: Iterable[Candidate],
    total_miles: float,
    max_range: float = MAX_RANGE_MILES,
    mpg: float = MPG,
) -> FuelPlan:
    """Return the cheapest set of fuel stops for a trip of `total_miles`.

    `candidates` are the stations along the route, in any order.
    Raises UnreachableError if some stretch longer than `max_range` has no station.
    """
    if total_miles <= 0:
        return FuelPlan(stops=[], total_gallons=0.0, total_cost=0.0)

    stations = sorted((c for c in candidates if c.mile <= total_miles), key=lambda c: c.mile)
    if not stations or stations[0].mile > max_range:
        raise UnreachableError(f"No fuel station within {max_range} miles of the start.")

    # How many miles' worth of fuel we buy at each station. The tank starts empty, so the
    # short drive from the start city to the first station is paid at that station's price.
    bought = [0.0] * len(stations)
    bought[0] = stations[0].mile

    i = 0  # index of the station we are at now
    fuel = 0.0  # miles of driving left in the tank when we arrive here
    while True:
        here = stations[i]
        in_range = [j for j in range(i + 1, len(stations)) if stations[j].mile - here.mile <= max_range]
        cheaper = next((j for j in in_range if stations[j].price < here.price), None)
        to_finish = total_miles - here.mile

        if cheaper is not None:
            # Rule 1: a cheaper station is in range, so buy only enough to get there.
            next_stop = cheaper
            needed = stations[next_stop].mile - here.mile
            buy = max(0.0, needed - fuel)
        elif to_finish <= max_range:
            # Rule 2: the finish is in range, so buy only enough to finish. Done.
            bought[i] += max(0.0, to_finish - fuel)
            break
        elif in_range:
            # Rule 3: nothing cheaper ahead, so fill up and go to the cheapest station in range.
            next_stop = min(in_range, key=lambda j: stations[j].price)
            needed = stations[next_stop].mile - here.mile
            buy = max_range - fuel
        else:
            raise UnreachableError(
                f"No fuel station within {max_range} miles after mile {here.mile:.0f} of the route."
            )

        bought[i] += buy
        fuel = fuel + buy - needed
        i = next_stop

    stops = [
        FuelStop(candidate=station, gallons=miles / mpg, cost=miles / mpg * station.price)
        for station, miles in zip(stations, bought)
        if miles > 0
    ]
    return FuelPlan(
        stops=stops,
        total_gallons=sum(s.gallons for s in stops),
        total_cost=sum(s.cost for s in stops),
    )
