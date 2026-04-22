from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Max, Prefetch
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import ComicBibleForm, ComicCharacterForm, ComicIssueForm, ComicLocationForm, ComicPageForm, ComicPanelForm, ComicProjectForm
from .models import ComicBible, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


def _project_queryset_for_user(user):
    if getattr(user, "is_superuser", False):
        return ComicProject.objects.all()
    return ComicProject.objects.filter(owner=user)


def _get_project_for_user(request, slug: str) -> ComicProject:
    return get_object_or_404(_project_queryset_for_user(request.user), slug=slug)


def _get_issue_for_project(project: ComicProject, issue_id) -> ComicIssue:
    return get_object_or_404(ComicIssue.objects.filter(project=project), pk=issue_id)


def _get_page_for_issue(issue: ComicIssue, page_id) -> ComicPage:
    return get_object_or_404(ComicPage.objects.filter(issue=issue), pk=page_id)


def _issue_workspace_url(issue: ComicIssue, *, page: ComicPage | None = None) -> str:
    url = reverse("comic_book:issue-workspace", kwargs={"slug": issue.project.slug, "pk": issue.pk})
    if page is not None:
        url += "?" + urlencode({"page": str(page.pk)})
    return url


def _renumber_issue_pages(issue: ComicIssue) -> None:
    for index, page in enumerate(issue.pages.order_by("page_number", "created_at", "id"), start=1):
        if page.page_number != index:
            page.page_number = index
            page.save(update_fields=["page_number", "updated_at"])


def _renumber_page_panels(page: ComicPage) -> None:
    for index, panel in enumerate(page.panels.order_by("panel_number", "created_at", "id"), start=1):
        if panel.panel_number != index:
            panel.panel_number = index
            panel.save(update_fields=["panel_number", "updated_at"])


def _seed_issue_pages(issue: ComicIssue) -> int:
    if issue.pages.exists():
        return 0

    page_count = max(int(issue.planned_page_count or 0), 0)
    if page_count <= 0:
        return 0

    ComicPage.objects.bulk_create(
        [
            ComicPage(
                issue=issue,
                page_number=page_number,
            )
            for page_number in range(1, page_count + 1)
        ]
    )
    return page_count


class ComicBookHomeView(LoginRequiredMixin, ListView):
    model = ComicProject
    template_name = "comic_book/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        return (
            _project_queryset_for_user(self.request.user)
            .annotate(
                issue_count=Count("issues", distinct=True),
                character_count=Count("characters", distinct=True),
                location_count=Count("locations", distinct=True),
            )
            .order_by("title")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        projects = ctx["projects"]
        ctx["project_count"] = projects.count()
        ctx["recently_updated_count"] = projects.filter(updated_at__gte=timezone.now() - timedelta(days=7)).count()
        ctx["issue_total"] = sum(project.issue_count for project in projects)
        return ctx


class ComicProjectCreateView(LoginRequiredMixin, CreateView):
    model = ComicProject
    form_class = ComicProjectForm
    template_name = "comic_book/project_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, "Comic project created.")
        return response

    def get_success_url(self):
        return reverse_lazy("comic_book:project-dashboard", kwargs={"slug": self.object.slug})


class ComicProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = ComicProject
    form_class = ComicProjectForm
    template_name = "comic_book/project_form.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return _project_queryset_for_user(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Comic project saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("comic_book:project-dashboard", kwargs={"slug": self.object.slug})


class ComicProjectDeleteView(LoginRequiredMixin, DeleteView):
    model = ComicProject
    template_name = "comic_book/confirm_delete.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return _project_queryset_for_user(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["object_kind"] = "comic project"
        ctx["cancel_url"] = reverse("comic_book:project-dashboard", kwargs={"slug": self.object.slug})
        return ctx

    def get_success_url(self):
        return reverse_lazy("comic_book:index")

    def form_valid(self, form):
        messages.success(self.request, "Comic project deleted.")
        return super().form_valid(form)


