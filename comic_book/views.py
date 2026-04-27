from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Prefetch
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from main.billing import billing_enabled, user_has_active_plan
from main.llm import call_llm, edit_image_data_url, generate_image_data_url
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
COMIC_BIBLE_AI_FIELDS = [
    "premise",
    "world_rules",
    "visual_rules",
    "continuity_rules",
    "cast_notes",
]
COMIC_BIBLE_AI_APPEND_FIELDS = set(COMIC_BIBLE_AI_FIELDS)
PAGE_AI_FIELDS = [
    "title",
    "summary",
    "page_turn_hook",
    "notes",
]
PAGE_AI_APPEND_FIELDS = {"summary", "page_turn_hook", "notes"}
CHARACTER_AI_FIELDS = [
    "name",
    "role",
    "age",
    "gender",
    "description",
    "costume_notes",
    "visual_notes",
    "voice_notes",
]
CHARACTER_AI_APPEND_FIELDS = {"description", "costume_notes", "visual_notes", "voice_notes"}
CHARACTER_AI_BULLET_FIELDS = {"description", "costume_notes", "visual_notes", "voice_notes"}
LOCATION_AI_FIELDS = [
    "name",
    "description",
    "visual_notes",
    "continuity_notes",
]
LOCATION_AI_APPEND_FIELDS = {"description", "visual_notes", "continuity_notes"}
LOCATION_AI_BULLET_FIELDS = {"visual_notes", "continuity_notes"}
CANVAS_NODE_AI_FIELDS = [
    "focus",
    "camera_angle",
    "action",
    "mood",
    "lighting_notes",
    "dialogue_space",
    "must_include",
    "must_avoid",
    "style_override",
    "notes",
]
CANVAS_NODE_AI_OUTPUT_FIELDS = CANVAS_NODE_AI_FIELDS + ["characters"]
CANVAS_NODE_AI_APPEND_FIELDS = {
    "action",
    "lighting_notes",
    "must_include",
    "must_avoid",
    "style_override",
    "notes",
}
CANVAS_QUICK_PROMPT_CACHE_SECONDS = 20 * 60
IMAGE_PROMPT_MAX_CHARS = 3800


def _canvas_quick_prompt_cache_key(user_id, node_id, token: str) -> str:
    return f"comic-canvas-quick-prompt:{user_id}:{node_id}:{token}"


def _truncate_text(value, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_prompt_lines(lines: list[str], max_chars: int = IMAGE_PROMPT_MAX_CHARS) -> str:
    prompt = "\n".join(lines).strip()
    if len(prompt) <= max_chars:
        return prompt

    compacted = []
    remaining = max_chars
    for line in lines:
        line_text = str(line or "")
        if line_text == "":
            candidate = ""
        else:
            candidate = _truncate_text(line_text, min(len(line_text), max(40, remaining - 1)))
        extra = len(candidate) + (1 if compacted else 0)
        if extra > remaining:
            break
        compacted.append(candidate)
        remaining -= extra
    return "\n".join(compacted).strip()


def _project_queryset_for_user(user):
    if getattr(user, "is_superuser", False):
        return ComicProject.objects.all()
    if not getattr(user, "is_authenticated", False):
        return ComicProject.objects.none()
    return ComicProject.objects.filter(owner=user)


def _get_project_for_user(request, slug: str) -> ComicProject:
    return get_object_or_404(_project_queryset_for_user(request.user), slug=slug)


def _anonymous_login_response(view, request):
    if not getattr(request.user, "is_authenticated", False):
        return view.handle_no_permission()
    return None


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


def _is_image_moderation_block(exc: Exception) -> bool:
    error_text = str(exc).lower()
    if "moderation_blocked" in error_text or "safety system" in error_text:
        return True

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "").lower()
                message = str(error.get("message") or "").lower()
                return code == "moderation_blocked" or "safety system" in message

    return False


def _is_provider_billing_limit_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    if "billing_hard_limit_reached" in error_text or "billing hard limit" in error_text:
        return True

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "").lower()
                message = str(error.get("message") or "").lower()
                error_type = str(error.get("type") or "").lower()
                return (
                    code == "billing_hard_limit_reached"
                    or "billing hard limit" in message
                    or error_type == "billing_limit_user_error"
                )

    return False


def _json_image_moderation_error() -> JsonResponse:
    return JsonResponse(
        {
            "ok": False,
            "error": "Image generation was blocked by the safety system. Revise the canvas brief to avoid graphic violence, explicit sexual content, real-person likenesses, or other sensitive details, then try again.",
        },
        status=400,
    )


def _json_provider_billing_limit_error() -> JsonResponse:
    return JsonResponse(
        {
            "ok": False,
            "error": "OpenAI image generation billing hard limit has been reached. Increase the OpenAI project billing limit or switch OPENAI_API_KEY to a project with available image budget.",
        },
        status=400,
    )


def _json_image_generation_error(error: Exception) -> JsonResponse:
    message = str(error or "").strip()
    if "Error code:" in message:
        message = message.split("Error code:", 1)[-1].strip()
    if len(message) > 260:
        message = message[:257].rstrip() + "..."
    if not message:
        message = "The image provider rejected the request."
    _log_exception("Comic book image generation failed.")
    return JsonResponse({"ok": False, "error": f"Image generation failed: {message}"}, status=400)


