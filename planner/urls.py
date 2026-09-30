"""Map planner URLs to views."""

from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.route, name="route"),
]
