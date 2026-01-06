from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import NovelProjectForm
from .models import NovelProject, OutlineNode
from .tasks import generate_all_scenes, generate_bible, generate_outline

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
