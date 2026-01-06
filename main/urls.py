from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", views.ProjectListView.as_view(), name="project-list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project-create"),
    path("projects/<slug:slug>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<slug:slug>/edit/", views.ProjectUpdateView.as_view(), name="project-edit"),
    path("projects/<slug:slug>/dashboard/", views.ProjectDashboardView.as_view(), name="project-dashboard"),
]
