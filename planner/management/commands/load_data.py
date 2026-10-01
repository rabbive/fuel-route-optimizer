"""Load the local US city list and fuel price CSV into the database."""
import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from planner.geocode import city_key, clean_census_name
from planner.models import City, FuelStation

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class Command(BaseCommand):
    """Load Census places and fuel station prices from CSV files."""

    help = "Load US cities (Census Gazetteer) and fuel stations (price CSV) into the database."

    def add_arguments(self, parser):
        """Add optional paths for the places and fuel input files."""
        parser.add_argument("--places", type=Path, default=DATA_DIR / "us_places.txt")
        parser.add_argument("--fuel", type=Path, default=DATA_DIR / "fuel-prices.csv")
        parser.add_argument("--fixes", type=Path, default=DATA_DIR / "city_fixes.csv")

    @transaction.atomic
    def handle(self, *args, **options):
        """Replace existing rows with the contents of both source files."""
        cities, fixed = self.load_cities(options["places"], options["fixes"])
        loaded, skipped = self.load_stations(options["fuel"], cities)
        self.stdout.write(
            f"Cities: {len(cities)}. Stations loaded: {loaded}. Skipped (city not found): {skipped}. City fixes: {fixed}."
        )

    def load_cities(self, path, fixes):
        """Save one city per normalized name and state, including hyphen aliases."""
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="|"))

        cities = {}
        # First pass: every place under its own full name (incorporated places before CDPs).
        # When two places share a name and state, the first one added wins, so real cities beat CDPs.
        for row in sorted(rows, key=lambda r: r["NAME"].endswith(" CDP")):
            self.add_city(cities, clean_census_name(row["NAME"]), row)
        # Second pass: extra short names (e.g. "Nashville-Davidson" -> "Nashville"), only if no real place already uses that name.
        for row in rows:
            name = clean_census_name(row["NAME"])
            if "-" in name:
                self.add_city(cities, name.split("-")[0], row)
            inside = re.search(r"\((.*?)\)", row["NAME"])  # "San Buenaventura (Ventura)" -> "Ventura"
            if inside and inside[1] != "balance":
                self.add_city(cities, inside[1], row)
            if name.endswith(" City"):  # "Boise City" -> "Boise"
                self.add_city(cities, name[: -len(" City")], row)
            if name.startswith("Urban "):  # "Urban Honolulu" -> "Honolulu"
                self.add_city(cities, name[len("Urban "):], row)

        fixed = 0
        # Census interior points for a few places (e.g. San Francisco, whose limits
        # include offshore islands) aren't near a road, so we move them downtown.
        with open(fixes, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                city = cities.get((city_key(row["name"]), row["state"].strip()))
                if city is None:
                    self.stdout.write(f"City fix not found: {row['name']}, {row['state']}")
                    continue
                original = (city.lat, city.lon)
                replacement = float(row["lat"]), float(row["lon"])
                for alias in cities.values():
                    if (alias.lat, alias.lon) == original:
                        alias.lat, alias.lon = replacement
                fixed += 1

        City.objects.all().delete()
        City.objects.bulk_create(cities.values())
        return cities, fixed

    @staticmethod
    def add_city(cities, name, row):
        """Add a city if its normalized name and state are not already present."""
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
        """Save each station at its city coordinates, keeping only its lowest price."""
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
                skipped += 1
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
