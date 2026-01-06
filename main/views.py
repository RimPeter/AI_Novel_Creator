import json
import re

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from django.views.decorators.http import require_POST
from django.db.models import Max, Q

from .forms import CharacterForm, NovelProjectForm, OutlineChapterForm, OutlineSceneForm, StoryBibleForm
from .models import Character, NovelProject, OutlineNode, StoryBible
from .tasks import generate_all_scenes, generate_bible, generate_outline
from .chapter_tools import parse_structure_json, render_from_structure, structurize_chapter
from .llm import call_llm


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


@require_POST
def move_scene(request, slug):
    project = get_object_or_404(NovelProject, slug=slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )

    scene_id = request.POST.get("scene_id")
    target_chapter_id = request.POST.get("target_chapter_id")
    before_scene_id = request.POST.get("before_scene_id") or None

    if not scene_id or not target_chapter_id:
        if wants_json:
            return JsonResponse({"ok": False, "error": "Missing scene or target chapter."}, status=400)
        messages.error(request, "Missing scene or target chapter.")
        return HttpResponseRedirect(reverse("project-dashboard", kwargs={"slug": project.slug}))

    scene = get_object_or_404(
        OutlineNode,
        id=scene_id,
        project=project,
        node_type=OutlineNode.NodeType.SCENE,
    )
    target_chapter = get_object_or_404(
        OutlineNode,
        id=target_chapter_id,
        project=project,
        node_type=OutlineNode.NodeType.CHAPTER,
    )

    before_scene = None
    if before_scene_id:
        before_scene = get_object_or_404(
            OutlineNode,
            id=before_scene_id,
            project=project,
            node_type=OutlineNode.NodeType.SCENE,
        )
        if before_scene.parent_id != target_chapter.id:
            if wants_json:
                return JsonResponse({"ok": False, "error": "Invalid drop target."}, status=400)
            messages.error(request, "Invalid drop target.")
            return HttpResponseRedirect(reverse("project-dashboard", kwargs={"slug": project.slug}))
        if before_scene.id == scene.id:
            before_scene = None

    source_chapter_id = scene.parent_id

    with transaction.atomic():
        target_ids = list(
            OutlineNode.objects.filter(
                project=project,
                node_type=OutlineNode.NodeType.SCENE,
                parent=target_chapter,
            )
            .order_by("order", "created_at", "id")
            .values_list("id", flat=True)
        )
        target_ids = [sid for sid in target_ids if sid != scene.id]

        if before_scene is not None:
            try:
                insert_at = target_ids.index(before_scene.id)
            except ValueError:
                insert_at = len(target_ids)
            target_ids.insert(insert_at, scene.id)
        else:
            target_ids.append(scene.id)

        target_objs = {obj.id: obj for obj in OutlineNode.objects.filter(id__in=target_ids)}
        target_updates = []
        for order, sid in enumerate(target_ids, start=1):
            obj = target_objs[sid]
            changed = False
            if obj.parent_id != target_chapter.id:
                obj.parent_id = target_chapter.id
                changed = True
            if obj.order != order:
                obj.order = order
                changed = True
            if changed:
                target_updates.append(obj)
        if target_updates:
            OutlineNode.objects.bulk_update(target_updates, ["parent", "order"])

        if source_chapter_id and source_chapter_id != target_chapter.id:
            source_ids = list(
                OutlineNode.objects.filter(
                    project=project,
                    node_type=OutlineNode.NodeType.SCENE,
                    parent_id=source_chapter_id,
                )
                .order_by("order", "created_at", "id")
                .values_list("id", flat=True)
            )
            source_ids = [sid for sid in source_ids if sid != scene.id]
            if source_ids:
                source_objs = {obj.id: obj for obj in OutlineNode.objects.filter(id__in=source_ids)}
                source_updates = []
                for order, sid in enumerate(source_ids, start=1):
                    obj = source_objs[sid]
                    if obj.order != order:
                        obj.order = order
                        source_updates.append(obj)
                if source_updates:
                    OutlineNode.objects.bulk_update(source_updates, ["order"])

        _renumber_outline_for_project(project)

    if wants_json:
        return JsonResponse({"ok": True})

    messages.success(request, "Moved scene.")
    return HttpResponseRedirect(reverse("project-dashboard", kwargs={"slug": project.slug}))


