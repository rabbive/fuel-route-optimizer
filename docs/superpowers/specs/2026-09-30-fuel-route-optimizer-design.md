# Fuel Route Optimizer — Design

Date: 2026-09-30
Status: approved in chat, pending written-spec review

## Goal

A Django API for a take-home assessment. Input: a start and a finish city in the USA. Output: the driving route on a map, the cheapest places to buy fuel along it (vehicle range 500 miles), and the total fuel cost at 10 MPG. Prices come from the supplied CSV (`fuel-prices-for-be-assessment.csv`).

### Success criteria

- Correct, cost-optimal fuel stops for a fixed route.
- Fast: one routing API call per request (zero when cached). Our own compute under ~100 ms.
- Clean, readable code with short plain-English comments so the author can explain it in a 5-minute Loom.
- Deliverables: GitHub repo, README, Postman collection. Loom recorded by the author.

### Constraints (from the assignment)

- Latest stable Django: **6.1.1** (Python ≥ 3.12; local Python 3.14).
- Free map/routing API; 1 call per request ideal, 2–3 acceptable.
- Due within 3 days of receiving the exercise.

## Decisions

| Topic | Decision |
|---|---|
| Input format | `"City, ST"` strings, geocoded **locally** against the Census city list. No geocoding API. |
| Starting fuel | Tank starts **empty**. Every gallon used is paid for. Total gallons = total miles ÷ 10. |
| Map output | JSON (GeoJSON route + stops + totals). Same URL with `format=map` returns a Leaflet HTML page. |
| Routing provider | **OpenRouteService** (free key, 2,000 routes/day, max route 6,000 km). |
| Framework | Plain Django (no DRF). SQLite. |
| Dependencies | `django`, `requests`, `numpy`. |

## Data facts (from inspecting the CSV)

- 8,151 rows, 6,738 unique `OPIS Truckstop ID`s (duplicates exist), 3,898 unique (city, state) pairs, 57 state codes (includes some Canadian provinces).
- Prices range $2.69–$6.40.
- **No latitude/longitude.** Address is freeform (`"I-44, EXIT 283 & US-69"`), so stations are placed at their city's center point.

## Project layout

```
manage.py
requirements.txt
.env.example                 ORS_API_KEY=
README.md
postman_collection.json
config/                      settings.py, urls.py
planner/
  models.py                  City, FuelStation
  management/commands/
    load_data.py             one-time import: Census cities + fuel CSV -> SQLite
  data/
    fuel-prices.csv          the supplied CSV
    us_places.csv            trimmed Census Gazetteer places (name, state, lat, lon)
  geocode.py                 "Chicago, IL" -> (lat, lon) via City table
  routing.py                 one ORS directions call -> route points + distance
  corridor.py                stations near the route + their mile marker
  optimizer.py               greedy fuel-stop algorithm (pure function)
  views.py                   GET /api/route/
  urls.py
  templates/planner/map.html Leaflet map
  tests/
```

Each module does one job and can be tested on its own. `optimizer.py` is pure: no DB, no network.

## Data preparation (`python manage.py load_data`)

1. **Cities.** Read `us_places.csv`, made from the US Census Gazetteer Places file (public domain, about 32k places including CDPs). Keep name, state, lat, lon. Store a normalized name:
   - lowercase, trimmed
   - drop the trailing place-type word (`city`, `town`, `village`, `CDP`, `borough`, …)
   - `st.` / `st` → `saint`, `ft.` → `fort`, and collapse punctuation and extra spaces
2. **Stations.** Read the fuel CSV. Collapse duplicate `OPIS Truckstop ID`s and keep the cheapest price. Look up each station's (normalized city, state) in `City` and copy its lat/lon.
3. Stations with no city match (Canadian provinces, places missing from the list) are skipped. The command prints matched and skipped counts.
4. **Fallback** if the US match rate is under ~90%: a one-off script geocodes the unmatched city names through Nominatim (1 request/second) and adds them to `us_places.csv`. It runs at build time only, never per request.

The command is idempotent: it clears and reloads both tables.

### Models

- `City(name, normalized_name, state, lat, lon)`, indexed on `(normalized_name, state)`.
- `FuelStation(opis_id unique, name, address, city, state, price, lat, lon)`, indexed on `(lat, lon)` for the bounding-box query.

## Request flow: `GET /api/route/?start=Chicago, IL&finish=Dallas, TX[&format=map]`

1. **Validate.** A Django Form checks that both fields exist and match `City, ST` (a two-letter state). Failure → 400.
2. **Cache.** The key is the normalized `(start, finish)`, held in Django's LocMem cache for 1 hour. On a hit, return the stored result with `cached: true` and `routing_api_calls: 0`.
3. **Geocode.** `geocode.py` looks up both cities in `City`. Not found → 404.
4. **Route.** `routing.py` sends a POST to ORS `/v2/directions/driving-car/geojson` with the two coordinates and a 10-second timeout. It returns the list of `[lon, lat]` points and the distance in miles. ORS error → 422 if no route exists, 502 for other failures or a timeout.
5. **Corridor** (`corridor.py`):
   - Resample the route to one point per mile along its length, so a point's index is its mile marker.
   - Query stations inside the route's bounding box, widened by the corridor width.
   - Using numpy, compute the haversine distance from each candidate station to every resampled point (chunked to bound memory). Keep the minimum distance and the index where it occurs.
   - Keep stations within `CORRIDOR_MILES = 10` of the route. The output is a list of `(station, mile_marker)` sorted by mile marker.
