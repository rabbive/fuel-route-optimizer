"""Define database models for route planning."""
from django.db import models


class City(models.Model):
    """A US place from the Census list, used to turn city names into coordinates."""

    name = models.CharField(max_length=200)
    key = models.CharField(max_length=200)
    state = models.CharField(max_length=2)
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        """Keep one normalized city name per state."""

        constraints = [models.UniqueConstraint(fields=["key", "state"], name="unique_city_key_state")]

    def __str__(self):
        """Return the display name and state."""
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    """A truck stop from the fuel price CSV, placed at the center of its city."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.FloatField()
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        """Index station coordinates for geographic lookups."""

        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        """Return a readable station label and fuel price."""
        return f"{self.name} ({self.city}, {self.state}) ${self.price:.3f}"
