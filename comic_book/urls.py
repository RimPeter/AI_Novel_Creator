from django.urls import path

from . import views

app_name = "comic_book"

urlpatterns = [
    path("", views.ComicBookHomeView.as_view(), name="index"),
    path("projects/new/", views.ComicProjectCreateView.as_view(), name="project-create"),
    path("projects/<slug:slug>/", views.ComicProjectDashboardView.as_view(), name="project-dashboard"),
    path("projects/<slug:slug>/edit/", views.ComicProjectUpdateView.as_view(), name="project-edit"),
    path("projects/<slug:slug>/delete/", views.ComicProjectDeleteView.as_view(), name="project-delete"),
    path("projects/<slug:slug>/bible/", views.ComicBibleUpdateView.as_view(), name="bible-edit"),
    path("projects/<slug:slug>/characters/", views.ComicCharacterListView.as_view(), name="character-list"),
    path("projects/<slug:slug>/characters/new/", views.ComicCharacterCreateView.as_view(), name="character-create"),
    path("projects/<slug:slug>/characters/<uuid:pk>/edit/", views.ComicCharacterUpdateView.as_view(), name="character-edit"),
    path("projects/<slug:slug>/characters/<uuid:pk>/delete/", views.ComicCharacterDeleteView.as_view(), name="character-delete"),
    path("projects/<slug:slug>/locations/", views.ComicLocationListView.as_view(), name="location-list"),
    path("projects/<slug:slug>/locations/new/", views.ComicLocationCreateView.as_view(), name="location-create"),
    path("projects/<slug:slug>/locations/<uuid:pk>/edit/", views.ComicLocationUpdateView.as_view(), name="location-edit"),
    path("projects/<slug:slug>/locations/<uuid:pk>/delete/", views.ComicLocationDeleteView.as_view(), name="location-delete"),
    path("projects/<slug:slug>/issues/brainstorm/", views.brainstorm_issue, name="issue-brainstorm"),
    path("projects/<slug:slug>/issues/add-details/", views.add_issue_details, name="issue-add-details"),
    path("projects/<slug:slug>/issues/new/", views.ComicIssueCreateView.as_view(), name="issue-create"),
    path("projects/<slug:slug>/issues/<uuid:pk>/", views.ComicIssueWorkspaceView.as_view(), name="issue-workspace"),
    path("projects/<slug:slug>/issues/<uuid:pk>/edit/", views.ComicIssueUpdateView.as_view(), name="issue-edit"),
    path("projects/<slug:slug>/issues/<uuid:pk>/delete/", views.ComicIssueDeleteView.as_view(), name="issue-delete"),
    path("projects/<slug:slug>/issues/<uuid:pk>/export/", views.ComicIssueExportView.as_view(), name="issue-export"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/new/", views.ComicPageCreateView.as_view(), name="page-create"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:pk>/brainstorm/", views.brainstorm_page, name="page-brainstorm"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:pk>/add-details/", views.add_page_details, name="page-add-details"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:pk>/edit/", views.ComicPageUpdateView.as_view(), name="page-edit"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:pk>/delete/", views.ComicPageDeleteView.as_view(), name="page-delete"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:pk>/shift/", views.shift_page, name="page-shift"),
    path(
        "projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:page_pk>/canvas/<str:canvas_key>/edit/",
        views.ComicCanvasNodeUpdateView.as_view(),
        name="canvas-node-edit",
    ),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:page_pk>/panels/new/", views.ComicPanelCreateView.as_view(), name="panel-create"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:page_pk>/panels/<uuid:pk>/edit/", views.ComicPanelUpdateView.as_view(), name="panel-edit"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:page_pk>/panels/<uuid:pk>/delete/", views.ComicPanelDeleteView.as_view(), name="panel-delete"),
    path("projects/<slug:slug>/issues/<uuid:issue_pk>/pages/<uuid:page_pk>/panels/<uuid:pk>/shift/", views.shift_panel, name="panel-shift"),
]
