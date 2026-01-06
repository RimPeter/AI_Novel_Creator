from django.urls import path

from . import views
from .views import (
    ProjectListView,
    ProjectDetailView,
    ProjectCreateView,
    ProjectUpdateView,
)

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", ProjectListView.as_view(), name="project-list"),
    path("projects/new/", ProjectCreateView.as_view(), name="project-create"),
    path("projects/<slug:slug>/", ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<slug:slug>/edit/", ProjectUpdateView.as_view(), name="project-edit"),
]
