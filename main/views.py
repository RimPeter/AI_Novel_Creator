import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from django.views.decorators.http import require_POST
from django.db.models import Max, Q

from .forms import CharacterForm, LocationForm, NovelProjectForm, OutlineChapterForm, OutlineSceneForm, StoryBibleForm
from .models import Character, Location, NovelProject, OutlineNode, StoryBible
from .tasks import generate_all_scenes, generate_bible, generate_outline
from .chapter_tools import parse_scene_structure_json, render_scene_from_structure, structurize_scene
from .llm import call_llm


def _get_project_for_user(request, slug: str) -> NovelProject:
    return get_object_or_404(NovelProject, slug=slug, owner=request.user)


def _get_scene_for_user(request, slug: str, pk) -> OutlineNode:
    project = _get_project_for_user(request, slug)
    qs = OutlineNode.objects.select_related("parent")
    return get_object_or_404(
        qs,
        id=pk,
        project=project,
        node_type=OutlineNode.NodeType.SCENE,
    )


def _get_story_bible_context(project: NovelProject) -> list[str]:
    try:
        bible = project.bible
    except StoryBible.DoesNotExist:
        return []

    lines = []
    summary = (bible.summary_md or "").strip()
    if summary:
        lines.append("Story bible summary: " + summary)

    constraints = bible.constraints or []
    if constraints:
        lines.append("Story bible constraints (JSON): " + json.dumps(constraints, ensure_ascii=False))

    facts = bible.facts or {}
    if facts:
        lines.append("Story bible facts (JSON): " + json.dumps(facts, ensure_ascii=False))

    if not lines:
        return []

    return ["Story bible context:"] + lines