6. **Optimize** (`optimizer.py`), using `MAX_RANGE_MILES = 500` and `MPG = 10`:
   - Add a virtual "finish" point at `total_miles`.
   - The truck starts empty. The first stop is the first station on the route. The miles from the start city to that station are charged at that station's price. This keeps gallons equal to miles ÷ 10.
   - At each stop, holding fuel `f` (in miles of range), look ahead up to 500 miles:
     - If a **cheaper** station is in range, buy just enough to reach the **nearest** cheaper one, then go there.
     - Otherwise, if the **finish** is in range, buy just enough to reach it. Done.
     - Otherwise, **fill** to 500 miles and go to the **cheapest** station in range.
   - If the next station or the finish is more than 500 miles ahead → raise `UnreachableError` → 422.
   - If the first station is more than 500 miles from the start → 422.
   - For this fixed-route problem, the greedy method gives the cheapest plan.
   - Output: the stops (station, mile marker, gallons, cost) plus totals. Money is rounded to cents only for output.
7. **Respond.** Build the JSON, store it in the cache, and return it. With `format=map`, render `map.html` from the same data.

## Response

### 200 JSON

```json
{
  "start":  {"query": "Chicago, IL", "lat": 41.88, "lon": -87.63},
  "finish": {"query": "Dallas, TX",  "lat": 32.78, "lon": -96.80},
  "total_distance_miles": 925.4,
  "total_gallons": 92.54,
  "total_fuel_cost": 281.17,
  "fuel_stops": [
    {"name": "PILOT #123", "address": "I-55, EXIT 1", "city": "Joliet", "state": "IL",
     "lat": 41.52, "lon": -88.08, "mile_marker": 3.2,
     "price_per_gallon": 3.05, "gallons": 50.0, "cost": 152.5}
  ],
  "route": {"type": "LineString", "coordinates": [[-87.63, 41.88]]},
  "map_url": "/api/route/?start=Chicago%2C+IL&finish=Dallas%2C+TX&format=map",
  "routing_api_calls": 1,
  "cached": false
}
```

- `route` is the resampled line (one point per mile), not ORS's raw geometry. This keeps the payload small.
- `routing_api_calls` and `cached` make the one-call behavior visible in the demo.

### Map (`format=map`)

A single Leaflet page with OpenStreetMap tiles. It shows the route as a polyline, a pin for each fuel stop (the popup lists name, price, gallons and cost), start and finish markers, and a totals box. Leaflet loads from a CDN, so there's no build step.

### Errors: `{"error": "<message>"}`

| Code | When |
|---|---|
| 400 | Missing or badly formatted `start`/`finish` |
| 404 | City not found in the US city list |
| 422 | ORS finds no route, or the trip is impossible with a 500-mile range |
| 502 | ORS failure or a 10-second timeout |

## Configuration

- `ORS_API_KEY` is read from the environment. Settings load a local `.env` if one is present; this is a few lines of code, not a library. `.env.example` is committed and `.env` is gitignored.
- Constants: `MAX_RANGE_MILES`, `MPG` in `optimizer.py`; `CORRIDOR_MILES` in `corridor.py`.

## Testing (Django test runner)

- **optimizer**: hand-made station lists covering:
  - buying only enough to reach a cheaper station
  - filling up when nothing ahead is cheaper
  - going straight to the finish when it's in range
  - a gap over 500 miles giving `UnreachableError`
  - gallons equal to miles ÷ 10
  - a one-station trip
- **corridor**: a straight synthetic route. A station 3 miles off is kept, one 30 miles off is dropped, and mile markers are correct.
- **geocode**: normalizing `St. Louis` / `Saint Louis` / `ST LOUIS`, suffix stripping, unknown city.
- **views** (ORS mocked):
  - the 200 JSON shape
  - the second identical request is `cached: true` and makes no ORS call
  - the 400/404/422/502 paths
  - `format=map` returns HTML
- **Manual**: real runs for Chicago→Dallas and New York→Los Angeles. Record timing in the README.

## Known limitations (to note in the README)

- Station positions are city centers, so the corridor is 10 miles wide and the detour from the route to a station is not counted.
- Fuel prices are a static snapshot from the CSV.
- The cache is per process, in memory. Redis would be the upgrade if the app ran on several servers.
- Input is limited to `City, ST`. Street addresses would need a geocoding API, which means more calls.

## Out of scope

Authentication, rate limiting, deployment, a frontend beyond the single map page, and multi-stop trips.
