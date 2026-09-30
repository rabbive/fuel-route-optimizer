"""Map project URLs to Django views."""

from django.contrib import admin
from django.urls import include, path

from planner import views

urlpatterns = [
    path("", views.home, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include("planner.urls")),
]
