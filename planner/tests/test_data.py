"""Check local geocoding and loading of city and fuel data."""
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
NV|Carson City|39.15|-119.74
CA|San Buenaventura (Ventura) city|34.27|-119.25
ID|Boise City city|43.60|-116.23
TX|Agua Dulce CDP|31.65|-106.13
TX|Agua Dulce city|27.78|-97.90
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
    """Check city lookup normalization and Census name cleanup."""

    def test_saint_spellings_match(self):
        """Treat common Saint abbreviations as the same city key."""
        self.assertEqual(city_key("St. Louis"), "saintlouis")
        self.assertEqual(city_key("Saint Louis"), "saintlouis")
        self.assertEqual(city_key("ST LOUIS"), "saintlouis")

    def test_spaces_and_punctuation_ignored(self):
        """Ignore spacing and punctuation in city lookup keys."""
        self.assertEqual(city_key("Mc Calla"), city_key("McCalla"))
        self.assertEqual(city_key("  Chicago "), "chicago")

    def test_accents_are_folded(self):
        """Treat accented letters like their plain versions."""
        self.assertEqual(city_key("Cañon City"), "canoncity")
        self.assertEqual(city_key("Canon City"), "canoncity")
        self.assertEqual(city_key("Española"), "espanola")

    def test_clean_census_name_strips_place_type(self):
        """Remove place type suffixes while keeping name words."""
        self.assertEqual(clean_census_name("Chicago city"), "Chicago")
        self.assertEqual(clean_census_name("Abanda CDP"), "Abanda")
        self.assertEqual(clean_census_name("Oklahoma City city"), "Oklahoma City")
        self.assertEqual(clean_census_name("Carson City"), "Carson City")
        self.assertEqual(
            clean_census_name("Nashville-Davidson metropolitan government (balance)"), "Nashville-Davidson"
        )


class LoadDataTests(TestCase):
    """Check database loading, deduplication, and geocoding behavior."""

    def run_load_data(self):
        """Run load_data on small temporary input files and return its output."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "places.txt").write_text(PLACES)
        (tmp / "fuel.csv").write_text(FUEL)
        out = StringIO()
        call_command("load_data", places=tmp / "places.txt", fuel=tmp / "fuel.csv", stdout=out)
        return out.getvalue()

    def test_load_data_dedupes_matches_and_skips(self):
        """Keep the cheapest station duplicate and skip places without US matches."""
        output = self.run_load_data()

        self.assertEqual(FuelStation.objects.count(), 4)
        self.assertEqual(FuelStation.objects.get(opis_id=1).price, 3.40)
        self.assertEqual(FuelStation.objects.get(opis_id=2).lat, 36.17)
        self.assertEqual(FuelStation.objects.get(opis_id=3).lat, 38.63)
        self.assertEqual(FuelStation.objects.get(opis_id=5).lat, 35.47)
        self.assertFalse(FuelStation.objects.filter(opis_id=4).exists())
        self.assertIn("Skipped (city not found): 1", output)

    def test_aliases_and_collisions(self):
        """Add short-name aliases, and let a real city beat a CDP with the same name."""
        self.run_load_data()
        self.assertEqual(find_city("Carson City", "NV").lat, 39.15)
        self.assertEqual(find_city("Ventura", "CA").lat, 34.27)
        self.assertEqual(find_city("Boise", "ID").lat, 43.60)
        self.assertEqual(find_city("Agua Dulce", "TX").lat, 27.78)

    def test_load_data_can_run_twice(self):
        """Allow repeat runs without duplicate cities or stations."""
        self.run_load_data()
        self.run_load_data()
        self.assertEqual(FuelStation.objects.count(), 4)
        self.assertEqual(City.objects.filter(key="chicago", state="IL").count(), 1)

    def test_find_city(self):
        """Find a loaded city and raise CityNotFound for a missing one."""
        self.run_load_data()
        city = find_city("st louis", "mo")
        self.assertEqual((city.lat, city.lon), (38.63, -90.24))
        with self.assertRaises(CityNotFound):
            find_city("Atlantis", "IL")
