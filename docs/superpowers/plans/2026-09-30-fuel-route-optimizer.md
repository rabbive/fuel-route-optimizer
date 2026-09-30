# Fuel Route Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Django 6.1.1 API that takes a start and finish "City, ST" in the USA and returns the route, the cheapest fuel stops for a 500-mile range, and the total fuel cost at 10 MPG. It makes one routing API call per request.

**Architecture:** A one-time `load_data` command stores the Census city list and the fuel CSV in SQLite, placing each station at its city's center. For each request:
1. Look up both cities locally.
2. Make one OpenRouteService call for the route line.
3. Resample the line to 1 point per mile and use numpy to find the stations within 10 miles.
4. Run a pure greedy optimizer to choose the fuel stops.

Results are cached for an hour. The same URL with `format=map` renders a Leaflet map.

**Tech Stack:** Python 3.14, Django 6.1.1, requests 2.34.2, numpy 2.5.3, SQLite, Leaflet 1.9.4 (CDN), OpenRouteService.

**Spec:** `docs/superpowers/specs/2026-09-30-fuel-route-optimizer-design.md`

## Global Constraints
> Superseded on 2026-09-30: ORS timeout is 30 s and requests send "instructions": false and "radiuses": [-1, -1] (see spec §Request flow step 4 and the ledger rulings).

- Django **6.1.1**; Python ≥ 3.12 (local is 3.14). Runtime dependencies are only `Django==6.1.1`, `requests==2.34.2` and `numpy==2.5.3`.
- Plain Django: no DRF, no dotenv library, no scipy.
- **One** ORS call per uncached request; **zero** on a cache hit. Never call a geocoding API.
- ORS URL: `{ORS_BASE_URL}/v2/directions/driving-car/geojson`. `ORS_BASE_URL` defaults to `https://api.heigit.org/openrouteservice`. `ORS_API_KEY` comes from the environment or `.env`. **Never commit `.env`.**
- Constants: `MAX_RANGE_MILES = 500` and `MPG = 10` (`optimizer.py`); `CORRIDOR_MILES = 10` (`corridor.py`); cache for 1 hour; ORS timeout of 10 seconds.
- The tank starts **empty**. Every gallon is paid for, and total gallons = total miles ÷ 10.
- Errors are JSON `{"error": "<message>"}`: 400 (bad input), 404 (city not found), 422 (no route, or impossible with the range), 502 (ORS failure or timeout).
- **Code style (user requirement):** every module, class and function gets a short plain-English docstring or comment saying what it does. The author has to explain this code in a 5-minute Loom. Prefer readable over clever.
- Run tests with `.venv/bin/python manage.py test planner`.
- Do not push to GitHub or create a remote. Commits stay local until the user says otherwise.

## Review Focus

1. **Casual input.** `"chicago, il"`, `"  Chicago ,  IL "`, `"St Louis, MO"` and `"Saint Louis, MO"` should all work, and the same city should share one cache entry. Tests: Task 2 (`city_key`), Task 6 (`test_casual_input_formatting_works_and_shares_cache`).
2. **Start equals finish.** This should return 200 with 0 miles, $0 and no stops, not a 422 or a crash. Tests: Task 3 (`test_zero_mile_trip_costs_nothing`), Task 4 (`test_resample_single_point_route`), Task 6 (`test_same_start_and_finish`).
3. **A station exactly 500 miles ahead is reachable** (`<=`, not `<`). Test: Task 3 (`test_station_exactly_at_range_is_reachable`).
4. **A reversed trip (B→A) is not served from the A→B cache.** Test: Task 6 (`test_reversed_trip_is_not_served_from_cache`).
5. **Dirty CSV rows.** Duplicate station IDs with different prices keep the cheapest. Canadian or unknown cities are skipped and counted, not a crash. Test: Task 2 (`test_load_data_dedupes_matches_and_skips`).

---

## File Structure

```
.gitignore                         ignore .venv, db, .env, caches, .claude
.env.example                       ORS_API_KEY= / ORS_BASE_URL=
requirements.txt                   pinned runtime deps
README.md                          setup, API, algorithm, assumptions, timings
postman_collection.json            demo requests
manage.py                          (generated)
config/settings.py                 + .env loader, ORS settings, planner app
config/urls.py                     mounts planner under /api/
planner/models.py                  City, FuelStation
planner/geocode.py                 city_key(), clean_census_name(), find_city(), CityNotFound
planner/management/commands/load_data.py   one-time import
planner/data/fuel-prices.csv       supplied CSV (copied in)
planner/data/us_places.txt         trimmed 2025 Census Gazetteer (USPS|NAME|INTPTLAT|INTPTLONG)
planner/optimizer.py               Candidate, FuelStop, FuelPlan, plan_fuel_stops(), UnreachableError
planner/corridor.py                RouteLine, resample_route(), nearest_route_point(), stations_near_route()
planner/routing.py                 get_route(), RoutingError, NoRouteError
planner/views.py                   RouteForm, route() view, plan_trip()
planner/urls.py                    /api/route/
planner/templates/planner/map.html Leaflet page
planner/tests/__init__.py
planner/tests/test_data.py         geocode + load_data
planner/tests/test_optimizer.py
planner/tests/test_corridor.py
planner/tests/test_routing.py
planner/tests/test_views.py
```

---

### Task 1: Project skeleton

**Files:**
- Create: `requirements.txt`, `.gitignore`, `.env.example`, Django project `config/`, app `planner/`, `planner/tests/__init__.py`
- Modify: `config/settings.py`
- Delete: `planner/tests.py` (generated)

**Interfaces:**
- Produces: `settings.ORS_API_KEY: str`, `settings.ORS_BASE_URL: str`, and the installed app `planner`.

- [ ] **Step 1: Write `requirements.txt`**