def _add_query_params(url: str, **params) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in params.items():
        if v is None:
            continue
        query[k] = str(v)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


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
@login_required
def brainstorm_project(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
        "seed_idea",
        "genre",
        "tone",
        "style_notes",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    rejected = {k for k in allowed_fields if request.POST.get(f"reject_{k}")}
    if rejected:
        for k in rejected:
            current[k] = ""
    empty_fields = [k for k in allowed_fields if not current.get(k)]
    if not empty_fields:
        return JsonResponse({"ok": True, "suggestions": {}})

    prompt_lines = [
        "You are a novelist's project brainstorming assistant.",
        "Goal: fill in ONLY the currently-empty fields with strong, coherent ideas.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- Keep genre/tone short (a few words).",
        "- For seed_idea/style_notes, write concise prose (no bullet points).",
        "",
        "Project title: " + (project.title or ""),
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "Existing fields (may be blank):",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 500},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in data.items():
            if key not in empty_fields:
                continue
            text = str(value or "").strip()
            if not text:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def add_project_details(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
        "seed_idea",
        "genre",
        "tone",
        "style_notes",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one project detail first."}, status=400)

    prompt_lines = [
        "You are a novelist's project development assistant.",
        "Goal: add helpful additional detail that expands (but does not repeat) what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- For genre/tone: only include if currently blank.",
        "- For seed_idea/style_notes: provide an additive paragraph (no bullet points).",
        "",
        "Project title: " + (project.title or ""),
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "Current fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 500},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in data.items():
            if key not in allowed_fields:
                continue
            if key in {"genre", "tone"} and current.get(key):
                continue
            text = str(value or "").strip()
            if not text:
                continue
            if current.get(key) and text in current[key]:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def brainstorm_scene(request, slug, pk):
    scene = _get_scene_for_user(request, slug=slug, pk=pk)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
        "title",
        "summary",
        "pov",
        "location",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    rejected = {k for k in allowed_fields if request.POST.get(f"reject_{k}")}
    if rejected:
        for k in rejected:
            current[k] = ""
    empty_fields = [k for k in allowed_fields if not current.get(k)]
    if not empty_fields:
        return JsonResponse({"ok": True, "suggestions": {}})

    prompt_lines = [
        "You are a novelist's scene brainstorming assistant.",
        "Goal: fill in ONLY the currently-empty fields with strong, coherent ideas.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- For 'title': keep it short and specific.",
        "- For 'summary': write concise prose (no bullet points).",
        "- For 'pov': provide a character name or short POV tag.",
        "- For 'location': provide a short place name.",
        "",
        "Project title: " + (scene.project.title or ""),
        "Chapter: " + (getattr(scene.parent, "title", "") or "").strip(),
    ]
    bible_lines = _get_story_bible_context(scene.project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "Existing fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 500},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in data.items():
            if key not in empty_fields:
                continue
            text = str(value or "").strip()
            if not text:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def add_scene_details(request, slug, pk):
    scene = _get_scene_for_user(request, slug=slug, pk=pk)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
        "title",
        "summary",
        "pov",
        "location",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one scene detail first."}, status=400)

    prompt_lines = [
        "You are a novelist's scene development assistant.",
        "Goal: add helpful additional detail that expands (but does not repeat) what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- For 'title': only include if currently blank.",
        "- For 'pov'/'location': only include if currently blank.",
        "- For 'summary': provide an additive paragraph (no bullet points).",
        "",
        "Project title: " + (scene.project.title or ""),
        "Chapter: " + (getattr(scene.parent, "title", "") or "").strip(),
    ]
    bible_lines = _get_story_bible_context(scene.project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "Current fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 500},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in data.items():
            if key not in allowed_fields:
                continue
            if key in {"title", "pov", "location"} and current.get(key):
                continue
            text = str(value or "").strip()
            if not text:
                continue
            if current.get(key) and text in current[key]:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def move_scene(request, slug):
    project = _get_project_for_user(request, slug)
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
@login_required
def rename_scene_title(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    scene_id = request.POST.get("scene_id")
    title = (request.POST.get("title") or "").strip()

    if not scene_id:
        return JsonResponse({"ok": False, "error": "Missing scene_id."}, status=400)
    if len(title) > 255:
        return JsonResponse({"ok": False, "error": "Title is too long (max 255 characters)."}, status=400)

    scene = get_object_or_404(
        OutlineNode,
        id=scene_id,
        project=project,
        node_type=OutlineNode.NodeType.SCENE,
    )
    scene.title = title
    scene.save(update_fields=["title", "updated_at"])
    return JsonResponse({"ok": True, "title": scene.title})


@require_POST
@login_required
def brainstorm_character(request, slug):
    project = _get_project_for_user(request, slug)
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
    rejected = {k for k in allowed_fields if request.POST.get(f"reject_{k}")}
    if rejected:
        for k in rejected:
            current[k] = ""
    empty_fields = [k for k in allowed_fields if not current.get(k)]
    if not empty_fields:
        return JsonResponse({"ok": True, "suggestions": {}})

    prompt_lines = [
        "You are a novelist's character assistant.",
        "Goal: fill in ONLY the currently-empty fields with plausible details that complement the already-filled fields.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- Keep answers concise but useful.",
        "- 'age' must be an integer (omit it if unsure).",
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "",
            "Existing character fields (may be blank):",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines)

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


@require_POST
@login_required
def add_character_details(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    allowed_fields = [
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

    name = (request.POST.get("name") or "").strip()
    current = {k: (request.POST.get(k) or "").strip() for k in ["name", *allowed_fields]}
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required to add details."}, status=400)

    prompt_lines = [
        "You are a novelist's character development assistant.",
        "Goal: refine the character by adding useful, specific detail.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- Do NOT change the character's name.",
        "- For fields that already have text, return ONLY additional text to append (do not rewrite or repeat).",
        "- For fields that are empty, provide a good starter value when it helps.",
        "- Keep additions concise but concrete (sensory, behavior, contradictions, tells).",
        "- 'age' must be an integer (omit if unsure).",
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "",
            "Current character fields:",
            json.dumps(current, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines)

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 650},
        )
        raw = (result.text or "").strip()
        suggestions = json.loads(raw) if raw else {}
        if not isinstance(suggestions, dict):
            raise ValueError("Model response must be a JSON object.")

        filtered = {}
        for key, value in suggestions.items():
            if key not in allowed_fields:
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
                if str(current.get("age") or "").strip():
                    continue
                filtered[key] = age_int
            else:
                text = str(value).strip()
                if not text:
                    continue
                # Avoid no-op "additions" that are identical to existing content.
                existing = (current.get(key) or "").strip()
                if existing and text in existing:
                    continue
                filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


class ProjectListView(LoginRequiredMixin, ListView):
    model = NovelProject
    template_name = "main/project_list.html"
    context_object_name = "projects"
    ordering = ["title"]

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user).order_by(*self.ordering)


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = NovelProject
    template_name = "main/project_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)


class CharacterListView(LoginRequiredMixin, ListView):
    model = Character
    template_name = "main/character_list.html"
    context_object_name = "characters"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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


class CharacterCreateView(LoginRequiredMixin, CreateView):
    model = Character
    form_class = CharacterForm
    template_name = "main/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        try:
            form.instance.extra_fields = _parse_character_extra_fields(self.request.POST)
        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        response = super().form_valid(form)
        messages.success(self.request, "Character created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["extra_rows"] = []
        return ctx

    def get_success_url(self):
        next_url = (self.request.GET.get("next") or "").strip()
        if next_url:
            return next_url
        return reverse_lazy("character-list", kwargs={"slug": self.project.slug})


class CharacterUpdateView(LoginRequiredMixin, UpdateView):
    form_class = CharacterForm
    template_name = "main/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Character.objects.filter(project=self.project)

    def form_valid(self, form):
        try:
            form.instance.extra_fields = _parse_character_extra_fields(self.request.POST)
        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        messages.success(self.request, "Character saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["extra_rows"] = sorted((self.object.extra_fields or {}).items(), key=lambda kv: kv[0].lower())
        return ctx

    def get_success_url(self):
        return reverse_lazy("character-list", kwargs={"slug": self.project.slug})


class CharacterDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "main/character_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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


def _parse_character_extra_fields(post_data) -> dict[str, str]:
    keys = post_data.getlist("extra_key")
    values = post_data.getlist("extra_value")

    extras: dict[str, str] = {}
    for k, v in zip(keys, values):
        key = (k or "").strip()
        value = (v or "").strip()
        if not key and not value:
            continue
        if not key:
            raise ValueError("Field name cannot be blank.")
        if key in extras:
            raise ValueError(f"Duplicate field name: {key}")
        extras[key] = value

    return extras


def _parse_location_objects(post_data) -> dict[str, str]:
    keys = post_data.getlist("object_key")
    values = post_data.getlist("object_value")

    objects: dict[str, str] = {}
    for k, v in zip(keys, values):
        key = (k or "").strip()
        value = (v or "").strip()
        if not key and not value:
            continue
        if not key:
            raise ValueError("Object name cannot be blank.")
        if key in objects:
            raise ValueError(f"Duplicate object name: {key}")
        objects[key] = value

    return objects


def _extract_json_object(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "{}"
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response did not contain a JSON object.")
    return text[start : end + 1]


_BRACE_SEGMENT_RE = re.compile(r"\{[^{}]*\}")


def _split_braced_segments(text: str) -> list[dict[str, str | bool]]:
    parts = re.split(r"(\{[^{}]*\})", text)
    segments: list[dict[str, str | bool]] = []
    for part in parts:
        if part == "":
            continue
        protected = bool(_BRACE_SEGMENT_RE.fullmatch(part))
        segments.append({"text": part, "protected": protected})
    return segments


@require_POST
@login_required
def brainstorm_location_description(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if description:
        return JsonResponse({"ok": True, "suggestions": {}})

    try:
        objects_map = _parse_location_objects(request.POST)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    prompt_lines = [
        "You are a worldbuilding assistant for a novelist.",
        "Write a vivid but concise location description (2–5 short paragraphs).",
        "Use sensory detail, atmosphere, and concrete specifics. Avoid bullet points.",
        "",
        "Return STRICT JSON only (no markdown), in the form:",
        '{"description": "..."}',
        "",
        "Project title: " + (project.title or ""),
        "Location name: " + name,
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.append("Known objects (JSON map): " + json.dumps(objects_map, ensure_ascii=False))
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 450},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")
        text = str(data.get("description") or "").strip()
        if not text:
            return JsonResponse({"ok": True, "suggestions": {}})
        return JsonResponse({"ok": True, "suggestions": {"description": text}})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def add_location_details(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    try:
        objects_map = _parse_location_objects(request.POST)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    prompt_lines = [
        "You are a worldbuilding assistant for a novelist.",
        "Goal: add NEW details to the existing location description (do not rewrite it).",
        "Rules:",
        "- Return STRICT JSON only (no markdown) in the form: {\"description\": \"...\"}",
        "- If the description is empty, write an initial description.",
        "- If the description already exists, return ONLY additional text to append (avoid repeating existing lines).",
        "- Add concrete details: layout, textures, lighting, smell, ambient sound, a standout object, and a small lived-in detail.",
        "",
        "Project title: " + (project.title or ""),
        "Location name: " + name,
    ]
    bible_lines = _get_story_bible_context(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "Existing description: " + description,
            "Known objects (JSON map): " + json.dumps(objects_map, ensure_ascii=False),
        ]
    )
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.7, "max_tokens": 450},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")
        text = str(data.get("description") or "").strip()
        if not text:
            return JsonResponse({"ok": True, "suggestions": {}})
        if description and text in description:
            return JsonResponse({"ok": True, "suggestions": {}})
        return JsonResponse({"ok": True, "suggestions": {"description": text}})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@require_POST
@login_required
def extract_location_objects(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not description:
        return JsonResponse({"ok": False, "error": "Description is required."}, status=400)

    try:
        existing_objects = _parse_location_objects(request.POST)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    existing_keys_lower = {k.lower() for k in (existing_objects or {}).keys() if isinstance(k, str)}

    prompt = "\n".join(
        [
            "You are a worldbuilding assistant for a novelist.",
            "Task: extract concrete objects mentioned or strongly implied by the description, including background/lived-in items.",
            "For each object, provide short attributes (materials, condition, placement, notable features) as a compact phrase.",
            "",
            "Rules:",
            '- Return STRICT JSON only (no markdown) in the form: {"objects": {"key": "attributes"}}',
            "- Keys should be short singular nouns (e.g., \"crate\", \"holo-sign\", \"workbench\").",
            "- Do not include character names or abstract concepts as objects.",
            "- If an object already exists in the provided object map, do not repeat it.",
            "- Return up to 12 objects.",
            "",
            "Project title: " + (project.title or ""),
            "Location name: " + name,
            "Description: " + description,
            "Existing objects (JSON map): " + json.dumps(existing_objects, ensure_ascii=False),
        ]
    ).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            params={"temperature": 0.4, "max_tokens": 550},
        )
        data = json.loads(_extract_json_object(result.text))
        if not isinstance(data, dict):
            raise ValueError("Model response must be a JSON object.")
        raw_objects = data.get("objects") or {}
        if not isinstance(raw_objects, dict):
            raise ValueError('"objects" must be a JSON object.')

        extracted: dict[str, str] = {}
        for raw_key, raw_val in raw_objects.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if key.lower() in existing_keys_lower:
                continue
            val = str(raw_val or "").strip()
            extracted[key] = val
            if len(extracted) >= 12:
                break

        return JsonResponse({"ok": True, "objects": extracted})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = "main/location_list.html"
    context_object_name = "locations"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = Location.objects.filter(project=self.project).order_by("name")
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        return ctx


class LocationCreateView(LoginRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = "main/location_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_rows"] = []
        ctx["next_url"] = (self.request.GET.get("next") or "").strip()
        return ctx

    def form_valid(self, form):
        form.instance.project = self.project
        try:
            form.instance.objects_map = _parse_location_objects(self.request.POST)
        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)

        response = super().form_valid(form)
        messages.success(self.request, "Location created.")
        return response

    def get_success_url(self):
        next_url = (self.request.GET.get("next") or "").strip()
        if next_url:
            return _add_query_params(next_url, prefill_location=self.object.name)
        return reverse_lazy("location-list", kwargs={"slug": self.project.slug})


class LocationUpdateView(LoginRequiredMixin, UpdateView):
    form_class = LocationForm
    template_name = "main/location_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Location.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_rows"] = sorted((self.object.objects_map or {}).items(), key=lambda kv: kv[0].lower())
        ctx["next_url"] = (self.request.GET.get("next") or "").strip()
        return ctx

    def form_valid(self, form):
        try:
            form.instance.objects_map = _parse_location_objects(self.request.POST)
        except Exception as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)

        messages.success(self.request, "Location saved.")
        return super().form_valid(form)

    def get_success_url(self):
        next_url = (self.request.GET.get("next") or "").strip()
        if next_url:
            return _add_query_params(next_url, prefill_location=self.object.name)
        return reverse_lazy("location-list", kwargs={"slug": self.project.slug})


class LocationDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "main/location_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Location.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx

    def get_success_url(self):
        return reverse_lazy("location-list", kwargs={"slug": self.project.slug})

    def form_valid(self, form):
        messages.success(self.request, "Location deleted.")
        return super().form_valid(form)


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, "Project created.")
        return response

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Project saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("project-detail", kwargs={"slug": self.object.slug})


class ProjectDeleteView(LoginRequiredMixin, DeleteView):
    model = NovelProject
    template_name = "main/project_confirm_delete.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    def get_success_url(self):
        return reverse_lazy("project-list")

    def form_valid(self, form):
        messages.success(self.request, "Project deleted.")
        return super().form_valid(form)


class ProjectDashboardView(LoginRequiredMixin, DetailView):
    model = NovelProject
    template_name = "main/project_dashboard.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    context_object_name = "project"

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

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
                chapters_by_act.setdefault(n.id, [])
            elif n.node_type == OutlineNode.NodeType.CHAPTER and n.parent_id:
                chapters_by_act.setdefault(n.parent_id, []).append(n)
                scenes_by_chapter.setdefault(n.id, [])
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


class StoryBibleUpdateView(LoginRequiredMixin, UpdateView):
    form_class = StoryBibleForm
    template_name = "main/bible_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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
        return reverse_lazy("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})


class OutlineChapterCreateView(LoginRequiredMixin, CreateView):
    form_class = OutlineChapterForm
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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
        return reverse_lazy("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})


class OutlineSceneCreateView(LoginRequiredMixin, CreateView):
    form_class = OutlineSceneForm
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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
        ctx["character_list"] = Character.objects.filter(project=self.project).order_by("name")
        form = ctx.get("form")
        selected = []
        if form is not None and "characters" in form.fields:
            raw = form["characters"].value()
            if raw:
                if isinstance(raw, (list, tuple)):
                    selected = [str(val) for val in raw]
                else:
                    selected = [str(raw)]
        ctx["selected_character_ids"] = selected
        if selected:
            ctx["selected_character_names"] = list(
                Character.objects.filter(project=self.project, id__in=selected).order_by("name").values_list("name", flat=True)
            )
        else:
            ctx["selected_character_names"] = []
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        prefill = (self.request.GET.get("prefill_location") or "").strip()
        if prefill:
            kwargs["prefill_location"] = prefill
        return kwargs

    def post(self, request, *args, **kwargs):
        if request.POST.get("location") == OutlineSceneForm.LOCATION_CREATE_SENTINEL:
            create_url = reverse("location-create", kwargs={"slug": self.project.slug})
            return HttpResponseRedirect(_add_query_params(create_url, next=request.get_full_path()))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        form.instance.parent = self.chapter
        form.instance.node_type = OutlineNode.NodeType.SCENE
        response = super().form_valid(form)
        messages.success(self.request, "Scene added.")
        return response

    def get_success_url(self):
        return reverse_lazy("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})


class OutlineNodeUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "main/outline_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if getattr(self.object, "node_type", None) == OutlineNode.NodeType.SCENE:
            kwargs["project"] = self.project
            prefill = (self.request.GET.get("prefill_location") or "").strip()
            if prefill:
                kwargs["prefill_location"] = prefill
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.object
        ctx["project"] = self.project
        ctx["parent_node"] = obj.parent
        ctx["node_kind"] = "chapter" if obj.node_type == OutlineNode.NodeType.CHAPTER else "scene"
        if obj.node_type == OutlineNode.NodeType.SCENE:
            ctx["character_list"] = Character.objects.filter(project=self.project).order_by("name")
            form = ctx.get("form")
            selected = []
            if form is not None and "characters" in form.fields:
                raw = form["characters"].value()
                if raw:
                    if isinstance(raw, (list, tuple)):
                        selected = [str(val) for val in raw]
                    else:
                        selected = [str(raw)]
            ctx["selected_character_ids"] = selected
            if selected:
                ctx["selected_character_names"] = list(
                    Character.objects.filter(project=self.project, id__in=selected)
                    .order_by("name")
                    .values_list("name", flat=True)
                )
            else:
                ctx["selected_character_names"] = []
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Saved.")
        self.object = form.save()
        return HttpResponseRedirect(
            reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})
        )

    def get_success_url(self):
        return reverse_lazy("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action") or ""

        if self.object.node_type == OutlineNode.NodeType.SCENE:
            if request.POST.get("location") == OutlineSceneForm.LOCATION_CREATE_SENTINEL:
                create_url = reverse("location-create", kwargs={"slug": self.project.slug})
                return HttpResponseRedirect(_add_query_params(create_url, next=request.get_full_path()))

        if self.object.node_type == OutlineNode.NodeType.SCENE and action in {"structurize", "render", "reshuffle", "import-draft"}:
            if action == "structurize":
                data = request.POST.copy()
                data.pop("structure_json", None)
                data.pop("rendered_text", None)
                kwargs = self.get_form_kwargs()
                kwargs["data"] = data
                form = self.get_form_class()(**kwargs)
            else:
                form = self.get_form()
            if not form.is_valid():
                return self.form_invalid(form)

            scene = form.save(commit=False)
            if action == "structurize":
                summary = (scene.summary or "").strip()
                if not summary:
                    form.add_error("summary", "Add a scene summary first.")
                    return self.form_invalid(form)

                prompt_lines = [
                    "Write a rough scene draft in prose (no bullet points, no JSON, no markdown headings).",
                    "Write in continuous prose with paragraphs; do not include section headers.",
                    "Keep it grounded in the provided summary, POV, and location when available.",
                    "Avoid meta commentary and avoid explaining what you are doing.",
                    "",
                    "Title: " + (scene.title or ""),
                    "POV: " + (scene.pov or ""),
                    "Location: " + (scene.location or ""),
                    "Summary: " + summary,
                ]
                bible_lines = _get_story_bible_context(scene.project)
                if bible_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(bible_lines)
                prompt = "\n".join(prompt_lines).strip()

                try:
                    result = call_llm(
                        prompt=prompt,
                        model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                        params={"temperature": 0.7, "max_tokens": 900},
                    )
                    scene.structure_json = (result.text or "").strip()
                    scene.save()
                    messages.success(request, "Generated draft from scene summary.")
                except Exception:
                    scene.structure_json = summary
                    scene.save()
                    messages.warning(
                        request,
                        "OpenAI draft failed; saved the summary as a placeholder draft instead. Check your model/API settings and try again.",
                    )
            elif action == "reshuffle":
                raw_draft = (scene.structure_json or "").strip()
                if not raw_draft:
                    form.add_error("structure_json", "Add a draft first.")
                    return self.form_invalid(form)

                segments = _split_braced_segments(raw_draft)
                plain_segments = [seg["text"] for seg in segments if not seg["protected"]]
                if not any(str(seg).strip() for seg in plain_segments):
                    messages.warning(
                        request,
                        "Reshuffle ignored protected text; kept the existing draft. Try again.",
                    )
                    return HttpResponseRedirect(
                        reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                    )
                prompt_lines = [
                    "Rewrite each segment in the JSON array into a fresh version with different phrasing.",
                    "Rules:",
                    "- Return STRICT JSON only in the form: {\"segments\": [...]}",
                    "- Keep the same number of segments and the same order.",
                    "- If a segment is only whitespace, return it unchanged.",
                    "- Use prose (no bullet points, no JSON inside the strings, no markdown headings).",
                    "- Avoid meta commentary and avoid explaining what you are doing.",
                    "",
                    "Segments (JSON array):",
                    json.dumps(plain_segments, ensure_ascii=False),
                ]
                bible_lines = _get_story_bible_context(scene.project)
                if bible_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(bible_lines)
                prompt = "\n".join(prompt_lines).strip()

                try:
                    result = call_llm(
                        prompt=prompt,
                        model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                        params={"temperature": 0.8, "max_tokens": 900},
                    )
                    data = json.loads(_extract_json_object(result.text))
                    updated = data.get("segments")
                    if not isinstance(updated, list) or len(updated) != len(plain_segments):
                        messages.warning(
                            request,
                            "Reshuffle ignored protected text; kept the existing draft. Try again.",
                        )
                        return HttpResponseRedirect(
                            reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                        )

                    rebuilt = []
                    idx = 0
                    for seg in segments:
                        if seg["protected"]:
                            rebuilt.append(seg["text"])
                            continue
                        original = str(seg["text"])
                        replacement = updated[idx] if idx < len(updated) else ""
                        idx += 1
                        if not original.strip():
                            rebuilt.append(original)
                            continue
                        if replacement is None:
                            rebuilt.append(original)
                            continue
                        replacement_text = str(replacement)
                        rebuilt.append(replacement_text if replacement_text.strip() else original)
                    scene.structure_json = "".join(rebuilt)
                    scene.save()
                    messages.success(request, "Reshuffled draft.")
                except Exception:
                    messages.warning(
                        request,
                        "Reshuffle ignored protected text; kept the existing draft. Try again.",
                    )
            else:
                raw_draft = (scene.structure_json or "").strip()
                if not raw_draft:
                    form.add_error("structure_json", "Add a draft first.")
                    return self.form_invalid(form)

                if action == "import-draft":
                    cleaned = raw_draft.replace("{", "").replace("}", "")
                    scene.rendered_text = cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else "")
                    scene.save()
                    messages.success(request, "Imported draft into final text.")
                    return HttpResponseRedirect(
                        reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                    )

                prompt = "\n".join(
                    [
                        "Rewrite the draft into polished novel prose (no bullet points, no JSON, no markdown headings).",
                        "Write in continuous prose with paragraphs; do not include section headers.",
                        "Preserve the story beats, POV, and location implied by the draft.",
                        "Avoid meta commentary and avoid explaining what you are doing.",
                        "",
                        "Draft:",
                        raw_draft,
                    ]
                ).strip()

                try:
                    result = call_llm(
                        prompt=prompt,
                        model_name=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                        params={
                            "temperature": 0.7,
                            "max_tokens": 1200,
                        },
                    )
                    scene.rendered_text = (result.text or "").strip() + "\n"
                    scene.save()
                    messages.success(request, "Rendered novel prose from draft.")
                except Exception:
                    scene.rendered_text = raw_draft + ("\n" if not raw_draft.endswith("\n") else "")
                    scene.save()
                    messages.warning(
                        request,
                        "OpenAI render failed; saved the draft as rendered prose instead. Check your model/API settings and try again.",
                    )

            return HttpResponseRedirect(
                reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
            )

        return super().post(request, *args, **kwargs)


class OutlineNodeDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "main/outline_node_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
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
