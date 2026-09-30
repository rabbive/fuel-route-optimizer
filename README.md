# Fuel Route Optimizer

A Django API that plans a road trip between two US cities. It returns the route on a map, the cheapest places to buy fuel along it, and the total fuel cost.

- Vehicle range: **500 miles** per tank. Fuel economy: **10 MPG**.
- Fuel prices come from `planner/data/fuel-prices.csv`.
- Routing uses [OpenRouteService](https://openrouteservice.org). Each trip makes **one** routing call, and a repeated trip makes none because it is cached.

## Setup

Requires Python 3.12 or newer (Django 6.1).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env               # then paste your free ORS key into .env
.venv/bin/python manage.py migrate
.venv/bin/python manage.py load_data    # one time: loads ~32k US cities and ~6.3k fuel stations
.venv/bin/python manage.py runserver
```

Run the tests: `.venv/bin/python manage.py test planner`

## API

`GET /api/route/?start=Chicago, IL&finish=Dallas, TX`

| Param | Meaning |
|---|---|
| `start`, `finish` | `"City, ST"` for a US city (case and spacing don't matter; `St.`/`Saint` both work) |
| `format=map` | optional: return an HTML map instead of JSON |

The response contains:
- `total_distance_miles`, `total_gallons`, `total_fuel_cost`
- `fuel_stops`: for each stop, the name, address, city/state, lat/lon, `mile_marker`, `price_per_gallon`, `gallons` and `cost`
- `route`: a GeoJSON LineString
- `map_url`
- `routing_api_calls` and `cached`

Errors are `{"error": "..."}`:

| Code | When |
|---|---|
| 400 | Bad input |
| 404 | City not found |
| 422 | No route, or stations more than 500 miles apart |
| 502 | Routing service down |

Import `postman_collection.json` into Postman to try it.

## How it works

1. **Find the cities locally.** `planner/geocode.py` looks up "City, ST" in a US Census city table, so there's no geocoding API call.
2. **One routing call.** `planner/routing.py` asks OpenRouteService for the driving route line.
3. **Stations on the route.** `planner/corridor.py`:
   - resamples the line to one point per mile;
   - finds each station's closest route point with one numpy matrix multiply;
   - keeps stations within 10 miles of the route.
4. **Cheapest fuel plan.** `planner/optimizer.py` runs the classic greedy method. At each station:
   - if a cheaper station is within 500 miles, buy just enough to reach it;
   - otherwise, if the finish is within range, buy just enough to finish;
   - otherwise, fill up and go to the cheapest station in range.
5. **Cache.** The result is cached for an hour, keyed by (start, finish).

## Assumptions

- The tank starts empty, so every gallon is paid for (total gallons = miles ÷ 10). The drive from the start city to the first station is paid at that station's price.
- Stations have no coordinates in the CSV, so each one is placed at its city's center point (from the 2025 Census Gazetteer).
  - 95% of US station rows match a city.
  - Canadian stations and unmatched ones are skipped.
  - Duplicate station IDs keep the cheapest price.
- Stops have no cost of their own, so the cheapest plan sometimes buys a few gallons to reach a slightly cheaper station nearby. A per-stop cost would merge those stops.
- When a city name repeats within a state, incorporated places win over CDPs.
- The detour from the route to a station isn't counted in distance or cost.

## Speed (measured on a laptop)

| Trip | First request | Cached request | Our compute (excl. ORS) |
|---|---|---|---|
| Chicago → Dallas | not measured yet | not measured yet | — |
| New York → Los Angeles | not measured yet | not measured yet | not measured yet |

Run the two Postman trips with your ORS key to fill this in.

## Limits / next steps

- The cache lives in memory in one process. Use Redis if the app runs on several servers.
- Input is limited to "City, ST". Supporting street addresses would need a geocoding API (more calls).
- Fuel prices are a static snapshot.
