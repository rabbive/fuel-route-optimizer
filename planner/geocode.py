"""Turn city and state names into coordinates using the local US city table."""
import re
import unicodedata

from .models import City

PLACE_TYPE_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|city|town|village|borough|CDP|municipality|plantation)$"
)
ABBREVIATIONS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount"}


class CityNotFound(Exception):
    """Raised when a city is not in the local US city list."""


def city_key(name: str) -> str:
    """Normalize punctuation, spacing, and common city name abbreviations."""
    # Fold accents first so "Cañon City" and "Canon City" match.
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    words = re.sub(r"[^a-z0-9 ]", " ", plain.lower()).split()
    return "".join(ABBREVIATIONS.get(word, word) for word in words)


def clean_census_name(census_name: str) -> str:
    """Remove parenthetical notes and trailing place type from a Census name."""
    name = re.sub(r"\s*\(.*?\)", "", census_name).strip()
    return PLACE_TYPE_SUFFIX.sub("", name)


def find_city(name: str, state: str) -> City:
    """Look up a US city by name and state code, raising CityNotFound if absent."""
    city = City.objects.filter(key=city_key(name), state=state.upper()).first()
    if city is None:
        raise CityNotFound(f"City not found in the US city list: {name}, {state.upper()}")
    return city
