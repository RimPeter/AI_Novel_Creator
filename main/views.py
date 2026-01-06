import re

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from django.db.models import Max

from .forms import NovelProjectForm, OutlineChapterForm, OutlineSceneForm, StoryBibleForm
from .models import NovelProject, OutlineNode, StoryBible
from .tasks import generate_all_scenes, generate_bible, generate_outline


def _renumber_outline_for_project(project: NovelProject) -> None:
    acts = list(
        OutlineNode.objects.filter(project=project, node_type=OutlineNode.NodeType.ACT).order_by(
            "order", "created_at", "id"
        )
    )

    chapter_number = 0
    scene_number = 0

    acts_to_update = []
    chapters_to_update = []
    scenes_to_update = []

    for act_idx, act in enumerate(acts, start=1):
        if act.order != act_idx:
            act.order = act_idx
            acts_to_update.append(act)

        chapters = list(
            OutlineNode.objects.filter(project=project, parent=act, node_type=OutlineNode.NodeType.CHAPTER).order_by(
                "order", "created_at", "id"
            )
        )

        for ch_idx, chapter in enumerate(chapters, start=1):
            if chapter.order != ch_idx:
                chapter.order = ch_idx
                chapters_to_update.append(chapter)

            chapter_number += 1
            current_title = (chapter.title or "").strip()
            if re.fullmatch(r"Chapter \d+", current_title):
                desired = f"Chapter {chapter_number}"
                if current_title != desired:
                    chapter.title = desired
                    if chapter not in chapters_to_update:
                        chapters_to_update.append(chapter)

            scenes = list(
                OutlineNode.objects.filter(project=project, parent=chapter, node_type=OutlineNode.NodeType.SCENE).order_by(
                    "order", "created_at", "id"
                )
            )

            for sc_idx, scene in enumerate(scenes, start=1):
                if scene.order != sc_idx:
                    scene.order = sc_idx
                    scenes_to_update.append(scene)

                scene_number += 1
                current_scene_title = (scene.title or "").strip()
                if re.fullmatch(r"Scene \d+", current_scene_title):
                    desired = f"Scene {scene_number}"
                    if current_scene_title != desired:
                        scene.title = desired
                        if scene not in scenes_to_update:
                            scenes_to_update.append(scene)

    if acts_to_update:
        OutlineNode.objects.bulk_update(acts_to_update, ["order"])
    if chapters_to_update:
        OutlineNode.objects.bulk_update(chapters_to_update, ["order", "title"])
    if scenes_to_update:
        OutlineNode.objects.bulk_update(scenes_to_update, ["order", "title"])


def home(request):
    return render(request, "main/base.html")


class ProjectListView(ListView):
    model = NovelProject
    template_name = "main/project_list.html"
    context_object_name = "projects"
    ordering = ["title"]


class ProjectDetailView(DetailView):
    model = NovelProject
    template_name = "main/project_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"


class ProjectCreateView(CreateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})


class ProjectUpdateView(UpdateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})


class ProjectDashboardView(DetailView):
    model = NovelProject
    template_name = "main/project_dashboard.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    context_object_name = "project"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.object

        try:
            ctx["bible"] = project.bible
        except StoryBible.DoesNotExist:
            ctx["bible"] = None

        ctx["recent_runs"] = project.runs.order_by("-created_at")[:10]

        # Build outline tree in one query, then group in Python
        nodes = (
            OutlineNode.objects
            .filter(project=project)
            .only("id", "node_type", "parent_id", "order", "title", "pov", "location", "created_at")
            .order_by("parent_id", "order", "created_at")
        )

        acts = []
        chapters_by_act = {}
        scenes_by_chapter = {}

        for n in nodes:
            if n.node_type == OutlineNode.NodeType.ACT:
                acts.append(n)
                chapters_by_act[n.id] = []
            elif n.node_type == OutlineNode.NodeType.CHAPTER and n.parent_id:
                chapters_by_act.setdefault(n.parent_id, []).append(n)
                scenes_by_chapter[n.id] = []
            elif n.node_type == OutlineNode.NodeType.SCENE and n.parent_id:
                scenes_by_chapter.setdefault(n.parent_id, []).append(n)

        ctx["acts"] = acts
        ctx["chapters_by_act"] = chapters_by_act
        ctx["scenes_by_chapter"] = scenes_by_chapter

        outline_tree = []
        for act in acts:
            chapters = []
            for chapter in chapters_by_act.get(act.id, []):
                chapters.append(
                    {
                        "chapter": chapter,
                        "scenes": scenes_by_chapter.get(chapter.id, []),
                    }
                )
            outline_tree.append({"act": act, "chapters": chapters})

        ctx["outline_tree"] = outline_tree
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        project = self.object
        action = request.POST.get("action", "")

        if action == "generate_bible":
            generate_bible.delay(str(project.id))
            messages.success(request, "Queued: Generate Bible.")
        elif action == "generate_outline":
            generate_outline.delay(str(project.id))
            messages.success(request, "Queued: Generate Outline.")
        elif action == "generate_all_scenes":
            generate_all_scenes.delay(str(project.id))
            messages.success(request, "Queued: Generate All Scenes.")
        else:
            messages.error(request, "Unknown action.")

        return HttpResponseRedirect(reverse("project-dashboard", kwargs={"slug": project.slug}))