@require_POST
def brainstorm_character(request, slug):
    project = get_object_or_404(NovelProject, slug=slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
        "name",
        "role",
        "age",
        "gender",
        "personality",
        "appearance",
        "background",
        "goals",
        "voice_notes",
        "description",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    empty_fields = [k for k in allowed_fields if not current.get(k)]
    if not empty_fields:
        return JsonResponse({"ok": True, "suggestions": {}})

    prompt = "\n".join(
        [
            "You are a novelist's character assistant.",
            "Goal: fill in ONLY the currently-empty fields with plausible details that complement the already-filled fields.",
            "Rules:",
            "- Return STRICT JSON only (no markdown, no extra text).",
            "- Output an object with only keys from: " + ", ".join(allowed_fields),
            "- Only include keys that are empty right now: " + ", ".join(empty_fields),
            "- Keep answers concise but useful.",
            "- 'age' must be an integer (omit it if unsure).",
            "",
            "Existing character fields (may be blank):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 500},
        )
        raw = (result.text or "").strip()
        suggestions = json.loads(raw) if raw else {}
        if not isinstance(suggestions, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in suggestions.items():
            if key not in allowed_fields or key not in empty_fields:
                continue
            if value is None:
                continue
            if key == "age":
                try:
                    age_int = int(value)
                except Exception:
                    continue
                if age_int < 0 or age_int > 130:
                    continue
                filtered[key] = age_int
            else:
                text = str(value).strip()
                if text:
                    filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


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


class CharacterListView(ListView):
    model = Character
    template_name = "main/character_list.html"
    context_object_name = "characters"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = Character.objects.filter(project=self.project).order_by("name")
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(role__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        return ctx


class CharacterCreateView(CreateView):
    model = Character
    form_class = CharacterForm
    template_name = "main/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        response = super().form_valid(form)
        messages.success(self.request, "Character created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse_lazy("character-list", kwargs={"slug": self.project.slug})


class CharacterUpdateView(UpdateView):
    form_class = CharacterForm
    template_name = "main/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Character.objects.filter(project=self.project)

    def form_valid(self, form):
        messages.success(self.request, "Character saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse_lazy("character-list", kwargs={"slug": self.project.slug})


class CharacterDeleteView(DeleteView):
    template_name = "main/character_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(NovelProject, slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Character.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse_lazy("character-list", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        messages.success(self.request, "Character deleted.")
        return super().form_valid(form)


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

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action") or ""

        if self.object.node_type == OutlineNode.NodeType.CHAPTER and action in {"structurize", "render"}:
            if action == "structurize":
                data = request.POST.copy()
                data.pop("structure_json", None)
                data.pop("rendered_text", None)
                form = self.get_form_class()(data=data, instance=self.object)
            else:
                form = self.get_form()
            if not form.is_valid():
                return self.form_invalid(form)

            chapter = form.save(commit=False)
            if action == "structurize":
                summary = (chapter.summary or "").strip()
                if not summary:
                    form.add_error("summary", "Add a chapter summary first.")
                    return self.form_invalid(form)
                chapter.structure_json = structurize_chapter(
                    chapter_title=chapter.title,
                    chapter_summary=summary,
                )
                chapter.save()
                messages.success(request, "Structurized chapter summary into JSON.")
            else:
                try:
                    structure = parse_structure_json(chapter.structure_json)
                except Exception as e:
                    form.add_error("structure_json", str(e))
                    return self.form_invalid(form)
                prompt = "\n".join(
                    [
                        "Write the next chapter content as polished novel prose (no bullet points, no JSON, no markdown headings).",
                        "Use scene breaks with a blank line, then `***`, then a blank line between scenes.",
                        "Keep it grounded in the provided structure; preserve POV/location notes when present.",
                        "Avoid meta commentary and avoid explaining what you are doing.",
                        "",
                        "Chapter structure (JSON):",
                        chapter.structure_json.strip(),
                    ]
                ).strip()

                try:
                    result = call_llm(
                        prompt=prompt,
                        model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                        params={
                            "temperature": 0.8,
                            "max_tokens": 1800,
                        },
                    )
                    chapter.rendered_text = (result.text or "").strip() + "\n"
                    chapter.save()
                    messages.success(request, "Rendered novel prose from structure JSON.")
                except Exception:
                    chapter.rendered_text = render_from_structure(structure)
                    chapter.save()
                    messages.warning(
                        request,
                        "OpenAI render failed; saved a local placeholder draft instead. Check your model/API settings and try again.",
                    )

            return HttpResponseRedirect(
                reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": chapter.id})
            )

        return super().post(request, *args, **kwargs)


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