class ComicProjectDashboardView(LoginRequiredMixin, DetailView):
    model = ComicProject
    template_name = "comic_book/project_dashboard.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    context_object_name = "project"

    def get_queryset(self):
        return _project_queryset_for_user(self.request.user).prefetch_related(
            Prefetch(
                "issues",
                queryset=ComicIssue.objects.order_by("number").prefetch_related(
                    Prefetch(
                        "pages",
                        queryset=ComicPage.objects.order_by("page_number").prefetch_related("panels"),
                    )
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.object
        issues = list(project.issues.all())
        panel_total = 0
        page_total = 0
        dialogue_total = 0
        for issue in issues:
            issue_pages = list(issue.pages.all())
            issue.panel_total = 0
            page_total += len(issue_pages)
            for page in issue_pages:
                page_panels = list(page.panels.all())
                issue.panel_total += len(page_panels)
                panel_total += len(page_panels)
                dialogue_total += sum(panel.balloon_word_count for panel in page_panels)

        try:
            ctx["bible"] = project.bible
        except ComicBible.DoesNotExist:
            ctx["bible"] = None

        ctx["issues"] = issues
        ctx["issue_count"] = len(issues)
        ctx["page_total"] = page_total
        ctx["panel_total"] = panel_total
        ctx["dialogue_total"] = dialogue_total
        ctx["character_count"] = project.characters.count()
        ctx["location_count"] = project.locations.count()
        return ctx


class ComicBibleUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicBibleForm
    template_name = "comic_book/bible_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        bible, _created = ComicBible.objects.get_or_create(project=self.project)
        return bible

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Comic bible saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("comic_book:bible-edit", kwargs={"slug": self.project.slug})


class ComicCharacterListView(LoginRequiredMixin, ListView):
    model = ComicCharacter
    template_name = "comic_book/character_list.html"
    context_object_name = "characters"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = ComicCharacter.objects.filter(project=self.project).order_by("name")
        query = (self.request.GET.get("q") or "").strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        return ctx


class ComicCharacterCreateView(LoginRequiredMixin, CreateView):
    model = ComicCharacter
    form_class = ComicCharacterForm
    template_name = "comic_book/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        messages.success(self.request, "Comic character created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:character-list", kwargs={"slug": self.project.slug})


class ComicCharacterUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicCharacterForm
    template_name = "comic_book/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicCharacter.objects.filter(project=self.project)

    def form_valid(self, form):
        messages.success(self.request, "Comic character saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:character-list", kwargs={"slug": self.project.slug})


class ComicCharacterDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicCharacter.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_kind"] = "comic character"
        ctx["cancel_url"] = reverse("comic_book:character-list", kwargs={"slug": self.project.slug})
        return ctx

    def get_success_url(self):
        return reverse("comic_book:character-list", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        messages.success(self.request, "Comic character deleted.")
        return super().form_valid(form)


class ComicLocationListView(LoginRequiredMixin, ListView):
    model = ComicLocation
    template_name = "comic_book/location_list.html"
    context_object_name = "locations"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = ComicLocation.objects.filter(project=self.project).order_by("name")
        query = (self.request.GET.get("q") or "").strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        return ctx


class ComicLocationCreateView(LoginRequiredMixin, CreateView):
    model = ComicLocation
    form_class = ComicLocationForm
    template_name = "comic_book/location_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        messages.success(self.request, "Comic location created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:location-list", kwargs={"slug": self.project.slug})


class ComicLocationUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicLocationForm
    template_name = "comic_book/location_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicLocation.objects.filter(project=self.project)

    def form_valid(self, form):
        messages.success(self.request, "Comic location saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:location-list", kwargs={"slug": self.project.slug})


class ComicLocationDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicLocation.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_kind"] = "comic location"
        ctx["cancel_url"] = reverse("comic_book:location-list", kwargs={"slug": self.project.slug})
        return ctx

    def get_success_url(self):
        return reverse("comic_book:location-list", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        messages.success(self.request, "Comic location deleted.")
        return super().form_valid(form)


class ComicIssueCreateView(LoginRequiredMixin, CreateView):
    model = ComicIssue
    form_class = ComicIssueForm
    template_name = "comic_book/issue_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        next_number = self.project.issues.aggregate(max_number=Max("number"))["max_number"] or 0
        initial.setdefault("number", next_number + 1)
        initial.setdefault("planned_page_count", 22)
        return initial

    def form_valid(self, form):
        form.instance.project = self.project
        with transaction.atomic():
            response = super().form_valid(form)
            page_count = _seed_issue_pages(self.object)
        if page_count:
            messages.success(self.request, f"Issue created and seeded with {page_count} pages.")
        else:
            messages.success(self.request, "Issue created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:issue-workspace", kwargs={"slug": self.project.slug, "pk": self.object.pk})


class ComicIssueUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicIssueForm
    template_name = "comic_book/issue_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicIssue.objects.filter(project=self.project)

    def form_valid(self, form):
        messages.success(self.request, "Issue saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse("comic_book:issue-workspace", kwargs={"slug": self.project.slug, "pk": self.object.pk})


class ComicIssueDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicIssue.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_kind"] = "issue"
        ctx["cancel_url"] = reverse("comic_book:project-dashboard", kwargs={"slug": self.project.slug})
        return ctx

    def get_success_url(self):
        return reverse("comic_book:project-dashboard", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        messages.success(self.request, "Issue deleted.")
        return super().form_valid(form)


class ComicIssueWorkspaceView(LoginRequiredMixin, DetailView):
    model = ComicIssue
    template_name = "comic_book/issue_workspace.html"
    context_object_name = "issue"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicIssue.objects.filter(project=self.project).prefetch_related(
            Prefetch(
                "pages",
                queryset=ComicPage.objects.order_by("page_number").prefetch_related(
                    Prefetch(
                        "panels",
                        queryset=ComicPanel.objects.order_by("panel_number").prefetch_related("characters", "location"),
                    )
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        issue = self.object
        pages = list(issue.pages.all())
        selected_page = None
        selected_page_id = (self.request.GET.get("page") or "").strip()
        if selected_page_id:
            selected_page = next((page for page in pages if str(page.pk) == selected_page_id), None)
        if selected_page is None and pages:
            selected_page = pages[0]

        ctx["project"] = self.project
        ctx["issues"] = self.project.issues.order_by("number")
        ctx["pages"] = pages
        ctx["selected_page"] = selected_page
        ctx["selected_panels"] = list(selected_page.panels.all()) if selected_page is not None else []
        ctx["character_count"] = self.project.characters.count()
        ctx["location_count"] = self.project.locations.count()
        ctx["project_characters"] = self.project.characters.order_by("name")[:8]
        ctx["project_locations"] = self.project.locations.order_by("name")[:8]
        ctx["page_count"] = len(pages)
        ctx["panel_total"] = sum(len(page.panels.all()) for page in pages)
        return ctx


class ComicIssueExportView(LoginRequiredMixin, DetailView):
    model = ComicIssue
    template_name = "comic_book/issue_export.html"
    context_object_name = "issue"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicIssue.objects.filter(project=self.project).prefetch_related(
            Prefetch(
                "pages",
                queryset=ComicPage.objects.order_by("page_number").prefetch_related(
                    Prefetch(
                        "panels",
                        queryset=ComicPanel.objects.order_by("panel_number").prefetch_related("characters", "location"),
                    )
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx


class ComicPageCreateView(LoginRequiredMixin, CreateView):
    model = ComicPage
    form_class = ComicPageForm
    template_name = "comic_book/page_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("page_number", self.issue.pages.count() + 1)
        return initial

    def form_valid(self, form):
        form.instance.issue = self.issue
        response = super().form_valid(form)
        _renumber_issue_pages(self.issue)
        messages.success(self.request, "Page created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        return ctx

    def get_success_url(self):
        return _issue_workspace_url(self.issue, page=self.object)


class ComicPageUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicPageForm
    template_name = "comic_book/page_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicPage.objects.filter(issue=self.issue)

    def form_valid(self, form):
        response = super().form_valid(form)
        _renumber_issue_pages(self.issue)
        messages.success(self.request, "Page saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        return ctx

    def get_success_url(self):
        return _issue_workspace_url(self.issue, page=self.object)


class ComicPageDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicPage.objects.filter(issue=self.issue)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["object_kind"] = "page"
        ctx["cancel_url"] = _issue_workspace_url(self.issue, page=self.object)
        return ctx

    def get_success_url(self):
        return reverse("comic_book:issue-workspace", kwargs={"slug": self.project.slug, "pk": self.issue.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        _renumber_issue_pages(self.issue)
        messages.success(self.request, "Page deleted.")
        return response


class ComicPanelCreateView(LoginRequiredMixin, CreateView):
    model = ComicPanel
    form_class = ComicPanelForm
    template_name = "comic_book/panel_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        self.page = _get_page_for_issue(self.issue, kwargs["page_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("panel_number", self.page.panels.count() + 1)
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        form.instance.page = self.page
        response = super().form_valid(form)
        _renumber_page_panels(self.page)
        messages.success(self.request, "Panel created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["page"] = self.page
        return ctx

    def get_success_url(self):
        return _issue_workspace_url(self.issue, page=self.page)


class ComicPanelUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicPanelForm
    template_name = "comic_book/panel_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        self.page = _get_page_for_issue(self.issue, kwargs["page_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicPanel.objects.filter(page=self.page)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        _renumber_page_panels(self.page)
        messages.success(self.request, "Panel saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["page"] = self.page
        return ctx

    def get_success_url(self):
        return _issue_workspace_url(self.issue, page=self.page)


class ComicPanelDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        self.page = _get_page_for_issue(self.issue, kwargs["page_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicPanel.objects.filter(page=self.page)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["page"] = self.page
        ctx["object_kind"] = "panel"
        ctx["cancel_url"] = _issue_workspace_url(self.issue, page=self.page)
        return ctx

    def get_success_url(self):
        return _issue_workspace_url(self.issue, page=self.page)

    def form_valid(self, form):
        response = super().form_valid(form)
        _renumber_page_panels(self.page)
        messages.success(self.request, "Panel deleted.")
        return response


@login_required
@require_POST
def shift_page(request, slug: str, issue_pk, pk):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, pk)
    direction = (request.POST.get("direction") or "").strip().lower()
    pages = list(issue.pages.order_by("page_number", "created_at", "id"))
    page_ids = [item.pk for item in pages]
    try:
        current_index = page_ids.index(page.pk)
    except ValueError as exc:
        raise Http404("Page not found.") from exc

    if direction == "up":
        swap_index = current_index - 1
    elif direction == "down":
        swap_index = current_index + 1
    else:
        messages.error(request, "Invalid page move direction.")
        return HttpResponseRedirect(_issue_workspace_url(issue, page=page))

    if swap_index < 0 or swap_index >= len(pages):
        messages.warning(request, "Page is already at the edge of the issue.")
        return HttpResponseRedirect(_issue_workspace_url(issue, page=page))

    other_page = pages[swap_index]
    current_number = page.page_number
    page.page_number = other_page.page_number
    other_page.page_number = current_number
    with transaction.atomic():
        page.save(update_fields=["page_number", "updated_at"])
        other_page.save(update_fields=["page_number", "updated_at"])
        _renumber_issue_pages(issue)
    messages.success(request, "Page order updated.")
    return HttpResponseRedirect(_issue_workspace_url(issue, page=page))


@login_required
@require_POST
def shift_panel(request, slug: str, issue_pk, page_pk, pk):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    panel = get_object_or_404(ComicPanel.objects.filter(page=page), pk=pk)
    direction = (request.POST.get("direction") or "").strip().lower()
    panels = list(page.panels.order_by("panel_number", "created_at", "id"))
    panel_ids = [item.pk for item in panels]
    try:
        current_index = panel_ids.index(panel.pk)
    except ValueError as exc:
        raise Http404("Panel not found.") from exc

    if direction == "up":
        swap_index = current_index - 1
    elif direction == "down":
        swap_index = current_index + 1
    else:
        messages.error(request, "Invalid panel move direction.")
        return HttpResponseRedirect(_issue_workspace_url(issue, page=page))

    if swap_index < 0 or swap_index >= len(panels):
        messages.warning(request, "Panel is already at the edge of the page.")
        return HttpResponseRedirect(_issue_workspace_url(issue, page=page))

    other_panel = panels[swap_index]
    current_number = panel.panel_number
    panel.panel_number = other_panel.panel_number
    other_panel.panel_number = current_number
    with transaction.atomic():
        panel.save(update_fields=["panel_number", "updated_at"])
        other_panel.save(update_fields=["panel_number", "updated_at"])
        _renumber_page_panels(page)
    messages.success(request, "Panel order updated.")
    return HttpResponseRedirect(_issue_workspace_url(issue, page=page))