class StoryBibleUpdateView(UpdateView):
    form_class = StoryBibleForm
    template_name = "main/bible_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        bible, _created = StoryBible.objects.get_or_create(project=self.project)
        return bible

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Story bible saved.")
        return response

    def get_success_url(self):
        return reverse_lazy("project-dashboard", kwargs={"slug": self.project.slug})


class OutlineChapterCreateView(CreateView):
    form_class = OutlineChapterForm
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        self.act = get_object_or_404(
            OutlineNode,
            id=kwargs["act_id"],
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        max_order = (
            OutlineNode.objects.filter(project=self.project, parent=self.act, node_type=OutlineNode.NodeType.CHAPTER)
            .aggregate(Max("order"))
            .get("order__max")
            or 0
        )
        initial.setdefault("order", max_order + 1)
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["parent_node"] = self.act
        ctx["node_kind"] = "chapter"
        return ctx

    def form_valid(self, form):
        form.instance.project = self.project
        form.instance.parent = self.act
        form.instance.node_type = OutlineNode.NodeType.CHAPTER
        response = super().form_valid(form)
        messages.success(self.request, "Chapter added.")
        return response

    def get_success_url(self):
        return reverse_lazy("project-dashboard", kwargs={"slug": self.project.slug})


class OutlineSceneCreateView(CreateView):
    form_class = OutlineSceneForm
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        self.chapter = get_object_or_404(
            OutlineNode,
            id=kwargs["chapter_id"],
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        max_order = (
            OutlineNode.objects.filter(project=self.project, parent=self.chapter, node_type=OutlineNode.NodeType.SCENE)
            .aggregate(Max("order"))
            .get("order__max")
            or 0
        )
        initial.setdefault("order", max_order + 1)
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["parent_node"] = self.chapter
        ctx["node_kind"] = "scene"
        return ctx

    def form_valid(self, form):
        form.instance.project = self.project
        form.instance.parent = self.chapter
        form.instance.node_type = OutlineNode.NodeType.SCENE
        response = super().form_valid(form)
        messages.success(self.request, "Scene added.")
        return response

    def get_success_url(self):
        return reverse_lazy("project-dashboard", kwargs={"slug": self.project.slug})


class OutlineNodeUpdateView(UpdateView):
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return OutlineNode.objects.filter(
            project=self.project,
            node_type__in=[OutlineNode.NodeType.CHAPTER, OutlineNode.NodeType.SCENE],
        ).select_related("parent")

    def get_form_class(self):
        obj = self.get_object()
        if obj.node_type == OutlineNode.NodeType.CHAPTER:
            return OutlineChapterForm
        return OutlineSceneForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.object
        ctx["project"] = self.project
        ctx["parent_node"] = obj.parent
        ctx["node_kind"] = "chapter" if obj.node_type == OutlineNode.NodeType.CHAPTER else "scene"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("project-dashboard", kwargs={"slug": self.project.slug})


class OutlineNodeDeleteView(DeleteView):
    template_name = "main/outline_node_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return OutlineNode.objects.filter(
            project=self.project,
            node_type__in=[OutlineNode.NodeType.CHAPTER, OutlineNode.NodeType.SCENE],
        ).select_related("parent")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["parent_node"] = self.object.parent
        ctx["node_kind"] = "chapter" if self.object.node_type == OutlineNode.NodeType.CHAPTER else "scene"
        return ctx

    def get_success_url(self):
        return reverse_lazy("project-dashboard", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        self.object = self.get_object()
        with transaction.atomic():
            self.object.delete()
            _renumber_outline_for_project(self.project)

        messages.success(self.request, "Deleted and renumbered.")
        return HttpResponseRedirect(self.get_success_url())
