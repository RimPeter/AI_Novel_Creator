from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Max, Prefetch
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from main.billing import billing_enabled, user_has_active_plan
from main.llm import call_llm
from main.text_models import get_user_text_model

from .forms import ComicBibleForm, ComicCanvasNodeForm, ComicCharacterForm, ComicIssueForm, ComicLocationForm, ComicPageForm, ComicPanelForm, ComicProjectForm
from .models import ComicBible, ComicCanvasNode, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject

logger = logging.getLogger(__name__)
ISSUE_AI_FIELDS = [
    "title",
    "summary",
    "theme",
    "opening_hook",
    "closing_hook",
    "notes",
]
ISSUE_AI_APPEND_FIELDS = {"summary", "opening_hook", "closing_hook", "notes"}
PAGE_AI_FIELDS = [
    "title",
    "summary",
    "page_turn_hook",
    "notes",
]
PAGE_AI_APPEND_FIELDS = {"summary", "page_turn_hook", "notes"}


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


def _log_exception(message: str, *args) -> None:
    logger.error(message, *args, exc_info=not getattr(settings, "RUNNING_TESTS", False))


def _get_billing_url(request, *, reason: str = "") -> str:
    billing_url = reverse("billing")
    params = {}
    next_url = request.get_full_path()
    if next_url:
        params["next"] = next_url
    if reason:
        params["required"] = reason
    if params:
        billing_url = f"{billing_url}?{urlencode(params)}"
    return billing_url


def _ai_context_for_request(request) -> dict[str, object]:
    return {
        "billing_enabled": billing_enabled(),
        "has_active_plan": user_has_active_plan(request.user),
        "ai_billing_url": _get_billing_url(request, reason="active-plan"),
    }


def _subscription_required_response(request):
    if not billing_enabled():
        return None
    if user_has_active_plan(request.user):
        return None
    error = "An active plan is required to generate text and use tokens."
    billing_url = _get_billing_url(request, reason="active-plan")
    return JsonResponse({"ok": False, "error": error, "billing_url": billing_url}, status=402)


def _ensure_json_ai_request(request):
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked
    return None


def _json_internal_error() -> JsonResponse:
    _log_exception("Comic book issue AI request failed.")
    return JsonResponse({"ok": False, "error": "Request failed. Please try again."}, status=400)