```
Django==6.1.1
requests==2.34.2
numpy==2.5.3
```

- [ ] **Step 2: Create the virtualenv and install**

Run:
```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements.txt
```
Expected: installs Django 6.1.1, requests, numpy (plus their small deps). Without uv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

- [ ] **Step 3: Generate the project and app**

Run:
```bash
.venv/bin/django-admin startproject config .
.venv/bin/python manage.py startapp planner
rm planner/tests.py
mkdir -p planner/tests planner/data planner/management/commands planner/templates/planner
touch planner/tests/__init__.py planner/management/__init__.py planner/management/commands/__init__.py
```

- [ ] **Step 4: Edit `config/settings.py`**

Add `import os` next to `from pathlib import Path`. Directly after the `BASE_DIR = ...` line, add:

```python
# Load simple KEY=VALUE lines from a local .env file (if there is one) into the environment.
# Real environment variables win over the file.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _value = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _value.strip())
```

Add `"planner",` to the end of `INSTALLED_APPS`.

At the bottom of the file, add:

```python
# OpenRouteService: the free routing API we call once per trip.
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ORS_BASE_URL = os.environ.get("ORS_BASE_URL", "https://api.heigit.org/openrouteservice")
```

(Django's default cache is already the in-memory LocMem cache, so no `CACHES` setting is needed.)

- [ ] **Step 5: Write `.gitignore` and `.env.example`**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
db.sqlite3
.env
.claude/
```

`.env.example`:
```
# Get a free key at https://openrouteservice.org/dev/#/signup
ORS_API_KEY=
# Optional: ORS is moving from api.openrouteservice.org to api.heigit.org
ORS_BASE_URL=https://api.heigit.org/openrouteservice
```

- [ ] **Step 6: Verify the skeleton**

Run: `.venv/bin/python manage.py check && .venv/bin/python manage.py test planner`
Expected: `System check identified no issues (0 silenced).`, then `Ran 0 tests` / `OK` (or "NO TESTS RAN").

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .gitignore .env.example manage.py config planner
git commit -m "chore: Django 6.1.1 project skeleton with ORS settings"
```

---

### Task 2: Data layer (models, local geocoding, `load_data`)

**Files:**
- Create: `planner/geocode.py`, `planner/management/commands/load_data.py`, `planner/data/fuel-prices.csv`, `planner/data/us_places.txt`, `planner/tests/test_data.py`
- Modify: `planner/models.py`
- Generated: `planner/migrations/0001_initial.py`

**Interfaces:**
- Produces:
  - `City(name: str, key: str, state: str, lat: float, lon: float)`
  - `FuelStation(opis_id: int, name, address, city, state: str, price: float, lat: float, lon: float)`
  - `city_key(name: str) -> str`
  - `clean_census_name(census_name: str) -> str`
  - `find_city(name: str, state: str) -> City` (raises `CityNotFound`)
  - the command `load_data [--places PATH] [--fuel PATH]`

- [ ] **Step 1: Add the data files**

Run:
```bash
cp "/Users/ashwanthkumaravel/.bb/thread-storage/thr_uh7tcf5ni5/Attachments/fuel-prices-for-be-assessment.csv" planner/data/fuel-prices.csv
curl -sSL -o "$TMPDIR/gaz.zip" https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip
unzip -p "$TMPDIR/gaz.zip" 2025_Gaz_place_national.txt | cut -d'|' -f1,5,12,13 > planner/data/us_places.txt
head -3 planner/data/us_places.txt; wc -l planner/data/us_places.txt
```
Expected:
```
USPS|NAME|INTPTLAT|INTPTLONG
AL|Abanda CDP|33.091627|-85.527029
AL|Abbeville city|31.565164|-85.259165
   32351 planner/data/us_places.txt
```

- [ ] **Step 2: Write the models**

`planner/models.py`:
```python
from django.db import models


class City(models.Model):
    """A US place from the Census list. Used to turn "City, ST" into coordinates."""

    name = models.CharField(max_length=200)  # display name, e.g. "Chicago"
    key = models.CharField(max_length=200)  # lookup key from geocode.city_key(), e.g. "chicago"
    state = models.CharField(max_length=2)
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "state"], name="unique_city_key_state")]

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    """A truck stop from the fuel price CSV, placed at the center of its city."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.FloatField()  # dollars per gallon
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price:.3f}"
```

Run: `.venv/bin/python manage.py makemigrations planner`
Expected: `Create model City` and `Create model FuelStation`.

- [ ] **Step 3: Write the failing tests**

`planner/tests/test_data.py`:
```python
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from planner.geocode import CityNotFound, city_key, clean_census_name, find_city
from planner.models import City, FuelStation

PLACES = """USPS|NAME|INTPTLAT|INTPTLONG
IL|Chicago city|41.837|-87.685
TN|Nashville-Davidson metropolitan government (balance)|36.17|-86.78
MO|St. Louis city|38.63|-90.24
OK|Oklahoma City city|35.47|-97.51
"""

FUEL = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
1,PILOT A,"I-90, EXIT 1",Chicago,IL,1,3.50
1,PILOT A,"I-90, EXIT 1",Chicago,IL,1,3.40
2,LOVES B,"I-40, EXIT 2",Nashville,TN,2,3.10
3,FLYING J C,I-70,Saint Louis,MO,3,3.20
4,CANADA D,HWY 1,Calgary,AB,4,4.00
5,TA E,I-40,Oklahoma City,OK,5,2.99
"""


class CityKeyTests(SimpleTestCase):
    def test_saint_spellings_match(self):
        self.assertEqual(city_key("St. Louis"), "saintlouis")
        self.assertEqual(city_key("Saint Louis"), "saintlouis")
        self.assertEqual(city_key("ST LOUIS"), "saintlouis")

    def test_spaces_and_punctuation_ignored(self):
        self.assertEqual(city_key("Mc Calla"), city_key("McCalla"))
        self.assertEqual(city_key("  Chicago "), "chicago")

    def test_clean_census_name_strips_place_type(self):
        self.assertEqual(clean_census_name("Chicago city"), "Chicago")
        self.assertEqual(clean_census_name("Abanda CDP"), "Abanda")
        self.assertEqual(clean_census_name("Oklahoma City city"), "Oklahoma City")
        self.assertEqual(
            clean_census_name("Nashville-Davidson metropolitan government (balance)"), "Nashville-Davidson"
        )


class LoadDataTests(TestCase):
    def run_load_data(self):
        """Run load_data on small temp files and return what it printed."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "places.txt").write_text(PLACES)
        (tmp / "fuel.csv").write_text(FUEL)
        out = StringIO()
        call_command("load_data", places=tmp / "places.txt", fuel=tmp / "fuel.csv", stdout=out)
        return out.getvalue()

    def test_load_data_dedupes_matches_and_skips(self):
        output = self.run_load_data()

        self.assertEqual(FuelStation.objects.count(), 4)
        self.assertEqual(FuelStation.objects.get(opis_id=1).price, 3.40)  # cheapest duplicate kept
        self.assertEqual(FuelStation.objects.get(opis_id=2).lat, 36.17)  # "Nashville" via hyphen alias
        self.assertEqual(FuelStation.objects.get(opis_id=3).lat, 38.63)  # "Saint Louis" == "St. Louis"
        self.assertEqual(FuelStation.objects.get(opis_id=5).lat, 35.47)  # "Oklahoma City" keeps "City"
        self.assertFalse(FuelStation.objects.filter(opis_id=4).exists())  # Canada skipped
        self.assertIn("Skipped (city not found): 1", output)

    def test_load_data_can_run_twice(self):
        self.run_load_data()
        self.run_load_data()
        self.assertEqual(FuelStation.objects.count(), 4)
        self.assertEqual(City.objects.filter(key="chicago", state="IL").count(), 1)

    def test_find_city(self):
        self.run_load_data()
        city = find_city("st louis", "mo")
        self.assertEqual((city.lat, city.lon), (38.63, -90.24))
        with self.assertRaises(CityNotFound):
            find_city("Atlantis", "IL")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python manage.py test planner.tests.test_data`
Expected: ERROR, `ModuleNotFoundError: No module named 'planner.geocode'`.

- [ ] **Step 5: Write `planner/geocode.py`**

```python
"""Turn "City, ST" into map coordinates using our local US city table (no API calls)."""
import re

from .models import City

# Words the Census adds to the end of place names, e.g. "Chicago city", "Abanda CDP".
PLACE_TYPE_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|city|town|village|borough|cdp|municipality|plantation)$",
    re.IGNORECASE,
)

# Short forms people write, mapped to the full word, so "St. Louis" matches "Saint Louis".
ABBREVIATIONS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}


class CityNotFound(Exception):
    """Raised when a city is not in our US city list."""


def city_key(name: str) -> str:
    """Make a lookup key from a city name.

    It lowercases, drops punctuation, expands abbreviations and removes spaces,
    so "St. Louis", "Saint Louis" and "ST LOUIS" all become "saintlouis".
    """
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return "".join(ABBREVIATIONS.get(word, word) for word in words)


def clean_census_name(census_name: str) -> str:
    """Remove the extras the Census adds to a place name.

    "Chicago city" -> "Chicago"
    "Nashville-Davidson metropolitan government (balance)" -> "Nashville-Davidson"
    Only the last place-type word goes, so "Oklahoma City city" -> "Oklahoma City".
    """
    name = re.sub(r"\s*\(.*?\)", "", census_name).strip()
    return PLACE_TYPE_SUFFIX.sub("", name)


def find_city(name: str, state: str) -> City:
    """Look up a US city by name and 2-letter state code. Raises CityNotFound."""
    city = City.objects.filter(key=city_key(name), state=state.upper()).first()
    if city is None:
        raise CityNotFound(f"City not found in the US city list: {name}, {state.upper()}")
    return city
```

- [ ] **Step 6: Write `planner/management/commands/load_data.py`**

```python
"""Load the US city list and the fuel price CSV into the database. Run once after migrating."""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from planner.geocode import city_key, clean_census_name
from planner.models import City, FuelStation

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class Command(BaseCommand):
    help = "Load US cities (Census Gazetteer) and fuel stations (price CSV) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--places", type=Path, default=DATA_DIR / "us_places.txt")
        parser.add_argument("--fuel", type=Path, default=DATA_DIR / "fuel-prices.csv")

    @transaction.atomic
    def handle(self, *args, **options):
        """Wipe both tables and reload them, so running this twice gives the same result."""
        cities = self.load_cities(options["places"])
        loaded, skipped = self.load_stations(options["fuel"], cities)
        self.stdout.write(
            f"Cities: {len(cities)}. Stations loaded: {loaded}. Skipped (city not found): {skipped}."
        )

    def load_cities(self, path):
        """Save one City per (name, state) from the Census file. Returns {(key, state): City}."""
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="|"))

        cities = {}
        # First pass: the full names, e.g. "Nashville-Davidson".
        for row in rows:
            self.add_city(cities, clean_census_name(row["NAME"]), row)
        # Second pass: the part before a hyphen, e.g. "Nashville", but only if no real city has that name.
        for row in rows:
            name = clean_census_name(row["NAME"])
            if "-" in name:
                self.add_city(cities, name.split("-")[0], row)

        City.objects.all().delete()
        City.objects.bulk_create(cities.values())
        return cities

    @staticmethod
    def add_city(cities, name, row):
        """Add a City to the dict unless one with the same key and state is already there."""
        key = (city_key(name), row["USPS"].strip())
        if key not in cities:
            cities[key] = City(
                name=name,
                key=key[0],
                state=key[1],
                lat=float(row["INTPTLAT"]),
                lon=float(row["INTPTLONG"]),
            )

    def load_stations(self, path, cities):
        """Save each station once (cheapest price wins), placed at its city. Returns (loaded, skipped)."""
        cheapest = {}  # station ID -> the CSV row with its lowest price
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                opis_id = int(row["OPIS Truckstop ID"])
                if opis_id not in cheapest or float(row["Retail Price"]) < float(cheapest[opis_id]["Retail Price"]):
                    cheapest[opis_id] = row

        stations, skipped = [], 0
        for opis_id, row in cheapest.items():
            city = cities.get((city_key(row["City"]), row["State"].strip()))
            if city is None:
                skipped += 1  # e.g. Canadian stations, or tiny places missing from the Census list
                continue
            stations.append(
                FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=row["City"].strip(),
                    state=row["State"].strip(),
                    price=float(row["Retail Price"]),
                    lat=city.lat,
                    lon=city.lon,
                )
            )

        FuelStation.objects.all().delete()
        FuelStation.objects.bulk_create(stations)
        return len(stations), skipped
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python manage.py test planner.tests.test_data`
Expected: `Ran 6 tests` / `OK`.

- [ ] **Step 8: Load the real data**

Run: `.venv/bin/python manage.py migrate && .venv/bin/python manage.py load_data`
Expected: `Cities: ~32,000. Stations loaded: ~6,300. Skipped (city not found): ~400` (roughly 6,700 unique IDs; the skipped ones are mostly Canadian). If more than 10% of the **US** stations are skipped, stop and report back, because the spec's fallback would then be needed.

- [ ] **Step 9: Commit**

```bash
git add planner/models.py planner/migrations planner/geocode.py planner/management planner/data planner/tests/test_data.py
git commit -m "feat: city/station models, local geocoding and load_data command"
```

---

### Task 3: Fuel stop optimizer (pure function)

**Files:**
- Create: `planner/optimizer.py`, `planner/tests/test_optimizer.py`

**Interfaces:**
- Produces:
  - `Candidate(mile: float, price: float, station: Any = None)`
  - `FuelStop(candidate: Candidate, gallons: float, cost: float)`
  - `FuelPlan(stops: list[FuelStop], total_gallons: float, total_cost: float)`
  - `plan_fuel_stops(candidates: Iterable[Candidate], total_miles: float, max_range: float = 500, mpg: float = 10) -> FuelPlan`
  - `UnreachableError(Exception)`
  - the constants `MAX_RANGE_MILES`, `MPG`

- [ ] **Step 1: Write the failing tests**

`planner/tests/test_optimizer.py`:
```python
from django.test import SimpleTestCase

from planner.optimizer import Candidate, UnreachableError, plan_fuel_stops


def summary(plan):
    """(mile, gallons) for each stop, rounded, so the tests are easy to read."""
    return [(s.candidate.mile, round(s.gallons, 2)) for s in plan.stops]


class PlanFuelStopsTests(SimpleTestCase):
    def test_short_trip_buys_at_first_station_only(self):
        plan = plan_fuel_stops([Candidate(0, 3.0)], total_miles=200)
        self.assertEqual(summary(plan), [(0, 20.0)])
        self.assertAlmostEqual(plan.total_cost, 60.0)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        plan = plan_fuel_stops([Candidate(0, 4.0), Candidate(100, 3.0)], total_miles=300)
        # 100 miles at $4, then the remaining 200 miles at the cheaper $3.
        self.assertEqual(summary(plan), [(0, 10.0), (100, 20.0)])
        self.assertAlmostEqual(plan.total_cost, 10 * 4.0 + 20 * 3.0)

    def test_fills_up_when_nothing_ahead_is_cheaper(self):
        stations = [Candidate(0, 3.0), Candidate(300, 4.0), Candidate(450, 3.5)]
        plan = plan_fuel_stops(stations, total_miles=800)
        # Mile 0 is cheapest and the finish is out of range: fill up (500 miles).
        # Drive to the cheapest station in range (mile 450) with 50 miles left, then buy 300 to finish.
        self.assertEqual(summary(plan), [(0, 50.0), (450, 30.0)])
        self.assertAlmostEqual(plan.total_cost, 50 * 3.0 + 30 * 3.5)

    def test_drive_before_first_station_is_paid_at_its_price(self):
        plan = plan_fuel_stops([Candidate(40, 3.0)], total_miles=240)
        self.assertEqual(summary(plan), [(40, 24.0)])
        self.assertAlmostEqual(plan.total_cost, 72.0)

    def test_gallons_always_equal_miles_divided_by_mpg(self):
        prices = [3.9, 3.1, 3.6, 2.9, 3.3, 3.8, 3.0, 3.4, 3.2, 3.7, 3.5, 3.05, 3.15]
        stations = [Candidate(7 + 120 * k, p) for k, p in enumerate(prices)]
        plan = plan_fuel_stops(stations, total_miles=1500)
        self.assertAlmostEqual(plan.total_gallons, 150.0)

    def test_station_exactly_at_range_is_reachable(self):
        plan = plan_fuel_stops([Candidate(0, 3.0), Candidate(500, 3.0)], total_miles=900)
        self.assertEqual(summary(plan), [(0, 50.0), (500, 40.0)])

    def test_gap_longer_than_range_raises(self):
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([Candidate(0, 3.0), Candidate(600, 3.0)], total_miles=900)

    def test_no_station_near_start_raises(self):
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([Candidate(501, 3.0)], total_miles=900)

    def test_no_stations_at_all_raises(self):
        with self.assertRaises(UnreachableError):
            plan_fuel_stops([], total_miles=100)

    def test_zero_mile_trip_costs_nothing(self):
        plan = plan_fuel_stops([], total_miles=0)
        self.assertEqual((plan.stops, plan.total_gallons, plan.total_cost), ([], 0.0, 0.0))

    def test_candidates_in_any_order(self):
        plan = plan_fuel_stops([Candidate(100, 3.0), Candidate(0, 4.0)], total_miles=300)
        self.assertEqual(summary(plan), [(0, 10.0), (100, 20.0)])

    def test_station_object_is_handed_back(self):
        plan = plan_fuel_stops([Candidate(0, 3.0, station="PILOT #1")], total_miles=50)
        self.assertEqual(plan.stops[0].candidate.station, "PILOT #1")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python manage.py test planner.tests.test_optimizer`
Expected: ERROR, `ModuleNotFoundError: No module named 'planner.optimizer'`.

- [ ] **Step 3: Write `planner/optimizer.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python manage.py test planner.tests.test_optimizer`
Expected: `Ran 12 tests` / `OK`.

- [ ] **Step 5: Commit**

```bash
git add planner/optimizer.py planner/tests/test_optimizer.py
git commit -m "feat: greedy fuel stop optimizer"
```

---

### Task 4: Route corridor (match stations to the route)

**Files:**
- Create: `planner/corridor.py`, `planner/tests/test_corridor.py`

**Interfaces:**
- Consumes: `FuelStation` (Task 2), `Candidate` (Task 3).
- Produces:
  - `RouteLine(lats: np.ndarray, lons: np.ndarray, miles: np.ndarray, total_miles: float)` with `.as_geojson() -> dict`
  - `resample_route(coordinates: list[list[float]]) -> RouteLine` (input is ORS `[lon, lat]` pairs)
  - `nearest_route_point(line, lats, lons) -> tuple[np.ndarray, np.ndarray]` (distance in miles, mile marker)
  - `stations_near_route(line: RouteLine) -> list[Candidate]` (each `.station` is a `FuelStation`)
  - the constant `CORRIDOR_MILES = 10`

- [ ] **Step 1: Write the failing tests**

`planner/tests/test_corridor.py`:
```python
import math

from django.test import SimpleTestCase, TestCase

from planner.corridor import nearest_route_point, resample_route, stations_near_route
from planner.models import FuelStation

MILES_PER_DEG_LAT = 69.09  # with Earth radius 3958.8 miles
# A straight east-west route along latitude 40, from longitude -100 to -99 (about 53 miles).
ROUTE = [[-100.0, 40.0], [-99.0, 40.0]]
ROUTE_MILES = MILES_PER_DEG_LAT * math.cos(math.radians(40))  # ~52.9


def north_of(lat, miles):
    return lat + miles / MILES_PER_DEG_LAT


class ResampleRouteTests(SimpleTestCase):
    def test_points_one_mile_apart_ending_at_finish(self):
        line = resample_route(ROUTE)
        self.assertAlmostEqual(line.total_miles, ROUTE_MILES, delta=0.1)
        self.assertEqual(line.miles[0], 0.0)
        self.assertEqual(line.miles[1], 1.0)
        self.assertAlmostEqual(line.miles[-1], line.total_miles)
        self.assertAlmostEqual(line.lons[-1], -99.0)
        self.assertEqual(len(line.miles), math.ceil(line.total_miles) + 1)

    def test_resample_single_point_route(self):
        line = resample_route([[-100.0, 40.0], [-100.0, 40.0]])
        self.assertEqual(line.total_miles, 0.0)
        self.assertEqual(line.as_geojson()["coordinates"], [[-100.0, 40.0]])

    def test_geojson_is_lon_lat(self):
        coords = resample_route(ROUTE).as_geojson()["coordinates"]
        self.assertEqual(coords[0], [-100.0, 40.0])


class NearestRoutePointTests(SimpleTestCase):
    def test_distance_and_mile_marker(self):
        line = resample_route(ROUTE)
        distances, miles = nearest_route_point(line, [north_of(40, 3), north_of(40, 30)], [-99.5, -99.5])
        self.assertAlmostEqual(distances[0], 3.0, delta=0.1)
        self.assertAlmostEqual(distances[1], 30.0, delta=0.3)
        self.assertAlmostEqual(miles[0], ROUTE_MILES / 2, delta=1.0)


class StationsNearRouteTests(TestCase):
    def add_station(self, opis_id, lat, lon, price=3.0):
        return FuelStation.objects.create(
            opis_id=opis_id, name=f"S{opis_id}", address="", city="X", state="KS", price=price, lat=lat, lon=lon
        )

    def test_keeps_only_stations_inside_corridor(self):
        near = self.add_station(1, north_of(40, 3), -99.5, price=3.25)
        # Inside the bounding box, but about 12.7 miles from the start corner of the route.
        self.add_station(2, north_of(40, 9), -100 - 9 / (MILES_PER_DEG_LAT * math.cos(math.radians(40))))
        self.add_station(3, north_of(40, 30), -99.5)  # far away

        found = stations_near_route(resample_route(ROUTE))

        self.assertEqual([c.station for c in found], [near])
        self.assertEqual(found[0].price, 3.25)
        self.assertAlmostEqual(found[0].mile, ROUTE_MILES / 2, delta=1.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python manage.py test planner.tests.test_corridor`
Expected: ERROR, `ModuleNotFoundError: No module named 'planner.corridor'`.

- [ ] **Step 3: Write `planner/corridor.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python manage.py test planner.tests.test_corridor`
Expected: `Ran 5 tests` / `OK`.

- [ ] **Step 5: Commit**

```bash
git add planner/corridor.py planner/tests/test_corridor.py
git commit -m "feat: match fuel stations to the route corridor"
```

---

### Task 5: OpenRouteService client

**Files:**
- Create: `planner/routing.py`, `planner/tests/test_routing.py`

**Interfaces:**
- Consumes: `settings.ORS_API_KEY`, `settings.ORS_BASE_URL` (Task 1).
- Produces: `get_route(start: tuple[float, float], finish: tuple[float, float]) -> list[list[float]]`. The inputs are `(lat, lon)`; the output is `[lon, lat]` points. It raises `NoRouteError` or `RoutingError`, which are unrelated exception classes.

- [ ] **Step 1: Write the failing tests**

`planner/tests/test_routing.py`:
```python
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
    @patch("planner.routing.requests.post", return_value=fake_response(200, GEOJSON))
    def test_returns_coordinates_and_sends_lon_lat(self, post):
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
        with self.assertRaisesRegex(NoRouteError, "Could not find routable point"):
            get_route((21.3, -157.8), (41.9, -87.6))

    @patch(
        "planner.routing.requests.post",
        return_value=fake_response(403, {"error": "Access to this API has been disallowed"}),
    )
    def test_bad_key_is_routing_error(self, post):
        with self.assertRaisesRegex(RoutingError, "403.*disallowed"):
            get_route((41.9, -87.6), (32.8, -96.8))

    @patch("planner.routing.requests.post", side_effect=requests.Timeout("slow"))
    def test_timeout_is_routing_error(self, post):
        with self.assertRaises(RoutingError):
            get_route((41.9, -87.6), (32.8, -96.8))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python manage.py test planner.tests.test_routing`
Expected: ERROR, `ModuleNotFoundError: No module named 'planner.routing'`.

- [ ] **Step 3: Write `planner/routing.py`**

```python
"""Get a driving route from OpenRouteService (ORS). This is the only external API we call."""
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
    return response.json()["features"][0]["geometry"]["coordinates"]


def error_message(response) -> str:
    """Pull the readable message out of an ORS error response.

    ORS sends either {"error": "text"} or {"error": {"code": 2010, "message": "text"}}.
    """
    try:
        error = response.json().get("error", "")
    except (ValueError, AttributeError):
        return response.text[:200]
    return error.get("message", str(error)) if isinstance(error, dict) else str(error)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python manage.py test planner.tests.test_routing`
Expected: `Ran 4 tests` / `OK`.

- [ ] **Step 5: Commit**

```bash
git add planner/routing.py planner/tests/test_routing.py
git commit -m "feat: OpenRouteService routing client"
```

---

### Task 6: API endpoint and map page

**Files:**
- Create: `planner/urls.py`, `planner/templates/planner/map.html`, `planner/tests/test_views.py`
- Modify: `planner/views.py` (replace the generated file), `config/urls.py`

**Interfaces:**
- Consumes:
  - `find_city`, `city_key`, `CityNotFound` (Task 2)
  - `plan_fuel_stops`, `UnreachableError` (Task 3)
  - `resample_route`, `stations_near_route` (Task 4)
  - `get_route`, `NoRouteError`, `RoutingError` (Task 5)
- Produces:
  - `GET /api/route/?start=City, ST&finish=City, ST[&format=map]`, with the URL name `route`
  - the JSON shape from the spec

- [ ] **Step 1: Write the failing tests**

`planner/tests/test_views.py`:
```python
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from planner.models import City, FuelStation
from planner.routing import NoRouteError, RoutingError

CHICAGO = (41.88, -87.63)
DALLAS = (32.78, -96.80)
# A fake straight route Chicago -> Dallas ([lon, lat] like ORS returns). About 800 miles.
ROUTE = [[CHICAGO[1], CHICAGO[0]], [DALLAS[1], DALLAS[0]]]
URL = "/api/route/"


@patch("planner.views.get_route", return_value=ROUTE)
class RouteViewTests(TestCase):
    def setUp(self):
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
        return self.client.get(URL, {"start": "Chicago, IL", "finish": "Dallas, TX", **params})

    def test_returns_trip_json(self, get_route):
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
        first = self.get().json()
        second = self.get().json()

        self.assertEqual(get_route.call_count, 1)
        self.assertEqual((second["routing_api_calls"], second["cached"]), (0, True))
        self.assertEqual(second["total_fuel_cost"], first["total_fuel_cost"])

    def test_casual_input_formatting_works_and_shares_cache(self, get_route):
        self.get()
        response = self.client.get(URL, {"start": "  chicago ,  il ", "finish": "DALLAS, tx"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cached"])
        self.assertEqual(get_route.call_count, 1)

    def test_reversed_trip_is_not_served_from_cache(self, get_route):
        self.get()
        get_route.return_value = list(reversed(ROUTE))
        response = self.client.get(URL, {"start": "Dallas, TX", "finish": "Chicago, IL"})

        self.assertFalse(response.json()["cached"])
        self.assertEqual(get_route.call_count, 2)

    def test_same_start_and_finish(self, get_route):
        get_route.return_value = [ROUTE[0], ROUTE[0]]
        data = self.client.get(URL, {"start": "Chicago, IL", "finish": "Chicago, IL"}).json()

        self.assertEqual((data["total_distance_miles"], data["total_fuel_cost"], data["fuel_stops"]), (0, 0, []))

    def test_map_format_returns_html(self, get_route):
        response = self.get(format="map")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "leaflet")
        self.assertContains(response, "trip-data")

    def test_missing_finish_is_400(self, get_route):
        response = self.client.get(URL, {"start": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json()["error"])

    def test_bad_format_is_400(self, get_route):
        response = self.client.get(URL, {"start": "Chicago", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("City, ST", response.json()["error"])

    def test_unknown_city_is_404(self, get_route):
        response = self.client.get(URL, {"start": "Atlantis, IL", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 404)
        get_route.assert_not_called()

    def test_no_route_is_422(self, get_route):
        get_route.side_effect = NoRouteError("No driving route found: island")
        self.assertEqual(self.get().status_code, 422)

    def test_no_stations_is_422(self, get_route):
        FuelStation.objects.all().delete()
        response = self.get()
        self.assertEqual(response.status_code, 422)
        self.assertIn("No fuel station", response.json()["error"])

    def test_routing_failure_is_502(self, get_route):
        get_route.side_effect = RoutingError("Could not reach the routing service.")
        self.assertEqual(self.get().status_code, 502)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python manage.py test planner.tests.test_views`
Expected: the tests FAIL or ERROR. `@patch("planner.views.get_route")` raises `AttributeError: ... does not have the attribute 'get_route'`.

- [ ] **Step 3: Write `planner/views.py`**

```python
"""The API endpoint: GET /api/route/?start=City, ST&finish=City, ST[&format=map]."""
import re
from urllib.parse import urlencode

from django import forms
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from .corridor import resample_route, stations_near_route
from .geocode import CityNotFound, city_key, find_city
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
        return parse_city_state(self.cleaned_data["start"])

    def clean_finish(self):
        return parse_city_state(self.cleaned_data["finish"])


def route(request):
    """Return the route, the cheapest fuel stops and the total fuel cost (JSON, or a map page)."""
    form = RouteForm(request.GET)
    if not form.is_valid():
        field, errors = next(iter(form.errors.items()))
        return error_response(400, f"{field}: {errors[0]}")
    start, finish = form.cleaned_data["start"], form.cleaned_data["finish"]

    # Same cities (however they were typed) in the same order share one cache entry.
    cache_key = "trip:" + "|".join(f"{city_key(city)},{state}" for city, state in (start, finish))
    trip = cache.get(cache_key)
    if trip is not None:
        trip = {**trip, "cached": True, "routing_api_calls": 0}
    else:
        try:
            trip = plan_trip(start, finish)
        except CityNotFound as exc:
            return error_response(404, str(exc))
        except (NoRouteError, UnreachableError) as exc:
            return error_response(422, str(exc))
        except RoutingError as exc:
            return error_response(502, str(exc))
        cache.set(cache_key, trip, CACHE_SECONDS)

    trip["map_url"] = request.build_absolute_uri(
        reverse("route") + "?" + urlencode({"start": trip["start"]["query"], "finish": trip["finish"]["query"], "format": "map"})
    )
    if request.GET.get("format") == "map":
        return render(request, "planner/map.html", {"trip": trip})
    return JsonResponse(trip)


def plan_trip(start: tuple[str, str], finish: tuple[str, str]) -> dict:
    """The whole pipeline: find the cities, make ONE routing call, match stations, pick fuel stops."""
    start_city, finish_city = find_city(*start), find_city(*finish)
    coordinates = get_route((start_city.lat, start_city.lon), (finish_city.lat, finish_city.lon))
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
        "routing_api_calls": 1,
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
```

- [ ] **Step 4: Wire up the URLs**

`planner/urls.py`:
```python
from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.route, name="route"),
]
```

`config/urls.py`: replace the whole file:
```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("planner.urls")),
]
```

- [ ] **Step 5: Write the map page `planner/templates/planner/map.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fuel route: {{ trip.start.query }} to {{ trip.finish.query }}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
    #summary {
      position: absolute; top: 10px; right: 10px; z-index: 1000;
      background: white; padding: 10px 14px; border-radius: 6px;
      font: 14px/1.5 sans-serif; box-shadow: 0 1px 4px rgba(0, 0, 0, .3);
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="summary">
    <strong>{{ trip.start.query }} → {{ trip.finish.query }}</strong><br>
    {{ trip.total_distance_miles }} miles · {{ trip.fuel_stops|length }} fuel stops<br>
    {{ trip.total_gallons }} gallons · <strong>${{ trip.total_fuel_cost }}</strong>
  </div>

  {{ trip|json_script:"trip-data" }}
  <script>
    // Read the trip JSON that Django put into the page.
    const trip = JSON.parse(document.getElementById("trip-data").textContent);
    const map = L.map("map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    // Draw the route and zoom the map to fit it.
    const route = L.geoJSON(trip.route, { style: { color: "#2563eb", weight: 5 } }).addTo(map);
    map.fitBounds(route.getBounds(), { padding: [30, 30] });

    // Start and finish pins.
    L.marker([trip.start.lat, trip.start.lon]).addTo(map).bindPopup("Start: " + trip.start.query);
    L.marker([trip.finish.lat, trip.finish.lon]).addTo(map).bindPopup("Finish: " + trip.finish.query);

    // One orange dot per fuel stop, with the details in its popup.
    trip.fuel_stops.forEach((stop, i) => {
      L.circleMarker([stop.lat, stop.lon], { radius: 8, color: "#ea580c", fillOpacity: 0.9 })
        .addTo(map)
        .bindPopup(
          `<b>Stop ${i + 1}: ${stop.name}</b><br>${stop.address}, ${stop.city}, ${stop.state}<br>` +
          `Mile ${stop.mile_marker} · $${stop.price_per_gallon}/gal<br>${stop.gallons} gal = $${stop.cost}`
        );
    });
  </script>
</body>
</html>
```

(The map's `leaflet` text satisfies `assertContains(response, "leaflet")`.)

- [ ] **Step 6: Run the view tests, then the whole suite**

Run: `.venv/bin/python manage.py test planner.tests.test_views && .venv/bin/python manage.py test planner`
Expected: `Ran 12 tests` / `OK`, then the full suite: `Ran 39 tests` / `OK`.

- [ ] **Step 7: Commit**

```bash
git add planner/views.py planner/urls.py config/urls.py planner/templates planner/tests/test_views.py
git commit -m "feat: /api/route/ endpoint with caching and Leaflet map view"
```

---

### Task 7: Real run, timings, README, Postman collection

**Files:**
- Create: `README.md`, `postman_collection.json`

**Interfaces:**
- Consumes: the running API from Task 6. The **user** must first put their key in `.env` (`cp .env.example .env`, then paste the key). Never print or commit the key.

- [ ] **Step 1: Ask the user to create `.env` with their ORS key, then start the server**

Run: `.venv/bin/python manage.py runserver` (in the background).

- [ ] **Step 2: Real requests, with timing**

Run:
```bash
for q in "start=Chicago,%20IL&finish=Dallas,%20TX" "start=New%20York,%20NY&finish=Los%20Angeles,%20CA"; do
  curl -s -o "$TMPDIR/out.json" -w "first:  %{http_code} %{time_total}s\n" "http://127.0.0.1:8000/api/route/?$q"
  python3 -c "import json;d=json.load(open('$TMPDIR/out.json'));print(d.get('error') or {k:d[k] for k in ('total_distance_miles','total_gallons','total_fuel_cost','routing_api_calls')}, len(d.get('fuel_stops',[])),'stops')"
  curl -s -o /dev/null -w "cached: %{http_code} %{time_total}s\n" "http://127.0.0.1:8000/api/route/?$q"
done
```
Expected:
- Chicago→Dallas: 200, about 900–1,000 miles, 2–3 stops, gallons ≈ miles/10.
- New York→Los Angeles: 200, about 2,700–2,900 miles, 6 or more stops.
- The first request takes about 1–2 seconds; the cached one takes under 50 ms.

Sanity-check the stops: no gap between consecutive `mile_marker`s (or from the last stop to the finish) should exceed 500.

- [ ] **Step 3: Measure our own compute, not counting the ORS call**

Run:
```bash
.venv/bin/python manage.py shell -c "
import time
from planner.geocode import find_city
from planner.routing import get_route
from planner.corridor import resample_route, stations_near_route
from planner.optimizer import plan_fuel_stops
a, b = find_city('New York', 'NY'), find_city('Los Angeles', 'CA')
coords = get_route((a.lat, a.lon), (b.lat, b.lon))
t = time.perf_counter()
line = resample_route(coords); plan = plan_fuel_stops(stations_near_route(line), line.total_miles)
print(f'{len(coords)} ORS points, {len(line.miles)} resampled, compute {1000*(time.perf_counter()-t):.0f} ms, {len(plan.stops)} stops')
"
```
Expected: compute under about 150 ms. If it's much slower, report the number rather than optimizing without being asked.

- [ ] **Step 4: Check the map page**

Open `http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Dallas,%20TX&format=map` in a browser, using the browser tool, and screenshot it. Expected: a blue route line, orange stop dots with popups, and the totals box.

- [ ] **Step 5: Write `postman_collection.json`**

```json
{
  "info": {
    "name": "Fuel Route Optimizer",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "variable": [{ "key": "base_url", "value": "http://127.0.0.1:8000" }],
  "item": [
    {
      "name": "Chicago, IL -> Dallas, TX",
      "request": { "method": "GET", "url": "{{base_url}}/api/route/?start=Chicago, IL&finish=Dallas, TX" }
    },
    {
      "name": "New York, NY -> Los Angeles, CA (coast to coast)",
      "request": { "method": "GET", "url": "{{base_url}}/api/route/?start=New York, NY&finish=Los Angeles, CA" }
    },
    {
      "name": "Map view (HTML) - Chicago -> Dallas",
      "request": { "method": "GET", "url": "{{base_url}}/api/route/?start=Chicago, IL&finish=Dallas, TX&format=map" }
    },
    {
      "name": "Error 400 - bad format",
      "request": { "method": "GET", "url": "{{base_url}}/api/route/?start=Chicago&finish=Dallas, TX" }
    },
    {
      "name": "Error 404 - unknown city",
      "request": { "method": "GET", "url": "{{base_url}}/api/route/?start=Atlantis, IL&finish=Dallas, TX" }
    }
  ]
}
```

- [ ] **Step 6: Write `README.md`**

Put the numbers measured in Steps 2–3 into the "Speed" table.

````markdown
# Fuel Route Optimizer

A Django API that plans a road trip between two US cities. It returns the route on a map, the cheapest places to buy fuel along it, and the total fuel cost.

- Vehicle range: **500 miles** per tank. Fuel economy: **10 MPG**.
- Fuel prices come from `planner/data/fuel-prices.csv`.
- Routing uses [OpenRouteService](https://openrouteservice.org). Each trip makes **one** routing call, and a repeated trip makes none because it is cached.

## Setup

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
- The detour from the route to a station isn't counted in distance or cost.

## Speed (measured on a laptop)

| Trip | First request | Cached request | Our compute (excl. ORS) |
|---|---|---|---|
| Chicago → Dallas | _s | _ms | — |
| New York → Los Angeles | _s | _ms | _ms |

## Limits / next steps

- The cache lives in memory in one process. Use Redis if the app runs on several servers.
- Input is limited to "City, ST". Supporting street addresses would need a geocoding API (more calls).
- Fuel prices are a static snapshot.
````

Replace each `_` in the Speed table with the measured value before committing.

- [ ] **Step 7: Run the full suite one last time**

Run: `.venv/bin/python manage.py test planner`
Expected: `Ran 39 tests` / `OK`.

- [ ] **Step 8: Commit**

```bash
git add README.md postman_collection.json
git commit -m "docs: README with setup, algorithm, timings; Postman collection"
```

Do **not** push. Tell the user the repo is ready and ask whether to create the GitHub repo and push.
