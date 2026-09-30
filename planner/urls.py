"""Map planner URLs to views."""

from django.urls import path

from . import views

urlpatterns = [
    path("cities/", views.cities, name="cities"),
    path("route/", views.route, name="route"),
]