def _extract_json_object(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "{}"
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response did not contain a JSON object.")
    return text[start : end + 1]


def _call_llm_json_object(*, prompt: str, model_name: str, params: dict) -> dict:
    result = call_llm(prompt=prompt, model_name=model_name, params=params)
    raw_text = (result.text or "").strip()
    data = json.loads(_extract_json_object(raw_text) if raw_text else "{}")
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object.")
    return data


def _dedupe_appended_text(existing: str, addition: str) -> str:
    existing_text = (existing or "").strip()
    addition_text = (addition or "").strip()
    if not addition_text:
        return ""
    if not existing_text:
        return addition_text

    existing_lower = existing_text.lower()
    addition_lower = addition_text.lower()
    if addition_lower in existing_lower:
        return ""

    def trim_overlap(text: str) -> str:
        return text.lstrip(" \t\r\n;,:.-").strip()

    if addition_lower.startswith(existing_lower):
        return trim_overlap(addition_text[len(existing_text) :])

    max_overlap = min(len(existing_text), len(addition_text))
    for overlap in range(max_overlap, 0, -1):
        if existing_lower[-overlap:] == addition_lower[:overlap]:
            return trim_overlap(addition_text[overlap:])

    return addition_text


def _truncate_ai_context(value: str, *, max_length: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    truncated = text[: max_length - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (truncated or text[: max_length - 3]) + "..."


def _comic_project_context_lines(project: ComicProject) -> list[str]:
    lines = ["Project title: " + (project.title or "")]
    for label, value in [
        ("Project logline", project.logline),
        ("Genre", project.genre),
        ("Tone", project.tone),
        ("Target audience", project.target_audience),
        ("Art style notes", project.art_style_notes),
        ("Format notes", project.format_notes),
    ]:
        text = _truncate_ai_context(value)
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _comic_bible_context_lines(project: ComicProject) -> list[str]:
    try:
        bible = project.bible
    except ComicBible.DoesNotExist:
        return []

    lines = []
    for label, value in [
        ("Comic bible premise", bible.premise),
        ("Comic bible world rules", bible.world_rules),
        ("Comic bible visual rules", bible.visual_rules),
        ("Comic bible continuity rules", bible.continuity_rules),
        ("Comic bible cast notes", bible.cast_notes),
    ]:
        text = _truncate_ai_context(value)
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _comic_character_context_lines(project: ComicProject) -> list[str]:
    characters = list(project.characters.order_by("name")[:8])
    if not characters:
        return []

    lines = ["Key characters:"]
    for character in characters:
        parts = [character.name]
        role = _truncate_ai_context(character.role, max_length=80)
        description = _truncate_ai_context(character.description, max_length=140)
        if role:
            parts.append(role)
        if description:
            parts.append(description)
        lines.append("- " + " | ".join(parts))
    return lines


def _comic_location_context_lines(project: ComicProject) -> list[str]:
    locations = list(project.locations.order_by("name")[:8])
    if not locations:
        return []

    lines = ["Key locations:"]
    for location in locations:
        parts = [location.name]
        description = _truncate_ai_context(location.description, max_length=140)
        if description:
            parts.append(description)
        lines.append("- " + " | ".join(parts))
    return lines


def _issue_ai_current(request) -> dict[str, str]:
    return {field: (request.POST.get(field) or "").strip() for field in ISSUE_AI_FIELDS}


def _issue_ai_meta(request) -> dict[str, str]:
    return {
        "number": (request.POST.get("number") or "").strip(),
        "planned_page_count": (request.POST.get("planned_page_count") or "").strip(),
        "status": (request.POST.get("status") or "").strip(),
    }


def _page_ai_current(request) -> dict[str, str]:
    return {field: (request.POST.get(field) or "").strip() for field in PAGE_AI_FIELDS}


def _page_ai_meta(request) -> dict[str, str]:
    return {
        "page_number": (request.POST.get("page_number") or "").strip(),
        "page_role": (request.POST.get("page_role") or "").strip(),
        "layout_type": (request.POST.get("layout_type") or "").strip(),
    }


def _issue_brainstorm_suggestions(*, project: ComicProject, current: dict[str, str], meta: dict[str, str], user) -> dict[str, str]:
    empty_fields = [field for field in ISSUE_AI_FIELDS if not current.get(field)]
    if not empty_fields:
        return {}

    prompt_lines = [
        "You are a comic-book issue planning assistant.",
        "Goal: fill ONLY the currently-empty issue fields so they fit the wider comic project.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(ISSUE_AI_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- title: 2-6 words.",
        "- theme: a short phrase.",
        "- summary: 3-6 sentences describing the issue arc.",
        "- opening_hook: 1-2 punchy sentences.",
        "- closing_hook: 1-2 punchy sentences that create forward momentum.",
        "- notes: 3-6 short lines for pacing, continuity, or visual emphasis.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    prompt_lines.extend(
        [
            "Issue number: " + (meta.get("number") or ""),
            "Planned page count: " + (meta.get("planned_page_count") or ""),
            "Issue status: " + (meta.get("status") or ""),
        ]
    )
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    character_lines = _comic_character_context_lines(project)
    if character_lines:
        prompt_lines.append("")
        prompt_lines.extend(character_lines)
    location_lines = _comic_location_context_lines(project)
    if location_lines:
        prompt_lines.append("")
        prompt_lines.extend(location_lines)
    prompt_lines.extend(
        [
            "",
            "Current issue fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 650},
    )

    filtered = {}
    for key, value in data.items():
        if key not in empty_fields:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        filtered[key] = text
    return filtered


def _issue_add_detail_suggestions(*, project: ComicProject, current: dict[str, str], meta: dict[str, str], user) -> dict[str, str]:
    prompt_lines = [
        "You are a comic-book issue planning assistant.",
        "Goal: add fresh detail to the current issue plan without repeating or contradicting what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(ISSUE_AI_FIELDS),
        "- title and theme: only include them if they are currently blank.",
        "- summary, opening_hook, closing_hook, and notes: return ONLY additive text to append, not a full rewrite.",
        "- Keep additions aligned with the planned page count, project tone, and cast/location context.",
        "- notes: 2-5 short lines for pacing, continuity, or visual emphasis.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    prompt_lines.extend(
        [
            "Issue number: " + (meta.get("number") or ""),
            "Planned page count: " + (meta.get("planned_page_count") or ""),
            "Issue status: " + (meta.get("status") or ""),
        ]
    )
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    character_lines = _comic_character_context_lines(project)
    if character_lines:
        prompt_lines.append("")
        prompt_lines.extend(character_lines)
    location_lines = _comic_location_context_lines(project)
    if location_lines:
        prompt_lines.append("")
        prompt_lines.extend(location_lines)
    prompt_lines.extend(
        [
            "",
            "Current issue fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 650},
    )

    filtered = {}
    for key, value in data.items():
        if key not in ISSUE_AI_FIELDS:
            continue
        existing = current.get(key, "")
        if key not in ISSUE_AI_APPEND_FIELDS and existing:
            continue

        text = str(value or "").strip()
        if not text:
            continue

        if key in ISSUE_AI_APPEND_FIELDS:
            text = _dedupe_appended_text(existing, text)
        if not text:
            continue
        filtered[key] = text
    return filtered


def _comic_issue_context_lines(issue: ComicIssue) -> list[str]:
    lines = [f"Issue number: {issue.number}", "Issue title: " + (issue.title or "")]
    for label, value in [
        ("Issue summary", issue.summary),
        ("Issue theme", issue.theme),
        ("Issue opening hook", issue.opening_hook),
        ("Issue closing hook", issue.closing_hook),
        ("Issue notes", issue.notes),
    ]:
        text = _truncate_ai_context(value)
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _page_brainstorm_suggestions(*, project: ComicProject, issue: ComicIssue, current: dict[str, str], meta: dict[str, str], user) -> dict[str, str]:
    empty_fields = [field for field in PAGE_AI_FIELDS if not current.get(field)]
    if not empty_fields:
        return {}

    prompt_lines = [
        "You are a comic-book page planning assistant.",
        "Goal: fill ONLY the currently-empty page fields so they fit the issue and project context.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(PAGE_AI_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- title: 2-6 words.",
        "- summary: 2-5 sentences covering the page beat, pacing, and visual purpose.",
        "- page_turn_hook: 1-2 punchy sentences only if this page should push into the next beat.",
        "- notes: 2-5 short lines for composition, pacing, continuity, or emphasis.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    prompt_lines.append("")
    prompt_lines.extend(_comic_issue_context_lines(issue))
    prompt_lines.extend(
        [
            "Page number: " + (meta.get("page_number") or ""),
            "Page role: " + (meta.get("page_role") or ""),
            "Layout type: " + (meta.get("layout_type") or ""),
        ]
    )
    prompt_lines.extend(
        [
            "",
            "Current page fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 500},
    )

    filtered = {}
    for key, value in data.items():
        if key not in empty_fields:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        filtered[key] = text
    return filtered


def _page_add_detail_suggestions(*, project: ComicProject, issue: ComicIssue, current: dict[str, str], meta: dict[str, str], user) -> dict[str, str]:
    prompt_lines = [
        "You are a comic-book page planning assistant.",
        "Goal: add fresh detail to the current page plan without repeating or contradicting what is already there.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(PAGE_AI_FIELDS),
        "- title: only include it if it is currently blank.",
        "- summary, page_turn_hook, and notes: return ONLY additive text to append, not a rewrite.",
        "- Keep additions aligned with the issue arc, page role, and layout type.",
        "- notes: 2-5 short lines for composition, pacing, continuity, or emphasis.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    prompt_lines.append("")
    prompt_lines.extend(_comic_issue_context_lines(issue))
    prompt_lines.extend(
        [
            "Page number: " + (meta.get("page_number") or ""),
            "Page role: " + (meta.get("page_role") or ""),
            "Layout type: " + (meta.get("layout_type") or ""),
        ]
    )
    prompt_lines.extend(
        [
            "",
            "Current page fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 500},
    )

    filtered = {}
    for key, value in data.items():
        if key not in PAGE_AI_FIELDS:
            continue
        existing = current.get(key, "")
        if key not in PAGE_AI_APPEND_FIELDS and existing:
            continue

        text = str(value or "").strip()
        if not text:
            continue

        if key in PAGE_AI_APPEND_FIELDS:
            text = _dedupe_appended_text(existing, text)
        if not text:
            continue
        filtered[key] = text
    return filtered


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


def _find_canvas_layout_node(layout: dict, canvas_key: str, *, parent_key: str = "") -> dict | None:
    if not isinstance(layout, dict):
        return None

    node_key = str(layout.get("canvas_key") or "").strip()
    if node_key == canvas_key:
        return {
            "node": layout,
            "parent_key": parent_key,
        }

    for index, child in enumerate(layout.get("children") or []):
        if not isinstance(child, dict):
            continue
        match = _find_canvas_layout_node(child, canvas_key, parent_key=node_key)
        if match is not None:
            match["child_index"] = index
            return match
    return None


def _sync_canvas_node_from_layout(node: ComicCanvasNode) -> ComicCanvasNode:
    layout = node.page.canvas_layout or {}
    match = _find_canvas_layout_node(layout, node.canvas_key)
    if match is None:
        node.node_type = ComicCanvasNode.NodeType.PANEL
        node.parent = None
        node.child_index = 0
        node.split_direction = ""
        node.split_ratio = None
        return node

    layout_node = match["node"]
    parent_key = str(match.get("parent_key") or "").strip()
    node.node_type = (
        ComicCanvasNode.NodeType.SPLIT
        if str(layout_node.get("type") or "").strip() == "split"
        else ComicCanvasNode.NodeType.PANEL
    )
    node.child_index = int(match.get("child_index") or 0)
    split_direction = str(layout_node.get("direction") or "").strip()
    node.split_direction = split_direction if split_direction in {choice for choice, _label in ComicCanvasNode.SplitDirection.choices} else ""
    ratio = layout_node.get("ratio")
    node.split_ratio = ratio if ratio is not None else None
    node.parent = None
    if parent_key:
        node.parent = ComicCanvasNode.objects.filter(page=node.page, canvas_key=parent_key).first()
    return node


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
        ctx.update(_ai_context_for_request(self.request))
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
        ctx.update(_ai_context_for_request(self.request))
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
        ctx.update(_ai_context_for_request(self.request))
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
        ctx.update(_ai_context_for_request(self.request))
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


class ComicCanvasNodeUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicCanvasNodeForm
    template_name = "comic_book/canvas_node_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        self.page = _get_page_for_issue(self.issue, kwargs["page_pk"])
        self.canvas_key = (kwargs.get("canvas_key") or "").strip()
        if not self.canvas_key:
            raise Http404("Canvas key is required.")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        obj, _created = ComicCanvasNode.objects.get_or_create(
            page=self.page,
            canvas_key=self.canvas_key,
            defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
        )
        _sync_canvas_node_from_layout(obj)
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        form.instance.page = self.page
        _sync_canvas_node_from_layout(form.instance)
        response = super().form_valid(form)
        messages.success(self.request, "Canvas brief saved.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        canvas_node = self.object
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["page"] = self.page
        ctx["canvas_node"] = canvas_node
        return ctx

    def get_success_url(self):
        return reverse("comic_book:page-edit", kwargs={"slug": self.project.slug, "issue_pk": self.issue.pk, "pk": self.page.pk})


@login_required
@require_POST
def brainstorm_issue(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _issue_ai_current(request)
    meta = _issue_ai_meta(request)
    try:
        suggestions = _issue_brainstorm_suggestions(project=project, current=current, meta=meta, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_issue_details(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _issue_ai_current(request)
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one issue detail first."}, status=400)

    meta = _issue_ai_meta(request)
    try:
        suggestions = _issue_add_detail_suggestions(project=project, current=current, meta=meta, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def brainstorm_page(request, slug: str, issue_pk, pk):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    _get_page_for_issue(issue, pk)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _page_ai_current(request)
    meta = _page_ai_meta(request)
    try:
        suggestions = _page_brainstorm_suggestions(project=project, issue=issue, current=current, meta=meta, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_page_details(request, slug: str, issue_pk, pk):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    _get_page_for_issue(issue, pk)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _page_ai_current(request)
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one page detail first."}, status=400)

    meta = _page_ai_meta(request)
    try:
        suggestions = _page_add_detail_suggestions(project=project, issue=issue, current=current, meta=meta, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


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