def _extract_json_object(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "{}"
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response did not contain a JSON object.")
    return text[start : end + 1]


def _call_llm_json_object(*, prompt: str, model_name: str, params: dict, image_data_url: str = "") -> dict:
    call_kwargs = {"prompt": prompt, "model_name": model_name, "params": params}
    image_data_url = (image_data_url or "").strip()
    if image_data_url:
        call_kwargs["image_data_url"] = image_data_url
    result = call_llm(**call_kwargs)
    raw_text = (result.text or "").strip()
    data = json.loads(_extract_json_object(raw_text) if raw_text else "{}")
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object.")
    return data


def _image_data_url_from_upload(uploaded_file) -> str:
    if not uploaded_file:
        return ""
    content_type = (getattr(uploaded_file, "content_type", "") or "").strip().lower()
    if not content_type.startswith("image/"):
        return ""
    encoded = base64.b64encode(uploaded_file.read()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _location_image_data_url_from_request(request, *, fallback: str = "") -> str:
    posted_data_url = (request.POST.get("image_data_url") or "").strip()
    if posted_data_url:
        return posted_data_url
    uploaded_data_url = _image_data_url_from_upload(request.FILES.get("image_upload"))
    if uploaded_data_url:
        return uploaded_data_url
    return (fallback or "").strip()


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


def _normalize_bullet_block(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")

    def clean_line(value: str) -> str:
        cleaned = str(value or "").replace("[", "").replace("]", "").replace("'", "").strip()
        return cleaned

    if "- " not in normalized:
        lines = [clean_line(line) for line in normalized.split("\n") if clean_line(line)]
        return "\n".join(f"- {line}" for line in lines)

    bullet_lines = []
    for chunk in normalized.split("\n"):
        chunk = clean_line(chunk)
        if not chunk:
            continue
        if "- " not in chunk:
            bullet_lines.append(f"- {chunk}")
            continue

        parts = chunk.split("- ")
        prefix = clean_line(parts[0])
        if prefix:
            bullet_lines.append(f"- {prefix}")
        for part in parts[1:]:
            item = clean_line(part)
            if item:
                bullet_lines.append(f"- {item}")

    return "\n".join(bullet_lines)


def _truncate_ai_context(value: str, *, max_length: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    truncated = text[: max_length - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (truncated or text[: max_length - 3]) + "..."


def _append_detail_line(prompt_lines: list[str], label: str, value: str, *, limit: int = 220) -> None:
    text = _truncate_ai_context(value, max_length=limit)
    if text:
        prompt_lines.append(f"{label}: {text}")


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
        age = character.age
        gender = _truncate_ai_context(character.gender, max_length=40)
        description = _truncate_ai_context(character.description, max_length=140)
        if role:
            parts.append(role)
        if age is not None:
            parts.append(f"age {age}")
        if gender:
            parts.append(gender)
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


def _comic_character_image_prompt(*, project: ComicProject, character: ComicCharacter, current: dict[str, str], pose: str) -> str:
    if pose == "frontal":
        pose_label = "straight-on frontal face"
        composition = "show a tight square head-and-shoulders reference: complete head, hairline, chin, neck, and upper shoulders, facing the viewer directly"
        framing = "fill the square canvas with the head and shoulders while keeping the entire hair, scalp, forehead, ears, jaw, chin, neck, and shoulders visible. Leave only a small safety gap above the hair and below the shoulders; no large blank padding, no cropped head. Use a clean neutral background and no props."
        output_goal = "high-clarity character reference art with stable facial structure and consistent styling."
    elif pose == "sideways":
        pose_label = "sideways profile face"
        composition = "show a tight square side-profile head-and-shoulders reference: complete head, hairline, nose, chin, neck, and upper shoulders visible, looking to the side"
        framing = "fill the square canvas with the profile head and shoulders while keeping the entire hair, scalp, forehead, nose, jaw, chin, neck, and shoulders visible. Leave only a small safety gap above the hair and below the shoulders; no large blank padding, no cropped head. Use a clean neutral background and no props."
        output_goal = "high-clarity character reference art with stable facial structure and consistent styling."
    else:
        pose_label = "full-body frontal view"
        composition = "show a full-body character reference from the very top of the hair to the soles of the boots, standing upright and facing forward"
        framing = "use a vertical portrait canvas. The entire figure must be visible from hair to feet, including the complete top of the head. Center the figure and scale it down if needed so nothing is cropped; leave only a narrow safety gap above the hair and below the feet, with no large blank padding. Use a clean neutral background and no props."
        output_goal = "high-clarity full-body character reference art with readable silhouette, costume, proportions, and consistent styling."
    prompt_lines = [
        "Create a polished comic-book character reference portrait.",
        "The image must match the project's established comic style, rendering language, and tone.",
        "This is a character design reference, not a photoreal portrait.",
        f"Pose target: {pose_label}.",
        f"Composition: exactly one person only, {composition}.",
        "Subject count: a single face only. Never show two people, duplicate faces, mirrored faces, reflections, split views, or multiple angles in one image.",
        "Framing: " + framing,
        "Expression: neutral and readable, suitable for a design sheet.",
        "Output goal: " + output_goal,
        "No text, no logos, no watermarks, no speech balloons, no border labels.",
        "",
        "Project style context:",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "",
            "Character details:",
        ]
    )
    _append_detail_line(prompt_lines, "Name", current.get("name") or character.name, limit=80)
    _append_detail_line(prompt_lines, "Role", current.get("role") or character.role, limit=80)
    age_value = current.get("age")
    if age_value in (None, ""):
        age_value = character.age
    if age_value not in (None, ""):
        prompt_lines.append(f"Age: {age_value}")
    _append_detail_line(prompt_lines, "Gender", current.get("gender") or character.gender, limit=80)
    _append_detail_line(prompt_lines, "Description", current.get("description") or character.description)
    _append_detail_line(prompt_lines, "Costume notes", current.get("costume_notes") or character.costume_notes)
    _append_detail_line(prompt_lines, "Visual notes", current.get("visual_notes") or character.visual_notes)
    _append_detail_line(prompt_lines, "Voice notes", current.get("voice_notes") or character.voice_notes)
    return "\n".join(prompt_lines).strip()


def _comic_location_image_prompt(*, project: ComicProject, current: dict[str, str]) -> str:
    prompt_lines = [
        "Create a polished comic-book establishing shot of a fictional location.",
        "Composition: wide environmental view suitable as a reusable location reference.",
        "The image must match the project's established comic style, rendering language, and tone.",
        "No text, captions, logos, speech bubbles, signage text, or watermarks.",
        "Avoid making characters the focus; the place itself must be the subject.",
        "",
        "Project style context:",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.extend(
        [
            "",
            "Location details:",
        ]
    )
    _append_detail_line(prompt_lines, "Name", current.get("name"), limit=100)
    _append_detail_line(prompt_lines, "Description", current.get("description"))
    _append_detail_line(prompt_lines, "Visual notes", current.get("visual_notes"))
    _append_detail_line(prompt_lines, "Continuity notes", current.get("continuity_notes"))
    return "\n".join(prompt_lines).strip()


def _comic_canvas_node_image_prompt(*, project: ComicProject, issue: ComicIssue, page: ComicPage, node: ComicCanvasNode) -> str:
    _sync_canvas_node_from_layout(node)
    characters = list(node.characters.order_by("name"))
    location = node.location
    shot_type = node.get_shot_type_display()

    prompt_lines = [
        "Create a polished comic-book canvas image.",
        "This image is one canvas/panel inside a comic page, not a full page.",
        "The image must match the project's established comic style, rendering language, and tone.",
        "No text, captions, logos, speech bubbles, signage text, watermarks, panel borders, or UI.",
        "Respect the canvas brief exactly; do not add unrelated characters, props, or story events.",
        "",
        "Canvas brief:",
        f"Canvas key: {node.canvas_key}",
        f"Canvas type: {node.get_node_type_display()}",
        f"Shot type: {shot_type}",
    ]
    if node.split_direction:
        prompt_lines.append(f"Split direction: {node.get_split_direction_display()}")
    if node.split_ratio:
        prompt_lines.append(f"Split ratio: {node.split_ratio}")

    for label, value in [
        ("Focus", node.focus),
        ("Camera angle", node.camera_angle),
        ("Action", node.action),
        ("Mood", node.mood),
        ("Lighting notes", node.lighting_notes),
        ("Dialogue space", node.dialogue_space),
        ("Must include", node.must_include),
        ("Must avoid", node.must_avoid),
        ("Style override", node.style_override),
        ("Canvas notes", node.notes),
    ]:
        _append_detail_line(prompt_lines, label, value, limit=240)

    _append_detail_line(prompt_lines, "Primary issue notes", issue.notes, limit=320)

    if location:
        prompt_lines.extend(["", "Selected location details:"])
        _append_detail_line(prompt_lines, "Location name", location.name, limit=100)
        _append_detail_line(prompt_lines, "Location description", location.description, limit=180)
        _append_detail_line(prompt_lines, "Location visual notes", location.visual_notes, limit=180)
        _append_detail_line(prompt_lines, "Location continuity notes", location.continuity_notes, limit=120)

    if characters:
        prompt_lines.extend(["", "Selected character details:"])
        for character in characters:
            prompt_lines.append(f"- Character: {character.name}")
            _append_detail_line(prompt_lines, "  Role", character.role, limit=120)
            if character.age is not None:
                prompt_lines.append(f"  Age: {character.age}")
            _append_detail_line(prompt_lines, "  Gender", character.gender, limit=80)
            _append_detail_line(prompt_lines, "  Description", character.description, limit=160)
            _append_detail_line(prompt_lines, "  Costume notes", character.costume_notes, limit=140)
            _append_detail_line(prompt_lines, "  Visual notes", character.visual_notes, limit=140)

    context_lines = []
    project_lines = _comic_project_context_lines(project)
    if project_lines:
        context_lines.extend(["", "Project style context:"])
        context_lines.extend(project_lines)
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        context_lines.append("")
        context_lines.extend(bible_lines)
    issue_lines = _comic_issue_context_lines(issue, include_notes=False)
    if issue_lines:
        context_lines.append("")
        context_lines.extend(issue_lines)
    page_lines = _comic_page_context_lines(page)
    if page_lines:
        context_lines.append("")
        context_lines.extend(page_lines)

    for line in context_lines:
        if len("\n".join(prompt_lines + [line]).strip()) <= IMAGE_PROMPT_MAX_CHARS:
            prompt_lines.append(line)

    return _compact_prompt_lines(prompt_lines)


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


def _canvas_node_ai_current(request) -> dict[str, str]:
    return {field: (request.POST.get(field) or "").strip() for field in CANVAS_NODE_AI_FIELDS}


def _canvas_node_ai_meta(request, node: ComicCanvasNode) -> dict[str, str]:
    return {
        "canvas_key": node.canvas_key,
        "node_type": node.get_node_type_display(),
        "split_direction": node.get_split_direction_display() if node.split_direction else "",
        "split_ratio": str(node.split_ratio or ""),
        "shot_type": (request.POST.get("shot_type") or node.shot_type or "").strip(),
        "location": (request.POST.get("location_label") or "").strip(),
        "characters": (request.POST.get("characters_label") or "").strip(),
        "available_characters": ", ".join(ComicCharacter.objects.filter(project=node.page.issue.project).order_by("name").values_list("name", flat=True)),
    }


def _comic_bible_ai_current(request) -> dict[str, str]:
    return {field: (request.POST.get(field) or "").strip() for field in COMIC_BIBLE_AI_FIELDS}


def _comic_bible_brainstorm_suggestions(*, project: ComicProject, current: dict[str, str], user) -> dict[str, str]:
    empty_fields = [field for field in COMIC_BIBLE_AI_FIELDS if not current.get(field)]
    if not empty_fields:
        return {}

    prompt_lines = [
        "You are a comic-book series bible development assistant.",
        "Goal: fill ONLY the currently-empty bible fields so they create a clear shared foundation for future issues, pages, and character work.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(COMIC_BIBLE_AI_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- premise: 2-4 sentences defining the core comic concept and dramatic engine.",
        "- world_rules: 3-6 short lines describing the setting logic, constraints, and recurring world assumptions.",
        "- visual_rules: 3-6 short lines describing the visual language, design guardrails, and recurring art direction signals.",
        "- continuity_rules: 3-6 short lines covering canon, chronology, power limits, and consistency requirements.",
        "- cast_notes: 3-6 short lines clarifying ensemble function, relationship tension, and recurring role boundaries.",
        "- Keep the bible specific, production-friendly, and aligned with the existing project details.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.append("Saved series bible context:")
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
            "Current comic bible fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 700},
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


def _comic_bible_add_detail_suggestions(*, project: ComicProject, current: dict[str, str], user) -> dict[str, str]:
    prompt_lines = [
        "You are a comic-book series bible development assistant.",
        "Goal: add fresh detail to the current comic bible without repeating or contradicting what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(COMIC_BIBLE_AI_FIELDS),
        "- premise, world_rules, visual_rules, continuity_rules, and cast_notes: return ONLY additive text to append, not a full rewrite.",
        "- Keep additions aligned with the existing project direction, cast, and locations.",
        "- Focus on details that improve scripting clarity, art consistency, and canon control.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.append("Saved series bible context:")
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
            "Current comic bible fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 700},
    )

    filtered = {}
    for key, value in data.items():
        if key not in COMIC_BIBLE_AI_FIELDS:
            continue
        existing = current.get(key, "")
        text = str(value or "").strip()
        if not text:
            continue
        if key in COMIC_BIBLE_AI_APPEND_FIELDS:
            text = _dedupe_appended_text(existing, text)
        if not text:
            continue
        filtered[key] = text
    return filtered


def _character_ai_current(request) -> dict[str, str]:
    current = {field: (request.POST.get(field) or "").strip() for field in CHARACTER_AI_FIELDS}
    age_value = current.get("age", "")
    if age_value:
        try:
            age_int = int(age_value)
        except Exception:
            current["age"] = ""
        else:
            current["age"] = str(age_int) if 0 <= age_int <= 130 else ""
    return current


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


def _comic_issue_context_lines(issue: ComicIssue, *, include_notes: bool = True) -> list[str]:
    lines = [f"Issue number: {issue.number}", "Issue title: " + (issue.title or "")]
    detail_fields = [
        ("Issue summary", issue.summary),
        ("Issue theme", issue.theme),
        ("Issue opening hook", issue.opening_hook),
        ("Issue closing hook", issue.closing_hook),
    ]
    if include_notes:
        detail_fields.append(("Issue notes", issue.notes))
    for label, value in detail_fields:
        text = _truncate_ai_context(value)
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _comic_page_context_lines(page: ComicPage) -> list[str]:
    lines = [f"Page number: {page.page_number}", "Page title: " + (page.title or "")]
    for label, value in [
        ("Page summary", page.summary),
        ("Page role", page.get_page_role_display()),
        ("Layout type", page.get_layout_type_display()),
        ("Page-turn hook", page.page_turn_hook),
        ("Page notes", page.notes),
    ]:
        text = _truncate_ai_context(value)
        if text:
            lines.append(f"{label}: {text}")
    return lines


def _canvas_character_suggestions(value, *, project: ComicProject, selected_labels: str = "") -> list[str]:
    allowed = {
        name.casefold(): name
        for name in ComicCharacter.objects.filter(project=project).order_by("name").values_list("name", flat=True)
        if name
    }
    if not allowed:
        return []

    selected = {
        item.strip().casefold()
        for item in str(selected_labels or "").split(",")
        if item.strip()
    }

    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").replace("\n", ",").split(",")

    names = []
    seen = set()
    for item in raw_items:
        key = str(item or "").strip().casefold()
        if not key or key in selected or key in seen or key not in allowed:
            continue
        seen.add(key)
        names.append(allowed[key])
    return names


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


def _canvas_node_brainstorm_suggestions(
    *,
    project: ComicProject,
    issue: ComicIssue,
    page: ComicPage,
    node: ComicCanvasNode,
    current: dict[str, str],
    meta: dict[str, str],
    user,
) -> dict[str, str]:
    empty_fields = [field for field in CANVAS_NODE_AI_FIELDS if not current.get(field)]
    selected_characters = meta.get("characters") or ""
    if not selected_characters:
        empty_fields.append("characters")
    if not empty_fields:
        return {}

    prompt_lines = [
        "You are a comic-book canvas brief assistant.",
        "Goal: fill ONLY the currently-empty canvas brief fields so this canvas has clear framing, staging, and generation notes.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(CANVAS_NODE_AI_OUTPUT_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- focus: a short phrase naming the visual subject.",
        "- characters: an array of exact names selected from Available characters only. Omit characters if none fit.",
        "- camera_angle, mood, and dialogue_space: concise production notes.",
        "- action: 1-3 sentences describing what is visibly happening in this canvas.",
        "- lighting_notes, must_include, must_avoid, style_override, and notes: 2-5 short concrete lines each.",
        "- Keep additions aligned with the project, issue, page, selected shot type, location, and characters.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.append("")
    prompt_lines.extend(_comic_issue_context_lines(issue))
    prompt_lines.append("")
    prompt_lines.extend(_comic_page_context_lines(page))
    prompt_lines.extend(
        [
            "",
            "Canvas metadata:",
            "Canvas key: " + (meta.get("canvas_key") or node.canvas_key),
            "Canvas type: " + (meta.get("node_type") or ""),
            "Split direction: " + (meta.get("split_direction") or ""),
            "Split ratio: " + (meta.get("split_ratio") or ""),
            "Shot type: " + (meta.get("shot_type") or ""),
            "Selected location: " + (meta.get("location") or ""),
            "Selected characters: " + (meta.get("characters") or ""),
            "Available characters: " + (meta.get("available_characters") or ""),
            "",
            "Current canvas fields (JSON):",
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
        if key == "characters":
            if key not in empty_fields:
                continue
            names = _canvas_character_suggestions(value, project=project, selected_labels=selected_characters)
            if names:
                filtered[key] = names
            continue
        if key not in empty_fields:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        filtered[key] = text
    return filtered


def _canvas_node_add_detail_suggestions(
    *,
    project: ComicProject,
    issue: ComicIssue,
    page: ComicPage,
    node: ComicCanvasNode,
    current: dict[str, str],
    meta: dict[str, str],
    user,
) -> dict[str, str]:
    prompt_lines = [
        "You are a comic-book canvas brief assistant.",
        "Goal: add fresh detail to the current canvas brief without repeating or contradicting what is already there.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(CANVAS_NODE_AI_OUTPUT_FIELDS),
        "- characters: an array of exact names selected from Available characters only. Include only names not already selected.",
        "- focus, camera_angle, mood, and dialogue_space: only include them if they are currently blank.",
        "- action, lighting_notes, must_include, must_avoid, style_override, and notes: return ONLY additive text to append, not a rewrite.",
        "- Keep additions visually actionable for comic layout, staging, and image generation.",
        "- Respect the page purpose, selected shot type, selected location, and selected characters.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    prompt_lines.append("")
    prompt_lines.extend(_comic_issue_context_lines(issue))
    prompt_lines.append("")
    prompt_lines.extend(_comic_page_context_lines(page))
    prompt_lines.extend(
        [
            "",
            "Canvas metadata:",
            "Canvas key: " + (meta.get("canvas_key") or node.canvas_key),
            "Canvas type: " + (meta.get("node_type") or ""),
            "Split direction: " + (meta.get("split_direction") or ""),
            "Split ratio: " + (meta.get("split_ratio") or ""),
            "Shot type: " + (meta.get("shot_type") or ""),
            "Selected location: " + (meta.get("location") or ""),
            "Selected characters: " + (meta.get("characters") or ""),
            "Available characters: " + (meta.get("available_characters") or ""),
            "",
            "Current canvas fields (JSON):",
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
        if key == "characters":
            names = _canvas_character_suggestions(value, project=project, selected_labels=meta.get("characters") or "")
            if names:
                filtered[key] = names
            continue
        if key not in CANVAS_NODE_AI_FIELDS:
            continue
        existing = current.get(key, "")
        if key not in CANVAS_NODE_AI_APPEND_FIELDS and existing:
            continue

        text = str(value or "").strip()
        if not text:
            continue

        if key in CANVAS_NODE_AI_APPEND_FIELDS:
            text = _dedupe_appended_text(existing, text)
        if not text:
            continue
        filtered[key] = text
    return filtered


def _character_brainstorm_suggestions(*, project: ComicProject, current: dict[str, str], user) -> dict[str, str]:
    empty_fields = [field for field in CHARACTER_AI_FIELDS if not current.get(field)]
    if not empty_fields:
        return {}

    prompt_lines = [
        "You are a comic-book character development assistant.",
        "Goal: fill ONLY the currently-empty character fields so they fit the comic project's tone, world, and cast.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(CHARACTER_AI_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- name: 2-4 words.",
        "- role: a short label or phrase.",
        "- age: integer only, omit it if unsure.",
        "- gender: a short plain-language label.",
        "- description: 2-5 bullet points covering story function, attitude, and core tension.",
        "- costume_notes, visual_notes, voice_notes: 2-5 bullet points each with concrete, production-friendly detail.",
        "- For description, costume_notes, visual_notes, and voice_notes, each bullet must start with '- '.",
        "- Keep details specific enough to guide scripting and art direction without contradicting the project bible.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    character_lines = _comic_character_context_lines(project)
    if character_lines:
        prompt_lines.append("")
        prompt_lines.extend(character_lines)
    prompt_lines.extend(
        [
            "",
            "Current character fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 550},
    )

    filtered = {}
    for key, value in data.items():
        if key not in empty_fields:
            continue
        if key == "age":
            try:
                age_int = int(value)
            except Exception:
                continue
            if 0 <= age_int <= 130:
                filtered[key] = str(age_int)
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if key in CHARACTER_AI_BULLET_FIELDS:
            text = _normalize_bullet_block(text)
            if not text:
                continue
        filtered[key] = text
    return filtered


def _character_add_detail_suggestions(*, project: ComicProject, current: dict[str, str], user) -> dict[str, str]:
    prompt_lines = [
        "You are a comic-book character development assistant.",
        "Goal: add fresh detail to the current character profile without repeating or contradicting what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(CHARACTER_AI_FIELDS),
        "- name, role, age, and gender: only include them if they are currently blank.",
        "- description, costume_notes, visual_notes, and voice_notes: return ONLY additive bullet points to append, not a rewrite.",
        "- For description, costume_notes, visual_notes, and voice_notes, each added bullet must start with '- '.",
        "- Keep additions aligned with the project's tone, genre, visual rules, and existing cast.",
        "- Make additions concrete and useful for scripting, acting, and visual continuity.",
        "- age: integer only, omit it if unsure.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
    bible_lines = _comic_bible_context_lines(project)
    if bible_lines:
        prompt_lines.append("")
        prompt_lines.extend(bible_lines)
    character_lines = _comic_character_context_lines(project)
    if character_lines:
        prompt_lines.append("")
        prompt_lines.extend(character_lines)
    prompt_lines.extend(
        [
            "",
            "Current character fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 550},
    )

    filtered = {}
    for key, value in data.items():
        if key not in CHARACTER_AI_FIELDS:
            continue
        existing = current.get(key, "")
        if key not in CHARACTER_AI_APPEND_FIELDS and existing:
            continue

        if key == "age":
            try:
                age_int = int(value)
            except Exception:
                continue
            if 0 <= age_int <= 130:
                filtered[key] = str(age_int)
            continue

        text = str(value or "").strip()
        if not text:
            continue

        if key in CHARACTER_AI_BULLET_FIELDS:
            text = _normalize_bullet_block(text)
            if not text:
                continue
        if key in CHARACTER_AI_APPEND_FIELDS:
            text = _dedupe_appended_text(existing, text)
        if not text:
            continue
        filtered[key] = text
    return filtered


def _location_ai_current(request) -> dict[str, str]:
    return {field: (request.POST.get(field) or "").strip() for field in LOCATION_AI_FIELDS}


def _location_ai_image_data_url(request) -> str:
    return _location_image_data_url_from_request(request)


def _location_brainstorm_suggestions(*, project: ComicProject, current: dict[str, str], user, image_data_url: str = "") -> dict[str, str]:
    empty_fields = [field for field in LOCATION_AI_FIELDS if not current.get(field)]
    if not empty_fields:
        return {}

    has_image = bool((image_data_url or "").strip())
    prompt_lines = [
        "You are a comic-book location development assistant.",
        "Goal: fill ONLY the currently-empty location fields so the setting fits the comic project's world, tone, and visual language.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(LOCATION_AI_FIELDS),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- name: 2-5 words.",
        "- description: 2-4 sentences covering story purpose, atmosphere, and recurring use.",
        "- visual_notes and continuity_notes: 2-5 bullet points each with concrete, production-friendly detail.",
        "- For visual_notes and continuity_notes, each bullet must start with '- '.",
        "- Keep details specific enough to guide scripting and art direction without contradicting the series bible.",
        "- If an image is provided, use visible architecture, materials, lighting, geography, signage-free details, and atmosphere as visual evidence.",
        "- Do not invent hard story canon from the image alone; keep image-derived continuity notes as practical visual constraints.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
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
            "Image provided: " + ("yes" if has_image else "no"),
            "Current location fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 550},
        image_data_url=image_data_url,
    )

    filtered = {}
    for key, value in data.items():
        if key not in empty_fields:
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if key in LOCATION_AI_BULLET_FIELDS:
            text = _normalize_bullet_block(text)
            if not text:
                continue
        filtered[key] = text
    return filtered


def _location_add_detail_suggestions(*, project: ComicProject, current: dict[str, str], user, image_data_url: str = "") -> dict[str, str]:
    has_image = bool((image_data_url or "").strip())
    prompt_lines = [
        "You are a comic-book location development assistant.",
        "Goal: add fresh detail to the current location profile without repeating or contradicting what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(LOCATION_AI_FIELDS),
        "- name: only include it if it is currently blank.",
        "- description, visual_notes, and continuity_notes: return ONLY additive text to append, not a rewrite.",
        "- For visual_notes and continuity_notes, each added bullet must start with '- '.",
        "- Keep additions aligned with the project's tone, genre, visual rules, existing cast, and series bible.",
        "- Make additions concrete and useful for scripting, staging, art direction, and continuity.",
        "- If an image is provided, use visible architecture, materials, lighting, geography, signage-free details, and atmosphere as visual evidence.",
        "- Do not invent hard story canon from the image alone; keep image-derived continuity notes as practical visual constraints.",
        "",
    ]
    prompt_lines.extend(_comic_project_context_lines(project))
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
            "Image provided: " + ("yes" if has_image else "no"),
            "Current location fields (JSON):",
            json.dumps(current, ensure_ascii=False),
        ]
    )

    data = _call_llm_json_object(
        prompt="\n".join(prompt_lines).strip(),
        model_name=get_user_text_model(user),
        params={"temperature": 0.7, "max_tokens": 550},
        image_data_url=image_data_url,
    )

    filtered = {}
    for key, value in data.items():
        if key not in LOCATION_AI_FIELDS:
            continue
        existing = current.get(key, "")
        if key not in LOCATION_AI_APPEND_FIELDS and existing:
            continue

        text = str(value or "").strip()
        if not text:
            continue
        if key in LOCATION_AI_BULLET_FIELDS:
            text = _normalize_bullet_block(text)
            if not text:
                continue
        if key in LOCATION_AI_APPEND_FIELDS:
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


def _ensure_unique_canvas_layout_keys(layout) -> dict:
    if not isinstance(layout, dict):
        return {"type": "panel", "canvas_key": "root"}

    max_sequence = 0

    def scan(node) -> None:
        nonlocal max_sequence
        if not isinstance(node, dict):
            return
        key = str(node.get("canvas_key") or "").strip()
        match = re.fullmatch(r"canvas-(\d+)", key)
        if match:
            max_sequence = max(max_sequence, int(match.group(1)))
        for child in node.get("children") or []:
            scan(child)

    scan(layout)
    used = set()

    def next_key() -> str:
        nonlocal max_sequence
        while True:
            max_sequence += 1
            candidate = f"canvas-{max_sequence}"
            if candidate not in used:
                used.add(candidate)
                return candidate

    def normalize(node, *, is_root=False) -> dict:
        if not isinstance(node, dict):
            node = {}
        next_node = dict(node)
        desired_key = str(next_node.get("canvas_key") or "").strip()
        if not desired_key and is_root:
            desired_key = "root"
        if not desired_key or desired_key in used:
            desired_key = next_key()
        else:
            used.add(desired_key)
        next_node["canvas_key"] = desired_key

        if str(next_node.get("type") or "").strip() == "split":
            children = [child for child in (next_node.get("children") or []) if isinstance(child, dict)]
            next_node["children"] = [normalize(child) for child in children[:2]]
        else:
            next_node.pop("children", None)
        return next_node

    return normalize(layout, is_root=True)


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


@method_decorator(ensure_csrf_cookie, name="dispatch")
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


@login_required
@require_POST
def swap_issues(request, slug: str):
    project = _get_project_for_user(request, slug)
    source_id = (request.POST.get("issue_id") or "").strip()
    target_id = (request.POST.get("target_issue_id") or "").strip()
    if not source_id or not target_id or source_id == target_id:
        return JsonResponse({"ok": False, "error": "Choose two different issues to swap."}, status=400)

    source = _get_issue_for_project(project, source_id)
    target = _get_issue_for_project(project, target_id)
    source_number = source.number
    target_number = target.number
    if source_number == target_number:
        return JsonResponse({"ok": True})

    with transaction.atomic():
        temp_number = (project.issues.aggregate(max_number=Max("number"))["max_number"] or 0) + 1
        source.number = temp_number
        source.save(update_fields=["number", "updated_at"])
        target.number = source_number
        target.save(update_fields=["number", "updated_at"])
        source.number = target_number
        source.save(update_fields=["number", "updated_at"])

    return JsonResponse({"ok": True})


class ComicBibleUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicBibleForm
    template_name = "comic_book/bible_form.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        bible, _created = ComicBible.objects.get_or_create(project=self.project)
        return bible

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Comic bible saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("comic_book:bible-edit", kwargs={"slug": self.project.slug})


@login_required
@require_POST
def brainstorm_bible(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _comic_bible_ai_current(request)
    try:
        suggestions = _comic_bible_brainstorm_suggestions(project=project, current=current, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_bible_details(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _comic_bible_ai_current(request)
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one bible detail first."}, status=400)

    try:
        suggestions = _comic_bible_add_detail_suggestions(project=project, current=current, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


class ComicCharacterListView(LoginRequiredMixin, ListView):
    model = ComicCharacter
    template_name = "comic_book/character_list.html"
    context_object_name = "characters"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        raw_age = (self.request.POST.get("age") or "").strip()
        form.instance.age = int(raw_age) if raw_age.isdigit() else None
        form.instance.gender = (self.request.POST.get("gender") or "").strip()
        form.instance.frontal_face_image_data_url = (self.request.POST.get("frontal_face_image_data_url") or "").strip()
        form.instance.sideways_face_image_data_url = (self.request.POST.get("sideways_face_image_data_url") or "").strip()
        form.instance.full_body_image_data_url = (self.request.POST.get("full_body_image_data_url") or "").strip()
        messages.success(self.request, "Comic character created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:character-edit", kwargs={"slug": self.project.slug, "pk": self.object.pk})


class ComicCharacterUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicCharacterForm
    template_name = "comic_book/character_form.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicCharacter.objects.filter(project=self.project)

    def form_valid(self, form):
        raw_age = (self.request.POST.get("age") or "").strip()
        form.instance.age = int(raw_age) if raw_age.isdigit() else None
        form.instance.gender = (self.request.POST.get("gender") or "").strip()
        form.instance.frontal_face_image_data_url = (
            self.request.POST.get("frontal_face_image_data_url") or form.instance.frontal_face_image_data_url or ""
        ).strip()
        form.instance.sideways_face_image_data_url = (
            self.request.POST.get("sideways_face_image_data_url") or form.instance.sideways_face_image_data_url or ""
        ).strip()
        form.instance.full_body_image_data_url = (
            self.request.POST.get("full_body_image_data_url") or form.instance.full_body_image_data_url or ""
        ).strip()
        messages.success(self.request, "Comic character saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:character-edit", kwargs={"slug": self.project.slug, "pk": self.object.pk})


@login_required
@require_POST
def preview_character_faces(request, slug: str):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    current = {
        "name": (request.POST.get("name") or "").strip(),
        "role": (request.POST.get("role") or "").strip(),
        "age": (request.POST.get("age") or "").strip(),
        "gender": (request.POST.get("gender") or "").strip(),
        "description": (request.POST.get("description") or "").strip(),
        "costume_notes": (request.POST.get("costume_notes") or "").strip(),
        "visual_notes": (request.POST.get("visual_notes") or "").strip(),
        "voice_notes": (request.POST.get("voice_notes") or "").strip(),
    }
    if not current["name"]:
        return JsonResponse({"ok": False, "error": "Character name is required."}, status=400)

    temp_character = ComicCharacter(project=project, **current)
    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    try:
        frontal_url = generate_image_data_url(
            prompt=_comic_character_image_prompt(project=project, character=temp_character, current=current, pose="frontal"),
            model_name=model_name,
            size="1024x1024",
        )
        sideways_url = generate_image_data_url(
            prompt=_comic_character_image_prompt(project=project, character=temp_character, current=current, pose="sideways"),
            model_name=model_name,
            size="1024x1024",
        )
    except Exception:
        return _json_internal_error()

    return JsonResponse({"ok": True, "frontal_face_image_url": frontal_url, "sideways_face_image_url": sideways_url})


@login_required
@require_POST
def preview_character_full_body(request, slug: str):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    current = {
        "name": (request.POST.get("name") or "").strip(),
        "role": (request.POST.get("role") or "").strip(),
        "age": (request.POST.get("age") or "").strip(),
        "gender": (request.POST.get("gender") or "").strip(),
        "description": (request.POST.get("description") or "").strip(),
        "costume_notes": (request.POST.get("costume_notes") or "").strip(),
        "visual_notes": (request.POST.get("visual_notes") or "").strip(),
        "voice_notes": (request.POST.get("voice_notes") or "").strip(),
    }
    if not current["name"]:
        return JsonResponse({"ok": False, "error": "Character name is required."}, status=400)

    temp_character = ComicCharacter(project=project, **current)
    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    prompt = _comic_character_image_prompt(project=project, character=temp_character, current=current, pose="full_body")
    try:
        body_url = generate_image_data_url(prompt=prompt, model_name=model_name, size="1024x1536")
    except Exception:
        return _json_internal_error()

    return JsonResponse({"ok": True, "full_body_image_url": body_url})


@login_required
@require_POST
def generate_character_faces(request, slug: str, pk):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    character = get_object_or_404(ComicCharacter, id=pk, project=project)
    current = {
        "name": (request.POST.get("name") or character.name or "").strip(),
        "role": (request.POST.get("role") or character.role or "").strip(),
        "age": (request.POST.get("age") or ("" if character.age is None else str(character.age))).strip(),
        "gender": (request.POST.get("gender") or character.gender or "").strip(),
        "description": (request.POST.get("description") or character.description or "").strip(),
        "costume_notes": (request.POST.get("costume_notes") or character.costume_notes or "").strip(),
        "visual_notes": (request.POST.get("visual_notes") or character.visual_notes or "").strip(),
        "voice_notes": (request.POST.get("voice_notes") or character.voice_notes or "").strip(),
    }
    if not current["name"]:
        return JsonResponse({"ok": False, "error": "Character name is required."}, status=400)

    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    try:
        frontal_url = generate_image_data_url(
            prompt=_comic_character_image_prompt(project=project, character=character, current=current, pose="frontal"),
            model_name=model_name,
            size="1024x1024",
        )
        sideways_url = generate_image_data_url(
            prompt=_comic_character_image_prompt(project=project, character=character, current=current, pose="sideways"),
            model_name=model_name,
            size="1024x1024",
        )
    except Exception:
        return _json_internal_error()

    character.frontal_face_image_data_url = frontal_url
    character.sideways_face_image_data_url = sideways_url
    character.save(update_fields=["frontal_face_image_data_url", "sideways_face_image_data_url", "updated_at"])
    return JsonResponse({"ok": True, "frontal_face_image_url": frontal_url, "sideways_face_image_url": sideways_url})


@login_required
@require_POST
def generate_character_full_body(request, slug: str, pk):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    character = get_object_or_404(ComicCharacter, id=pk, project=project)
    current = {
        "name": (request.POST.get("name") or character.name or "").strip(),
        "role": (request.POST.get("role") or character.role or "").strip(),
        "age": (request.POST.get("age") or ("" if character.age is None else str(character.age))).strip(),
        "gender": (request.POST.get("gender") or character.gender or "").strip(),
        "description": (request.POST.get("description") or character.description or "").strip(),
        "costume_notes": (request.POST.get("costume_notes") or character.costume_notes or "").strip(),
        "visual_notes": (request.POST.get("visual_notes") or character.visual_notes or "").strip(),
        "voice_notes": (request.POST.get("voice_notes") or character.voice_notes or "").strip(),
    }
    if not current["name"]:
        return JsonResponse({"ok": False, "error": "Character name is required."}, status=400)

    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    prompt = _comic_character_image_prompt(project=project, character=character, current=current, pose="full_body")
    try:
        body_url = generate_image_data_url(prompt=prompt, model_name=model_name, size="1024x1536")
    except Exception:
        return _json_internal_error()

    character.full_body_image_data_url = body_url
    character.save(update_fields=["full_body_image_data_url", "updated_at"])
    return JsonResponse({"ok": True, "full_body_image_url": body_url})


@login_required
@require_POST
def preview_location_image(request, slug: str):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    current = _location_ai_current(request)
    if not current.get("name"):
        return JsonResponse({"ok": False, "error": "Location name is required."}, status=400)

    prompt = _comic_location_image_prompt(project=project, current=current)
    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    try:
        image_url = generate_image_data_url(prompt=prompt, model_name=model_name, size="1024x1024")
    except Exception:
        return _json_internal_error()

    return JsonResponse({"ok": True, "image_url": image_url})


class ComicCharacterDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.project = self.project
        form.instance.image_data_url = _location_image_data_url_from_request(self.request)
        messages.success(self.request, "Comic location created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:location-list", kwargs={"slug": self.project.slug})


class ComicLocationUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicLocationForm
    template_name = "comic_book/location_form.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicLocation.objects.filter(project=self.project)

    def form_valid(self, form):
        form.instance.image_data_url = _location_image_data_url_from_request(
            self.request,
            fallback=form.instance.image_data_url,
        )
        messages.success(self.request, "Comic location saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:location-edit", kwargs={"slug": self.project.slug, "pk": self.object.pk})


class ComicLocationDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("page_number", self.issue.pages.count() + 1)
        return initial

    def form_valid(self, form):
        form.instance.issue = self.issue
        form.instance.canvas_layout = _ensure_unique_canvas_layout_keys(form.instance.canvas_layout)
        response = super().form_valid(form)
        _renumber_issue_pages(self.issue)
        messages.success(self.request, "Page created.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["canvas_image_map"] = {}
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:page-edit", kwargs={"slug": self.project.slug, "issue_pk": self.issue.pk, "pk": self.object.pk})


class ComicPageUpdateView(LoginRequiredMixin, UpdateView):
    form_class = ComicPageForm
    template_name = "comic_book/page_form.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
        self.project = _get_project_for_user(request, kwargs["slug"])
        self.issue = _get_issue_for_project(self.project, kwargs["issue_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ComicPage.objects.filter(issue=self.issue)

    def form_valid(self, form):
        form.instance.canvas_layout = _ensure_unique_canvas_layout_keys(form.instance.canvas_layout)
        response = super().form_valid(form)
        _renumber_issue_pages(self.issue)
        if self.request.headers.get("x-comic-page-autosave") == "true":
            return JsonResponse({"ok": True})
        return response

    def form_invalid(self, form):
        if self.request.headers.get("x-comic-page-autosave") == "true":
            return JsonResponse({"ok": False, "error": "Page autosave failed."}, status=400)
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["canvas_image_map"] = {
            node.canvas_key: node.image_data_url
            for node in self.object.canvas_nodes.exclude(image_data_url="").only("canvas_key", "image_data_url")
        }
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse("comic_book:page-edit", kwargs={"slug": self.project.slug, "issue_pk": self.issue.pk, "pk": self.object.pk})


class ComicPageDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "comic_book/confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        if response := _anonymous_login_response(self, request):
            return response
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
        form = ctx.get("form")
        selected_character_ids = []
        if form is not None:
            raw_value = form["characters"].value()
            if raw_value:
                selected_character_ids = [str(value) for value in raw_value]
        ctx["project"] = self.project
        ctx["issue"] = self.issue
        ctx["page"] = self.page
        ctx["canvas_node"] = canvas_node
        ctx["character_list"] = self.project.characters.order_by("name")
        ctx["selected_character_ids"] = selected_character_ids
        ctx.update(_ai_context_for_request(self.request))
        return ctx

    def get_success_url(self):
        return reverse(
            "comic_book:canvas-node-edit",
            kwargs={
                "slug": self.project.slug,
                "issue_pk": self.issue.pk,
                "page_pk": self.page.pk,
                "canvas_key": self.canvas_key,
            },
        )


@login_required
@require_POST
def brainstorm_character(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _character_ai_current(request)
    try:
        suggestions = _character_brainstorm_suggestions(project=project, current=current, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_character_details(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _character_ai_current(request)
    if not current.get("name"):
        return JsonResponse({"ok": False, "error": "Add a character name first."}, status=400)

    try:
        suggestions = _character_add_detail_suggestions(project=project, current=current, user=request.user)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def brainstorm_location(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _location_ai_current(request)
    image_data_url = _location_ai_image_data_url(request)
    try:
        suggestions = _location_brainstorm_suggestions(project=project, current=current, user=request.user, image_data_url=image_data_url)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_location_details(request, slug: str):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _location_ai_current(request)
    if not current.get("name"):
        return JsonResponse({"ok": False, "error": "Add a location name first."}, status=400)

    image_data_url = _location_ai_image_data_url(request)
    try:
        suggestions = _location_add_detail_suggestions(project=project, current=current, user=request.user, image_data_url=image_data_url)
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


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
def brainstorm_canvas_node(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _canvas_node_ai_current(request)
    meta = _canvas_node_ai_meta(request, node)
    try:
        suggestions = _canvas_node_brainstorm_suggestions(
            project=project,
            issue=issue,
            page=page,
            node=node,
            current=current,
            meta=meta,
            user=request.user,
        )
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def add_canvas_node_details(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    current = _canvas_node_ai_current(request)
    if not any(current.values()) and not (request.POST.get("characters_label") or "").strip():
        return JsonResponse({"ok": False, "error": "Add at least one canvas brief detail first."}, status=400)

    meta = _canvas_node_ai_meta(request, node)
    try:
        suggestions = _canvas_node_add_detail_suggestions(
            project=project,
            issue=issue,
            page=page,
            node=node,
            current=current,
            meta=meta,
            user=request.user,
        )
        return JsonResponse({"ok": True, "suggestions": suggestions})
    except Exception:
        return _json_internal_error()


@login_required
@require_POST
def generate_canvas_node_image(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)

    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    prompt = _comic_canvas_node_image_prompt(project=project, issue=issue, page=page, node=node)
    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    try:
        image_url = generate_image_data_url(prompt=prompt, model_name=model_name, size="1024x1024")
    except Exception as error:
        node.image_status = ComicCanvasNode.ImageStatus.FAILED
        node.save(update_fields=["image_status", "updated_at"])
        if _is_image_moderation_block(error):
            return _json_image_moderation_error()
        if _is_provider_billing_limit_error(error):
            return _json_provider_billing_limit_error()
        return _json_image_generation_error(error)

    node.image_prompt = prompt
    node.image_data_url = image_url
    node.image_status = ComicCanvasNode.ImageStatus.READY
    node.last_generated_at = timezone.now()
    node.save(update_fields=["image_prompt", "image_data_url", "image_status", "last_generated_at", "updated_at"])
    return JsonResponse({"ok": True, "image_url": image_url})


@login_required
@require_POST
def quick_prompt_canvas_node_image(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)

    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image editing is not configured."}, status=400)
    blocked = _subscription_required_response(request)
    if blocked is not None:
        return blocked

    user_prompt = (request.POST.get("prompt") or "").strip()
    if not user_prompt:
        return JsonResponse({"ok": False, "error": "Write what to change first."}, status=400)

    uploaded_reference_image = _image_data_url_from_upload(request.FILES.get("reference_image_upload"))
    reference_image = uploaded_reference_image or (request.POST.get("reference_image_data_url") or "").strip() or node.image_data_url
    if not reference_image:
        return JsonResponse({"ok": False, "error": "Generate this canvas image before using Quick Prompt."}, status=400)

    edit_prompt = "\n".join(
        [
            "Edit only the provided image.",
            "Use the image itself as the only visual and story reference.",
            "Make the smallest localized edit that satisfies the requested change.",
            "Preserve the original composition, characters, style, color grading, exact palette, saturation, contrast, brightness, lighting, linework, texture, sharpness, framing, and speech bubbles unless the requested change explicitly names one of those properties.",
            "Do not apply a global filter, haze, blur, fade, grain, static noise, texture overlay, desaturation, recoloring, or contrast shift.",
            "Do not add context, story details, characters, logos, captions, or text that are not already visible unless the requested change explicitly requires it.",
            "Requested change:",
            user_prompt,
        ]
    )
    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")

    try:
        image_url = edit_image_data_url(prompt=edit_prompt, image_data_url=reference_image, model_name=model_name, size="1024x1024")
    except Exception as error:
        node.image_status = ComicCanvasNode.ImageStatus.FAILED
        node.save(update_fields=["image_status", "updated_at"])
        if _is_image_moderation_block(error):
            return _json_image_moderation_error()
        if _is_provider_billing_limit_error(error):
            return _json_provider_billing_limit_error()
        return _json_image_generation_error(error)

    token = uuid.uuid4().hex
    cache.set(
        _canvas_quick_prompt_cache_key(request.user.pk, node.pk, token),
        {"image_url": image_url, "image_prompt": edit_prompt},
        timeout=CANVAS_QUICK_PROMPT_CACHE_SECONDS,
    )
    return JsonResponse({"ok": True, "image_url": image_url, "pending_token": token})


@login_required
@require_POST
def accept_quick_prompt_canvas_node_image(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)

    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    token = (request.POST.get("pending_token") or "").strip()
    pending = cache.get(_canvas_quick_prompt_cache_key(request.user.pk, node.pk, token)) if token else None
    if not isinstance(pending, dict) or not pending.get("image_url"):
        return JsonResponse({"ok": False, "error": "Quick Prompt preview expired. Run Quick Prompt again."}, status=400)

    node.image_prompt = str(pending.get("image_prompt") or "")
    node.image_data_url = str(pending["image_url"])
    node.image_status = ComicCanvasNode.ImageStatus.READY
    node.last_generated_at = timezone.now()
    node.save(update_fields=["image_prompt", "image_data_url", "image_status", "last_generated_at", "updated_at"])
    cache.delete(_canvas_quick_prompt_cache_key(request.user.pk, node.pk, token))
    return JsonResponse({"ok": True, "image_url": node.image_data_url})


@login_required
@require_POST
def reject_quick_prompt_canvas_node_image(request, slug: str, issue_pk, page_pk, canvas_key: str):
    project = _get_project_for_user(request, slug)
    issue = _get_issue_for_project(project, issue_pk)
    page = _get_page_for_issue(issue, page_pk)
    node, _created = ComicCanvasNode.objects.get_or_create(
        page=page,
        canvas_key=(canvas_key or "").strip(),
        defaults={"node_type": ComicCanvasNode.NodeType.PANEL},
    )
    _sync_canvas_node_from_layout(node)

    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    token = (request.POST.get("pending_token") or "").strip()
    if token:
        cache.delete(_canvas_quick_prompt_cache_key(request.user.pk, node.pk, token))
    return JsonResponse({"ok": True, "image_url": node.image_data_url})


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
