from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("projects/", views.ProjectListView.as_view(), name="project-list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project-create"),
    path("projects/<slug:slug>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<slug:slug>/edit/", views.ProjectUpdateView.as_view(), name="project-edit"),
    path("projects/<slug:slug>/brainstorm/", views.brainstorm_project, name="project-brainstorm"),
    path("projects/<slug:slug>/add-details/", views.add_project_details, name="project-add-details"),
    path("projects/<slug:slug>/characters/", views.CharacterListView.as_view(), name="character-list"),
    path("projects/<slug:slug>/characters/new/", views.CharacterCreateView.as_view(), name="character-create"),
    path("projects/<slug:slug>/characters/brainstorm/", views.brainstorm_character, name="character-brainstorm"),
    path("projects/<slug:slug>/characters/add-details/", views.add_character_details, name="character-add-details"),
    path("projects/<slug:slug>/characters/<uuid:pk>/edit/", views.CharacterUpdateView.as_view(), name="character-edit"),
    path("projects/<slug:slug>/characters/<uuid:pk>/delete/", views.CharacterDeleteView.as_view(), name="character-delete"),
    path("projects/<slug:slug>/locations/", views.LocationListView.as_view(), name="location-list"),
    path("projects/<slug:slug>/locations/new/", views.LocationCreateView.as_view(), name="location-create"),
    path("projects/<slug:slug>/locations/brainstorm/", views.brainstorm_location_description, name="location-brainstorm"),
    path("projects/<slug:slug>/locations/add-details/", views.add_location_details, name="location-add-details"),
    path("projects/<slug:slug>/locations/extract-objects/", views.extract_location_objects, name="location-extract-objects"),
    path("projects/<slug:slug>/locations/<uuid:pk>/edit/", views.LocationUpdateView.as_view(), name="location-edit"),
    path("projects/<slug:slug>/locations/<uuid:pk>/delete/", views.LocationDeleteView.as_view(), name="location-delete"),
    path("projects/<slug:slug>/dashboard/", views.ProjectDashboardView.as_view(), name="project-dashboard"),
    path("projects/<slug:slug>/bible/edit/", views.StoryBibleUpdateView.as_view(), name="bible-edit"),
    path(
        "projects/<slug:slug>/outline/chapters/new/<uuid:act_id>/",
        views.OutlineChapterCreateView.as_view(),
        name="chapter-add",
    ),
    path(
        "projects/<slug:slug>/outline/scenes/new/<uuid:chapter_id>/",
        views.OutlineSceneCreateView.as_view(),
        name="scene-add",
    ),
    path(
        "projects/<slug:slug>/outline/scenes/move/",
        views.move_scene,
        name="scene-move",
    ),
    path(
        "projects/<slug:slug>/outline/node/<uuid:pk>/edit/",
        views.OutlineNodeUpdateView.as_view(),
        name="outline-node-edit",
    ),
    path(
        "projects/<slug:slug>/outline/node/<uuid:pk>/brainstorm/",
        views.brainstorm_scene,
        name="scene-brainstorm",
    ),
    path(
        "projects/<slug:slug>/outline/node/<uuid:pk>/add-details/",
        views.add_scene_details,
        name="scene-add-details",
    ),
    path(
        "projects/<slug:slug>/outline/node/<uuid:pk>/delete/",
        views.OutlineNodeDeleteView.as_view(),
        name="outline-node-delete",
    ),
]
