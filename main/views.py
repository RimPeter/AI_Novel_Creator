import json
import logging
import re
import struct
import textwrap
import zlib
from collections import defaultdict
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.utils import OperationalError, ProgrammingError
from django.db import transaction
from django.db.models import Count, Max, Q, ProtectedError, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView
from django.views.decorators.http import require_GET, require_POST

from .billing import (
    _vat_breakdown_from_gross_minor,
    billing_enabled,
    clear_subscription_status,
    cancel_recurring_subscription,
    create_billing_portal_session,
    create_checkout_session,
    format_minor_amount,
    get_billing_company_profile,
    get_billing_invoice_displays,
    get_price_options,
    get_subscription_display,
    sync_checkout_session,
    user_has_active_plan,
    user_has_active_subscription,
)
from .forms import (
    BillingCompanyProfileForm,
    CharacterForm,
    HomeUpdateForm,
    LocationForm,
    NovelProjectForm,
    OutlineChapterForm,
    OutlineSceneForm,
    StoryBibleForm,
    StoryBiblePdfUploadForm,
)
from .location_hierarchy import build_location_tree
from .models import BillingCompanyProfile, BillingInvoice, Character, GenerationRun, HomeUpdate, Location, NovelProject, OutlineNode, StoryBible, StoryBibleDocument
from .text_models import get_available_text_models, get_default_text_model, get_user_text_model, save_user_text_model
from .tasks import generate_all_scenes, generate_bible, generate_outline
from .llm import call_llm, generate_image_data_url

STORY_BIBLE_DOCUMENT_MAX_EXTRACTED_CHARS = 200_000
STORY_BIBLE_DOCUMENT_PROMPT_TOTAL_CHARS = 12_000
STORY_BIBLE_DOCUMENT_PROMPT_PER_DOC_CHARS = 4_000
STRIPE_CHECKOUT_SESSION_PLACEHOLDER = "{CHECKOUT_SESSION_ID}"
BILLING_STATUS_RESET_SUPERUSER = "ferdinand"
logger = logging.getLogger(__name__)
INVOICE_LOGO_PATH = Path(settings.BASE_DIR) / "media" / "images" / "Friendly AI robot logo.png"


def _invoice_totals_for_pdf(invoice: BillingInvoice) -> list[tuple[str, str]]:
    subtotal_amount = int(invoice.subtotal_amount or 0)
    tax_amount = int(invoice.tax_amount or 0)
    total_amount = int(invoice.total_amount or 0)
    if total_amount > 0 and tax_amount <= 0 and subtotal_amount >= total_amount:
        subtotal_amount, tax_amount = _vat_breakdown_from_gross_minor(total_amount)
    return [
        ("Subtotal ex VAT", format_minor_amount(subtotal_amount, invoice.currency)),
        ("VAT 20%", format_minor_amount(tax_amount, invoice.currency)),
        ("Total inc VAT", format_minor_amount(total_amount, invoice.currency)),
        ("Paid", format_minor_amount(invoice.amount_paid, invoice.currency)),
        ("Amount due", format_minor_amount(invoice.amount_due, invoice.currency)),
    ]


@lru_cache(maxsize=1)
def _load_invoice_logo_png() -> tuple[int, int, bytes] | None:
    try:
        data = INVOICE_LOGO_PATH.read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None

    width = height = None
    bit_depth = color_type = compression = filter_method = interlace = None
    idat_parts: list[bytes] = []
    offset = 8
    data_len = len(data)
    while offset + 8 <= data_len:
        chunk_len = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + chunk_len
        if chunk_data_end + 4 > data_len:
            return None
        chunk_data = data[chunk_data_start:chunk_data_end]
        offset = chunk_data_end + 4
        if chunk_type == b"IHDR":
            if len(chunk_data) != 13:
                return None
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not width or not height or not idat_parts:
        return None
    if (bit_depth, color_type, compression, filter_method, interlace) != (8, 2, 0, 0, 0):
        return None
    compressed_data = b"".join(idat_parts)
    try:
        zlib.decompress(compressed_data)
    except zlib.error:
        return None
    return width, height, compressed_data


def _pdf_draw_image(commands: list[str], *, x: float, y: float, width: float, height: float, image_name: str) -> None:
    commands.extend(
        [
            "q",
            f"{width:.2f} 0 0 {height:.2f} {x:.2f} {y:.2f} cm",
            f"/{image_name} Do",
            "Q",
        ]
    )


def _project_queryset_for_user(user):
    if getattr(user, "is_superuser", False):
        return NovelProject.objects.all()
    return NovelProject.objects.filter(owner=user)


def _get_project_for_user(request, slug: str) -> NovelProject:
    return get_object_or_404(_project_queryset_for_user(request.user), slug=slug)


def _get_project_redirect_url(request, fallback_url: str) -> str:
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


def _get_billing_url(request, *, reason: str = "") -> str:
    billing_url = reverse("billing")
    next_url = request.get_full_path()
    params = {}
    if next_url:
        params["next"] = next_url
    if reason:
        params["required"] = reason
    if params:
        billing_url = _add_query_params(billing_url, **params)
    return billing_url


def _subscription_required_response(request, *, wants_json: bool = False):
    if not billing_enabled():
        return None
    if user_has_active_plan(request.user):
        return None
    error = "An active plan is required to generate text and use tokens."
    billing_url = _get_billing_url(request, reason="active-plan")
    if wants_json:
        return JsonResponse({"ok": False, "error": error, "billing_url": billing_url}, status=402)
    messages.error(request, error)
    return HttpResponseRedirect(billing_url)


def _ensure_json_ai_request(request):
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)
    blocked = _subscription_required_response(request, wants_json=True)
    if blocked is not None:
        return blocked
    return None


def _json_internal_error() -> JsonResponse:
    _log_exception("Request processing failed.")
    return JsonResponse({"ok": False, "error": "Request failed. Please try again."}, status=400)


def _log_exception(message: str, *args) -> None:
    logger.error(message, *args, exc_info=not getattr(settings, "RUNNING_TESTS", False))


def _call_tracked_llm_json_object(
    *,
    project: NovelProject,
    action_label: str,
    prompt: str,
    model_name: str,
    params: dict,
    run_type: str = GenerationRun.RunType.SCENE,
    outline_node: OutlineNode | None = None,
    use_json_object_extractor: bool = True,
):
    result = _call_tracked_llm(
        project=project,
        action_label=action_label,
        prompt=prompt,
        model_name=model_name,
        params=params,
        run_type=run_type,
        outline_node=outline_node,
    )
    raw_text = (result.text or "").strip()
    payload = _extract_json_object(raw_text) if use_json_object_extractor else raw_text
    data = json.loads(payload) if payload else {}
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object.")
    return data


def _get_annotated_projects_queryset(user):
    return _project_queryset_for_user(user).annotate(
        character_count=Count("characters", distinct=True),
        outline_count=Count("outline_nodes", distinct=True),
        scene_count=Count(
            "outline_nodes",
            filter=Q(outline_nodes__node_type=OutlineNode.NodeType.SCENE),
            distinct=True,
        ),
        run_count=Count("runs", distinct=True),
    )


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

    constraints = bible.constraints
    if isinstance(constraints, str):
        if constraints.strip():
            lines.append("Story bible constraints: " + constraints.strip())
    elif constraints:
        lines.append("Story bible constraints (JSON): " + json.dumps(constraints, ensure_ascii=False))

    facts = bible.facts
    if isinstance(facts, str):
        if facts.strip():
            lines.append("Story bible facts: " + facts.strip())
    elif facts:
        lines.append("Story bible facts (JSON): " + json.dumps(facts, ensure_ascii=False))

    document_lines = _get_story_bible_document_context(bible)
    if document_lines:
        lines.extend(document_lines)

    if not lines:
        return []

    return ["Story bible context:"] + lines


def _get_story_bible_document_context(bible: StoryBible) -> list[str]:
    try:
        documents = list(
            bible.documents.exclude(extracted_text="")
            .only("original_name", "page_count", "extracted_text")
            .order_by("-created_at", "-id")
        )
    except (OperationalError, ProgrammingError):
        return []
    if not documents:
        return []

    lines = []
    remaining = STORY_BIBLE_DOCUMENT_PROMPT_TOTAL_CHARS
    for document in documents:
        if remaining <= 0:
            break

        raw_text = (document.extracted_text or "").strip()
        if not raw_text:
            continue

        excerpt_limit = min(STORY_BIBLE_DOCUMENT_PROMPT_PER_DOC_CHARS, remaining)
        excerpt = _truncate_prompt_text(raw_text, limit=excerpt_limit)
        if not excerpt:
            continue

        name = (document.original_name or "").strip() or "PDF reference"
        header = f"Story bible PDF reference: {name}"
        if document.page_count:
            header += f" ({document.page_count} pages)"
        lines.append(header)
        lines.append("PDF excerpt: " + excerpt)
        remaining -= len(excerpt)

    return lines


def _extract_story_bible_pdf(uploaded_file) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF uploads require the pypdf package to be installed.") from exc

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    try:
        reader = PdfReader(uploaded_file)
        if reader.is_encrypted:
            reader.decrypt("")
    except Exception as exc:
        raise ValueError("Could not read that PDF. Upload a standard, non-corrupted PDF file.") from exc

    pages = []
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pages.append(text)

    extracted_text = "\n\n".join(pages).strip()
    if not extracted_text:
        raise ValueError("The PDF did not contain extractable text.")

    if len(extracted_text) > STORY_BIBLE_DOCUMENT_MAX_EXTRACTED_CHARS:
        extracted_text = _truncate_prompt_text(extracted_text, limit=STORY_BIBLE_DOCUMENT_MAX_EXTRACTED_CHARS)

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    return extracted_text, len(reader.pages)


def _get_selected_character_context(project: NovelProject, selected_ids: list[str] | None) -> list[str]:
    ids = [str(pk).strip() for pk in (selected_ids or []) if str(pk).strip()]
    if not ids:
        return []

    characters = {str(obj.id): obj for obj in Character.objects.filter(project=project, id__in=ids)}
    if not characters:
        return []

    lines = ["Selected scene characters:"]
    for char_id in ids:
        character = characters.get(char_id)
        if character is None:
            continue

        details = []
        if (character.role or "").strip():
            details.append(f"role={character.role.strip()}")
        if character.age is not None:
            details.append(f"age={character.age}")
        if (character.gender or "").strip():
            details.append(f"gender={character.gender.strip()}")
        if (character.personality or "").strip():
            details.append(f"personality={character.personality.strip()}")
        if (character.appearance or "").strip():
            details.append(f"appearance={character.appearance.strip()}")
        if (character.background or "").strip():
            details.append(f"background={character.background.strip()}")
        if (character.goals or "").strip():
            details.append(f"goals={character.goals.strip()}")
        if (character.voice_notes or "").strip():
            details.append(f"voice_notes={character.voice_notes.strip()}")
        if (character.description or "").strip():
            details.append(f"description={character.description.strip()}")
        if isinstance(character.extra_fields, dict):
            for key, value in character.extra_fields.items():
                key_text = str(key or "").strip()
                value_text = str(value or "").strip()
                if key_text and value_text:
                    details.append(f"{key_text}={value_text}")

        line = f"- {character.name}"
        if details:
            line += ": " + "; ".join(details)
        lines.append(line)

    return lines if len(lines) > 1 else []


def _get_selected_location_context(project: NovelProject, location_name: str) -> list[str]:
    name = (location_name or "").strip()
    if not name:
        return []

    location = (
        Location.objects.filter(project=project, name__iexact=name)
        .only("name", "description")
        .order_by("name", "id")
        .first()
    )
    if location is None:
        return []

    lines = [f"Selected location: {location.name}"]
    description = (location.description or "").strip()
    if description:
        lines.append("Location description: " + description)

    return lines if len(lines) > 1 else []


def _truncate_prompt_text(text: str, limit: int = 2000) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


_PORTRAIT_VISUAL_EXTRA_FIELDS = {
    "appearance",
    "build",
    "clothing",
    "eyes",
    "eye color",
    "face",
    "facial features",
    "features",
    "hair",
    "hair color",
    "height",
    "outfit",
    "skin",
    "style",
}


def _normalize_image_detail(text: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _append_detail_line(lines: list[str], label: str, value: str, limit: int = 220) -> None:
    text = _normalize_image_detail(value, limit=limit)
    if text:
        lines.append(f"{label}: {text}")


def _get_portrait_visual_extra_lines(extra_fields) -> list[str]:
    if not isinstance(extra_fields, dict):
        return []

    lines = []
    for key, value in extra_fields.items():
        key_text = _normalize_image_detail(str(key or ""), limit=60)
        if not key_text:
            continue
        if key_text.lower() not in _PORTRAIT_VISUAL_EXTRA_FIELDS:
            continue
        value_text = _normalize_image_detail(str(value or ""), limit=140)
        if value_text:
            lines.append(f"{key_text.title()}: {value_text}")
    return lines


def _get_usage_int(usage: dict, key: str) -> int:
    raw = (usage or {}).get(key)
    if raw in (None, ""):
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _get_run_total_tokens(run: GenerationRun) -> int:
    usage = run.usage or {}
    total_tokens = _get_usage_int(usage, "total_tokens")
    if total_tokens:
        return total_tokens

    prompt_tokens = _get_usage_int(usage, "prompt_tokens")
    completion_tokens = _get_usage_int(usage, "completion_tokens")
    return prompt_tokens + completion_tokens


def _get_run_action_label(run: GenerationRun) -> str:
    custom_label = str((run.usage or {}).get("action_label") or "").strip()
    if custom_label:
        return custom_label
    return {
        GenerationRun.RunType.BIBLE: "Generate Bible",
        GenerationRun.RunType.OUTLINE: "Generate Outline",
        GenerationRun.RunType.SCENE: "Generate All Scenes",
    }.get(run.run_type, run.get_run_type_display())


def _call_tracked_llm(
    *,
    project: NovelProject,
    action_label: str,
    prompt: str,
    model_name: str,
    params: dict,
    run_type: str = GenerationRun.RunType.SCENE,
    outline_node: OutlineNode | None = None,
):
    result = call_llm(
        prompt=prompt,
        model_name=model_name,
        params=params,
    )

    usage = dict(result.usage or {})
    usage["action_label"] = action_label

    GenerationRun.objects.create(
        project=project,
        outline_node=outline_node,
        run_type=run_type,
        status=GenerationRun.Status.SUCCEEDED,
        prompt=prompt,
        model_name=model_name,
        params=params,
        output_text=(result.text or "").strip(),
        usage=usage,
    )
    return result


def _get_previous_scene_context(scene: OutlineNode) -> list[str]:
    if not scene.parent_id:
        return []

    siblings = list(
        OutlineNode.objects.filter(
            project=scene.project,
            parent_id=scene.parent_id,
            node_type=OutlineNode.NodeType.SCENE,
        ).order_by("order", "created_at", "id")
    )
    if not siblings:
        return []

    previous = None
    for idx, sibling in enumerate(siblings):
        if sibling.id != scene.id:
            continue
        if idx > 0:
            previous = siblings[idx - 1]
        break

    if previous is None:
        return []

    lines = ["Previous scene in this chapter:"]
    if (previous.title or "").strip():
        lines.append("Title: " + previous.title.strip())
    if (previous.pov or "").strip():
        lines.append("POV: " + previous.pov.strip())
    if (previous.location or "").strip():
        lines.append("Location: " + previous.location.strip())
    if (previous.summary or "").strip():
        lines.append("Summary: " + previous.summary.strip())

    continuity_text = (previous.rendered_text or "").strip() or (previous.structure_json or "").strip()
    if continuity_text:
        lines.append("Text for continuity: " + _truncate_prompt_text(continuity_text))

    return lines if len(lines) > 1 else []


def _get_chapter_scene_links(scene: OutlineNode) -> list[OutlineNode]:
    if not scene.parent_id:
        return []

    return list(
        OutlineNode.objects.filter(
            project=scene.project,
            parent_id=scene.parent_id,
            node_type=OutlineNode.NodeType.SCENE,
        ).order_by("order", "created_at", "id")
    )


def _add_query_params(url: str, **params) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in params.items():
        if v is None:
            continue
        query[k] = str(v)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def _is_stripe_checkout_session_placeholder(session_id: str) -> bool:
    return str(session_id or "").strip() == STRIPE_CHECKOUT_SESSION_PLACEHOLDER


def _build_stripe_checkout_success_url(request) -> str:
    placeholder_token = "__stripe_checkout_session_id__"
    success_path = _add_query_params(
        reverse("billing"),
        checkout="success",
        session_id=placeholder_token,
    )
    return request.build_absolute_uri(success_path).replace(placeholder_token, STRIPE_CHECKOUT_SESSION_PLACEHOLDER)


def _can_clear_billing_status(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_superuser", False):
        return False
    return str(getattr(user, "username", "") or "").strip().lower() == BILLING_STATUS_RESET_SUPERUSER


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
    updates = HomeUpdate.objects.order_by("-date", "-created_at")
    return render(request, "main/home.html", {"updates": updates})


@require_POST
@login_required
def brainstorm_project(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 500}
        data = _call_tracked_llm_json_object(
            project=project,
            action_label="Project Brainstorm",
            prompt=prompt,
            model_name=model_name,
            params=params,
            run_type=GenerationRun.RunType.BIBLE,
        )

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
        return _json_internal_error()


@require_POST
@login_required
def add_project_details(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 500}
        data = _call_tracked_llm_json_object(
            project=project,
            action_label="Project Add Details",
            prompt=prompt,
            model_name=model_name,
            params=params,
            run_type=GenerationRun.RunType.BIBLE,
        )

        filtered = {}
        for key, value in data.items():
            if key not in allowed_fields:
                continue
            if key in {"genre", "tone"} and current.get(key):
                continue
            existing = (current.get(key) or "").strip()
            text = _dedupe_appended_text(existing, str(value))
            if not text:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return _json_internal_error()


@require_POST
@login_required
def brainstorm_story_bible(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    allowed_fields = [
        "summary_md",
        "constraints",
        "facts",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    empty_fields = [k for k in allowed_fields if not current.get(k)]
    if not empty_fields:
        return JsonResponse({"ok": True, "suggestions": {}})

    prompt_lines = [
        "You are a novelist's story bible assistant.",
        "Goal: fill in ONLY the currently-empty story bible fields with coherent canon notes.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- Only include keys that are empty right now: " + ", ".join(empty_fields),
        "- Keep each field concise and useful.",
        "- Use plain prose. No bullet points.",
        "",
        "Project title: " + (project.title or ""),
        "Project slug: " + (project.slug or ""),
        "",
        "Existing story bible fields (JSON):",
        json.dumps(current, ensure_ascii=False),
    ]
    prompt = "\n".join(prompt_lines).strip()

    try:
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 650}
        data = _call_tracked_llm_json_object(
            project=project,
            action_label="Story Bible Brainstorm",
            prompt=prompt,
            model_name=model_name,
            params=params,
            run_type=GenerationRun.RunType.BIBLE,
        )

        filtered = {}
        for key, value in data.items():
            if key not in empty_fields:
                continue
            text = str(value or "").strip()
            if not text:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception:
        return _json_internal_error()


@require_POST
@login_required
def add_story_bible_details(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    allowed_fields = [
        "summary_md",
        "constraints",
        "facts",
    ]

    current = {k: (request.POST.get(k) or "").strip() for k in allowed_fields}
    if not any(current.values()):
        return JsonResponse({"ok": False, "error": "Add at least one story bible detail first."}, status=400)

    prompt_lines = [
        "You are a novelist's story bible development assistant.",
        "Goal: add helpful additional detail that expands (but does not repeat) what already exists.",
        "Rules:",
        "- Return STRICT JSON only (no markdown, no extra text).",
        "- Output an object with only keys from: " + ", ".join(allowed_fields),
        "- For fields that already have text, return ONLY additional text to append (do not rewrite).",
        "- For fields that are empty, provide a concise starter value when useful.",
        "- Use plain prose. No bullet points.",
        "",
        "Project title: " + (project.title or ""),
        "Project slug: " + (project.slug or ""),
        "",
        "Current story bible fields (JSON):",
        json.dumps(current, ensure_ascii=False),
    ]
    prompt = "\n".join(prompt_lines).strip()

    try:
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 700}
        result = _call_tracked_llm(
            project=project,
            action_label="Story Bible Add Details",
            prompt=prompt,
            model_name=model_name,
            params=params,
            run_type=GenerationRun.RunType.BIBLE,
        )
        data = _extract_story_bible_suggestions(result.text)

        if not data:
            repair_prompt = "\n".join(
                [
                    "Convert the following text into STRICT JSON only.",
                    "Output one object with optional keys: summary_md, constraints, facts.",
                    "Do not include markdown, code fences, or commentary.",
                    "If a key has no usable value, omit it.",
                    "",
                    "Text to convert:",
                    (result.text or "").strip(),
                ]
            ).strip()
            repair_result = _call_tracked_llm(
                project=project,
                action_label="Story Bible Add Details JSON Repair",
                prompt=repair_prompt,
                model_name=model_name,
                params={"temperature": 0.0, "max_tokens": 450},
                run_type=GenerationRun.RunType.BIBLE,
            )
            data = _extract_story_bible_suggestions(repair_result.text)

        filtered = {}
        for key, value in data.items():
            if key not in allowed_fields:
                continue
            text = str(value or "").strip()
            if not text:
                continue
            if current.get(key) and text in current[key]:
                continue
            filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception:
        return _json_internal_error()


@require_POST
@login_required
def brainstorm_scene(request, slug, pk):
    scene = _get_scene_for_user(request, slug=slug, pk=pk)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 500}
        data = _call_tracked_llm_json_object(
            project=scene.project,
            action_label="Scene Brainstorm",
            prompt=prompt,
            model_name=model_name,
            params=params,
            outline_node=scene,
        )

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
        return _json_internal_error()


@require_POST
@login_required
def add_scene_details(request, slug, pk):
    scene = _get_scene_for_user(request, slug=slug, pk=pk)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 500}
        data = _call_tracked_llm_json_object(
            project=scene.project,
            action_label="Scene Add Details",
            prompt=prompt,
            model_name=model_name,
            params=params,
            outline_node=scene,
        )

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
        return _json_internal_error()


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
def move_location(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )

    location_id = request.POST.get("location_id")
    target_parent_id = request.POST.get("target_parent_id")

    if not location_id or not target_parent_id:
        if wants_json:
            return JsonResponse({"ok": False, "error": "Missing location or target parent."}, status=400)
        messages.error(request, "Missing location or target parent.")
        return HttpResponseRedirect(reverse("location-list", kwargs={"slug": project.slug}))

    location = get_object_or_404(Location, id=location_id, project=project)
    target_parent = get_object_or_404(Location, id=target_parent_id, project=project)

    if location.is_root:
        if wants_json:
            return JsonResponse({"ok": False, "error": "The root location cannot be moved."}, status=400)
        messages.error(request, "The root location cannot be moved.")
        return HttpResponseRedirect(reverse("location-list", kwargs={"slug": project.slug}))

    if location.id == target_parent.id:
        if wants_json:
            return JsonResponse({"ok": False, "error": "A location cannot be nested inside itself."}, status=400)
        messages.error(request, "A location cannot be nested inside itself.")
        return HttpResponseRedirect(reverse("location-list", kwargs={"slug": project.slug}))

    location.parent = target_parent
    try:
        location.full_clean()
        location.save(update_fields=["parent", "updated_at"])
    except Exception as e:
        if wants_json:
            return _json_internal_error()
        messages.error(request, str(e))
        return HttpResponseRedirect(reverse("location-list", kwargs={"slug": project.slug}))

    if wants_json:
        return JsonResponse({"ok": True})
    messages.success(request, "Location moved.")
    return HttpResponseRedirect(reverse("location-list", kwargs={"slug": project.slug}))


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


@require_GET
@login_required
def scene_synonyms(request, slug):
    _get_project_for_user(request, slug)

    word = re.sub(r"[^A-Za-z'-]+", "", (request.GET.get("word") or "").strip()).strip("-'")
    if len(word) < 2:
        return JsonResponse({"ok": False, "error": "Word is required."}, status=400)

    request_url = "https://api.datamuse.com/words?" + urlencode({"ml": word.lower(), "max": 12})
    api_request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AI-Novel-Creator/1.0",
        },
    )

    synonyms = []
    try:
        with urlopen(api_request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        payload = []

    seen = set()
    for item in payload if isinstance(payload, list) else []:
        candidate = str(item.get("word") or "").strip()
        normalized = re.sub(r"[^A-Za-z'-]+", "", candidate).strip("-'").lower()
        if not normalized or normalized == word.lower() or normalized in seen:
            continue
        seen.add(normalized)
        synonyms.append(candidate)
        if len(synonyms) >= 8:
            break

    return JsonResponse({"ok": True, "word": word.lower(), "synonyms": synonyms})


@require_POST
@login_required
def brainstorm_character(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 500}
        suggestions = _call_tracked_llm_json_object(
            project=project,
            action_label="Character Brainstorm",
            prompt=prompt,
            model_name=model_name,
            params=params,
            use_json_object_extractor=False,
        )

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
        return _json_internal_error()


@require_POST
@login_required
def add_character_details(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 650}
        suggestions = _call_tracked_llm_json_object(
            project=project,
            action_label="Character Add Details",
            prompt=prompt,
            model_name=model_name,
            params=params,
            use_json_object_extractor=False,
        )

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
                existing = (current.get(key) or "").strip()
                text = _dedupe_appended_text(existing, str(value))
                if not text:
                    continue
                filtered[key] = text

        return JsonResponse({"ok": True, "suggestions": filtered})
    except Exception as e:
        return _json_internal_error()


@require_POST
@login_required
def generate_character_portrait(request, slug, pk):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request, wants_json=True)
    if blocked is not None:
        return blocked

    character = get_object_or_404(Character, id=pk, project=project)

    def get_text(field, fallback):
        value = request.POST.get(field)
        if value is None:
            value = fallback
        return (value or "").strip()

    def get_age_value(fallback):
        raw = request.POST.get("age")
        if raw is None:
            return fallback
        raw = raw.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except Exception:
            return None

    name = get_text("name", character.name)
    if not name:
        return JsonResponse({"ok": False, "error": "Character name is required."}, status=400)

    prompt_lines = [
        "Create a photorealistic passport photo of a fictional person.",
        "This must look like an official passport or ID photo, not concept art, not a cinematic portrait, and not an illustration.",
        "Composition: exactly one person only, head and upper shoulders only, centered in frame, straight-on camera, looking directly at the lens.",
        "Subject count: a single face only. Never show two people, twins, a duplicate face, a mirrored subject, a reflection, or a split composition.",
        "Cropping: face fills most of the frame like a passport photo, with a small amount of space above the head and both shoulders visible.",
        "Expression and pose: neutral expression, mouth closed, eyes open, upright posture, no dramatic pose.",
        "Lighting and background: flat even studio lighting, plain white or light gray background, minimal shadows.",
        "Styling constraints: simple everyday clothing only, no props, no hands in frame, no stylized makeup, no dramatic hair motion.",
        "Image quality: sharp focus, natural skin texture, realistic proportions, official document photo aesthetic.",
        "Ignore story context and personality traits. Use only stable visible physical characteristics.",
        "No text, no logos, no watermarks, no borders.",
        "",
        "Subject details:",
    ]
    _append_detail_line(prompt_lines, "Name", name, limit=80)
    age_value = get_age_value(character.age)
    if age_value is not None:
        prompt_lines.append(f"Age: {age_value}")
    _append_detail_line(prompt_lines, "Gender", get_text("gender", character.gender), limit=80)
    _append_detail_line(prompt_lines, "Appearance", get_text("appearance", character.appearance))
    _append_detail_line(prompt_lines, "Description", get_text("description", character.description))
    prompt_lines.extend(_get_portrait_visual_extra_lines(character.extra_fields or {}))

    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-1")
    fallback_model = getattr(settings, "OPENAI_IMAGE_FALLBACK_MODEL", "")
    if not fallback_model and model_name == "gpt-image-1":
        fallback_model = "dall-e-3"

    try:
        data_url = generate_image_data_url(
            prompt="\n".join(prompt_lines),
            model_name=model_name,
            size="1024x1024",
        )
    except Exception as e:
        if fallback_model and fallback_model != model_name:
            try:
                data_url = generate_image_data_url(
                    prompt="\n".join(prompt_lines),
                    model_name=fallback_model,
                    size="1024x1024",
                )
            except Exception as fallback_error:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": str(fallback_error),
                    },
                    status=400,
                )
        else:
            return _json_internal_error()

    character.portrait_data_url = data_url
    character.save(update_fields=["portrait_data_url", "updated_at"])
    return JsonResponse({"ok": True, "portrait_url": data_url})


class ProjectListView(LoginRequiredMixin, ListView):
    model = NovelProject
    template_name = "main/project_list.html"
    context_object_name = "projects"
    ordering = ["title"]

    def get_queryset(self):
        return _get_annotated_projects_queryset(self.request.user).filter(is_archived=False).order_by(*self.ordering)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        projects = ctx["projects"]
        ctx["project_count"] = projects.count()
        ctx["total_target_word_count"] = projects.aggregate(total=Sum("target_word_count"))["total"] or 0
        ctx["recently_updated_count"] = projects.filter(
            updated_at__gte=timezone.now() - timedelta(days=7)
        ).count()
        ctx["archived_count"] = _project_queryset_for_user(self.request.user).filter(is_archived=True).count()
        return ctx


class ProjectArchiveListView(LoginRequiredMixin, ListView):
    model = NovelProject
    template_name = "main/archive.html"
    context_object_name = "projects"
    ordering = ["title"]

    def get_queryset(self):
        return _get_annotated_projects_queryset(self.request.user).filter(is_archived=True).order_by(*self.ordering)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        projects = ctx["projects"]
        ctx["project_count"] = projects.count()
        return ctx


class TokenUsageView(LoginRequiredMixin, TemplateView):
    template_name = "main/token_usage.html"

    def dispatch(self, request, *args, **kwargs):
        blocked = _subscription_required_response(request)
        if blocked is not None:
            return blocked
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            selected_model = save_user_text_model(request.user, request.POST.get("text_model_name") or "")
        except ValueError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"Text model updated to {selected_model}.")
        return HttpResponseRedirect(reverse("token-usage"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        runs = list(
            GenerationRun.objects.select_related("project")
            .filter(project__in=_project_queryset_for_user(self.request.user))
            .order_by("-created_at")
        )
        daily_totals = defaultdict(lambda: {"date": None, "action_label": "", "total_tokens": 0, "run_count": 0})
        project_totals = defaultdict(lambda: {"project": None, "total_tokens": 0, "run_count": 0})

        tracked_run_count = 0
        overall_total_tokens = 0

        for run in runs:
            token_total = _get_run_total_tokens(run)
            if token_total <= 0:
                continue

            tracked_run_count += 1
            overall_total_tokens += token_total
            action_label = _get_run_action_label(run)
            run_date = timezone.localtime(run.created_at).date()

            daily_key = (run_date, action_label)
            daily_entry = daily_totals[daily_key]
            daily_entry["date"] = run_date
            daily_entry["action_label"] = action_label
            daily_entry["total_tokens"] += token_total
            daily_entry["run_count"] += 1

            project_entry = project_totals[run.project_id]
            project_entry["project"] = run.project
            project_entry["total_tokens"] += token_total
            project_entry["run_count"] += 1

        ctx["daily_rows"] = sorted(
            daily_totals.values(),
            key=lambda row: (row["date"], row["action_label"]),
            reverse=True,
        )
        ctx["project_rows"] = sorted(
            project_totals.values(),
            key=lambda row: (row["total_tokens"], row["project"].title.lower() if row["project"] else ""),
            reverse=True,
        )
        ctx["overall_total_tokens"] = overall_total_tokens
        ctx["tracked_run_count"] = tracked_run_count
        ctx["tracked_day_count"] = len({row["date"] for row in daily_totals.values()})
        ctx["available_text_models"] = get_available_text_models()
        ctx["selected_text_model"] = get_user_text_model(self.request.user)
        ctx["default_text_model"] = get_default_text_model()
        return ctx


class BillingView(LoginRequiredMixin, TemplateView):
    template_name = "main/billing.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["billing_enabled"] = billing_enabled()
        ctx["price_options"] = get_price_options()
        ctx["invoices"] = get_billing_invoice_displays(self.request.user)
        ctx["can_clear_billing_status"] = _can_clear_billing_status(self.request.user)
        ctx["can_edit_company_details"] = bool(self.request.user.is_authenticated and self.request.user.is_superuser)
        ctx["checkout_status"] = (self.request.GET.get("checkout") or "").strip()
        ctx["checkout_session_id"] = (self.request.GET.get("session_id") or "").strip()
        ctx["required_access"] = (self.request.GET.get("required") or "").strip()
        ctx["access_notice"] = ""
        if ctx["required_access"] == "active-plan":
            ctx["access_notice"] = "An active plan is required to generate text and use tokens."
        ctx["checkout_sync_error"] = ""
        ctx["checkout_sync_notice"] = ""
        if (
            ctx["billing_enabled"]
            and ctx["checkout_status"] == "success"
            and ctx["checkout_session_id"]
        ):
            if _is_stripe_checkout_session_placeholder(ctx["checkout_session_id"]):
                ctx["checkout_sync_notice"] = (
                    "Stripe did not return a usable session id in the redirect. "
                    "Your account status will update shortly through the Stripe webhook."
                )
            else:
                try:
                    sync_checkout_session(
                        user=self.request.user,
                        session_id=ctx["checkout_session_id"],
                    )
                except Exception as e:
                    _log_exception("Failed to sync Stripe checkout session id=%s", ctx["checkout_session_id"])
                    ctx["checkout_sync_error"] = "Could not refresh account status immediately. Webhook sync will retry shortly."
        ctx["subscription"] = get_subscription_display(self.request.user)
        ctx["next_url"] = (self.request.GET.get("next") or "").strip()
        return ctx


class BillingTermsView(LoginRequiredMixin, TemplateView):
    template_name = "main/billing_terms.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["price_options"] = get_price_options()
        return ctx


@require_POST
@login_required
def create_billing_checkout(request):
    if not billing_enabled():
        messages.error(request, "Stripe billing is not configured yet.")
        return HttpResponseRedirect(reverse("billing"))

    if user_has_active_subscription(request.user):
        messages.warning(request, "You already have active paid access. Use Manage billing for recurring plans.")
        return HttpResponseRedirect(reverse("billing"))

    plan = (request.POST.get("plan") or "").strip().lower()
    option = next((item for item in get_price_options() if item["key"] == plan and item["price_id"]), None)
    if option is None:
        messages.error(request, "Choose a valid billing plan.")
        return HttpResponseRedirect(reverse("billing"))
    accepted_terms = str(request.POST.get("accepted_terms") or "").strip().lower()
    if accepted_terms not in {"1", "true", "yes", "on"}:
        messages.error(
            request,
            "Accept the plan purchase terms and conditions before continuing to checkout.",
        )
        return HttpResponseRedirect(reverse("billing"))

    success_url = _build_stripe_checkout_success_url(request)
    cancel_url = request.build_absolute_uri(_add_query_params(reverse("billing"), checkout="cancelled"))
    try:
        session = create_checkout_session(
            user=request.user,
            option=option,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as e:
        _log_exception("Failed to create Stripe checkout session for user_id=%s", request.user.pk)
        messages.error(request, "Could not start Stripe Checkout right now. Please try again.")
        return HttpResponseRedirect(reverse("billing"))
    return HttpResponseRedirect(session.url)


@require_POST
@login_required
def create_billing_portal(request):
    if not billing_enabled():
        messages.error(request, "Stripe billing is not configured yet.")
        return HttpResponseRedirect(reverse("billing"))
    try:
        session = create_billing_portal_session(
            user=request.user,
            return_url=request.build_absolute_uri(reverse("billing")),
        )
    except Exception as e:
        _log_exception("Failed to create Stripe billing portal session for user_id=%s", request.user.pk)
        messages.error(request, "Could not open the billing portal right now. Please try again.")
        return HttpResponseRedirect(reverse("billing"))
    return HttpResponseRedirect(session.url)


@require_POST
@login_required
def cancel_billing_recurring(request):
    if not billing_enabled():
        messages.error(request, "Stripe billing is not configured yet.")
        return HttpResponseRedirect(reverse("billing"))

    try:
        record = cancel_recurring_subscription(user=request.user)
    except Exception as e:
        _log_exception("Failed to cancel recurring subscription for user_id=%s", request.user.pk)
        messages.error(request, "Could not cancel recurring payments right now. Please try again.")
        return HttpResponseRedirect(reverse("billing"))

    if record is None:
        messages.error(request, "No recurring subscription was found to cancel.")
        return HttpResponseRedirect(reverse("billing"))

    messages.success(request, "Recurring payments cancelled. Access remains active until the current period ends.")
    return HttpResponseRedirect(reverse("billing"))


@require_POST
@login_required
def clear_billing_status(request):
    if not _can_clear_billing_status(request.user):
        return JsonResponse({"ok": False, "error": "Forbidden."}, status=403)
    record = clear_subscription_status(request.user)
    if record is None:
        messages.info(request, "No billing status was stored for this account.")
    else:
        messages.success(request, "Billing status cleared for test checkout.")
    return HttpResponseRedirect(reverse("billing"))


def _get_billing_invoice_for_request(request, pk) -> BillingInvoice:
    queryset = BillingInvoice.objects.select_related("user", "subscription_record")
    if request.user.is_superuser:
        return get_object_or_404(queryset, pk=pk)
    return get_object_or_404(queryset, pk=pk, user=request.user)


def _escape_pdf_text(value: str) -> str:
    text = str(value or "")
    text = text.translate(
        {
            ord("\u2018"): "'",
            ord("\u2019"): "'",
            ord("\u201a"): "'",
            ord("\u201b"): "'",
            ord("\u201c"): '"',
            ord("\u201d"): '"',
            ord("\u201e"): '"',
            ord("\u201f"): '"',
            ord("\u2032"): "'",
            ord("\u2033"): '"',
            ord("\u2013"): "-",
            ord("\u2014"): "-",
            ord("\u2026"): "...",
            ord("\u00a0"): " ",
        }
    )
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_wrap_lines(value: str, *, width: int) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return [""]
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        wrapped = textwrap.wrap(raw.strip(), width=width) if raw.strip() else [""]
        lines.extend(wrapped or [""])
    return lines or [""]


def _pdf_set_fill(commands: list[str], r: float, g: float, b: float) -> None:
    commands.append(f"{r:.3f} {g:.3f} {b:.3f} rg")


def _pdf_set_stroke(commands: list[str], r: float, g: float, b: float) -> None:
    commands.append(f"{r:.3f} {g:.3f} {b:.3f} RG")


def _pdf_fill_rect(commands: list[str], x: float, y: float, w: float, h: float) -> None:
    commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")


def _pdf_stroke_rect(commands: list[str], x: float, y: float, w: float, h: float) -> None:
    commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re S")


def _pdf_draw_line(commands: list[str], x1: float, y1: float, x2: float, y2: float) -> None:
    commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")


def _pdf_draw_text(
    commands: list[str],
    *,
    x: float,
    y: float,
    text: str,
    font: str = "/F1",
    size: float = 10.0,
    color: tuple[float, float, float] = (0.1, 0.13, 0.18),
) -> None:
    r, g, b = color
    commands.extend(
        [
            "BT",
            f"{font} {size:.2f} Tf",
            f"{r:.3f} {g:.3f} {b:.3f} rg",
            f"{x:.2f} {y:.2f} Td",
            f"({_escape_pdf_text(text)}) Tj",
            "ET",
        ]
    )


def _pdf_draw_text_lines(
    commands: list[str],
    *,
    x: float,
    y: float,
    lines: list[str],
    font: str = "/F1",
    size: float = 10.0,
    leading: float = 13.0,
    color: tuple[float, float, float] = (0.1, 0.13, 0.18),
) -> None:
    if not lines:
        return
    r, g, b = color
    commands.extend(
        [
            "BT",
            f"{font} {size:.2f} Tf",
            f"{leading:.2f} TL",
            f"{r:.3f} {g:.3f} {b:.3f} rg",
            f"{x:.2f} {y:.2f} Td",
        ]
    )
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({_escape_pdf_text(line)}) Tj")
    commands.append("ET")


def _build_simple_pdf(lines: list[str]) -> bytes:
    wrapped_lines: list[str] = []
    for raw_line in lines:
        text = str(raw_line or "").strip()
        if not text:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(text, width=88) or [""])
    wrapped_lines = wrapped_lines[:52]

    content_lines = [
        "BT",
        "/F1 12 Tf",
        "50 790 Td",
        "14 TL",
    ]
    for index, line in enumerate(wrapped_lines):
        if index:
            content_lines.append("T*")
        content_lines.append(f"({_escape_pdf_text(line)}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", "replace")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n".encode("ascii") + stream + b"\nendstream endobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _build_paginated_text_pdf(
    lines: list[str],
    *,
    wrap_width: int = 72,
    lines_per_page: int = 52,
    page_break_token: str | None = None,
    heading_token: str | None = None,
    act_heading_token: str | None = None,
    chapter_heading_token: str | None = None,
) -> bytes:
    page_chunks: list[list[tuple[str, str]]] = []
    current_page: list[tuple[str, str]] = []

    def _append_line(line: str, style: str) -> None:
        nonlocal current_page
        if len(current_page) >= lines_per_page:
            page_chunks.append(current_page)
            current_page = []
        current_page.append((line, style))

    for raw_line in lines:
        line_value = str(raw_line or "")
        if page_break_token is not None and line_value == page_break_token:
            if current_page:
                page_chunks.append(current_page)
                current_page = []
            continue

        style = "body"
        line_wrap_width = wrap_width
        if act_heading_token is not None and line_value.startswith(act_heading_token):
            style = "act"
            line_value = line_value[len(act_heading_token) :]
            line_wrap_width = 50
        elif chapter_heading_token is not None and line_value.startswith(chapter_heading_token):
            style = "chapter"
            line_value = line_value[len(chapter_heading_token) :]
            line_wrap_width = 58
        if heading_token is not None and line_value.startswith(heading_token):
            style = "heading"
            line_value = line_value[len(heading_token) :]

        text = line_value.strip()
        if not text:
            _append_line("", "body")
            continue
        for wrapped_line in textwrap.wrap(text, width=line_wrap_width) or [""]:
            _append_line(wrapped_line, style)

    if current_page:
        page_chunks.append(current_page)
    if not page_chunks:
        page_chunks = [[("", "body")]]

    objects: list[bytes] = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n",
        b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n",
    ]
    page_refs: list[str] = []

    for idx, chunk in enumerate(page_chunks):
        page_object_id = 5 + idx * 2
        stream_object_id = page_object_id + 1
        page_refs.append(f"{page_object_id} 0 R")

        y = 756.0
        line_step = 14.0
        content_lines: list[str] = []
        for line, style in chunk:
            if style == "act":
                font_name = "/F2"
                font_size = 16.0
                centered = True
            elif style == "chapter":
                font_name = "/F2"
                font_size = 14.0
                centered = True
            elif style == "heading":
                font_name = "/F2"
                font_size = 11.0
                centered = False
            else:
                font_name = "/F1"
                font_size = 11.0
                centered = False

            x = 36.0
            if centered and line:
                approx_width = len(line) * font_size * 0.56
                x = max(36.0, (612.0 - approx_width) / 2.0)

            content_lines.extend(
                [
                    "BT",
                    f"{font_name} {font_size:.2f} Tf",
                    f"{x:.2f} {y:.2f} Td",
                    f"({_escape_pdf_text(line)}) Tj",
                    "ET",
                ]
            )
            y -= line_step
        stream = "\n".join(content_lines).encode("latin-1", "replace")

        objects.append(
            f"{page_object_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {stream_object_id} 0 R >> endobj\n".encode("ascii")
        )
        objects.append(
            f"{stream_object_id} 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
            + stream
            + b"\nendstream endobj\n"
        )

    pages_object = f"2 0 obj << /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >> endobj\n".encode("ascii")
    objects[1] = pages_object

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _build_invoice_pdf(invoice: BillingInvoice) -> bytes:
    company_profile = get_billing_company_profile()
    seller_name = company_profile.company_name or "AI Novel Creator"
    seller_email = company_profile.company_email or ""
    seller_address = company_profile.company_address or ""
    seller_tax = company_profile.company_tax_id or ""

    buyer_name = invoice.buyer_name or invoice.user.get_username()
    buyer_email = invoice.buyer_email or ""
    buyer_address = invoice.buyer_address or ""
    issue_date = invoice.issue_date.isoformat() if invoice.issue_date else "N/A"
    due_date = invoice.due_date.isoformat() if invoice.due_date else "N/A"
    paid_at = timezone.localtime(invoice.paid_at).strftime("%Y-%m-%d %H:%M UTC") if invoice.paid_at else "N/A"
    status_text = (invoice.status or "draft").upper()
    description = invoice.description or "Billing invoice"
    currency = (invoice.currency or "GBP").upper()
    logo = _load_invoice_logo_png()

    commands: list[str] = []
    # Header bar
    _pdf_set_fill(commands, 0.09, 0.16, 0.30)
    _pdf_fill_rect(commands, 0, 718, 612, 74)
    if logo:
        logo_width, logo_height, _ = logo
        target_height = 40.0
        target_width = target_height * (logo_width / logo_height)
        _pdf_draw_image(commands, x=334, y=734, width=target_width, height=target_height, image_name="Im1")
    _pdf_draw_text(commands, x=44, y=764, text="INVOICE", font="/F2", size=24, color=(1.0, 1.0, 1.0))
    _pdf_draw_text(commands, x=44, y=744, text=seller_name, font="/F2", size=11, color=(0.89, 0.94, 1.0))
    _pdf_draw_text(commands, x=430, y=760, text=f"#{invoice.public_number}", font="/F2", size=12, color=(1.0, 1.0, 1.0))
    _pdf_draw_text(commands, x=430, y=742, text=f"STATUS: {status_text}", font="/F2", size=9.5, color=(0.84, 0.90, 1.0))

    # Meta details strip
    _pdf_set_fill(commands, 0.96, 0.97, 0.99)
    _pdf_fill_rect(commands, 44, 676, 524, 30)
    _pdf_set_stroke(commands, 0.84, 0.87, 0.93)
    _pdf_stroke_rect(commands, 44, 676, 524, 30)
    _pdf_draw_text(commands, x=52, y=688, text=f"Issue: {issue_date}", font="/F2", size=9.2)
    _pdf_draw_text(commands, x=192, y=688, text=f"Due: {due_date}", font="/F2", size=9.2)
    _pdf_draw_text(commands, x=330, y=688, text=f"Paid at: {paid_at}", font="/F2", size=9.2)
    _pdf_draw_text(commands, x=505, y=688, text=f"Currency: {currency}", font="/F2", size=9.2, color=(0.22, 0.28, 0.38))

    # Seller / Bill to cards
    _pdf_set_fill(commands, 0.99, 0.99, 1.0)
    _pdf_fill_rect(commands, 44, 560, 252, 104)
    _pdf_fill_rect(commands, 316, 560, 252, 104)
    _pdf_set_stroke(commands, 0.84, 0.87, 0.93)
    _pdf_stroke_rect(commands, 44, 560, 252, 104)
    _pdf_stroke_rect(commands, 316, 560, 252, 104)

    _pdf_draw_text(commands, x=56, y=648, text="FROM", font="/F2", size=9.5, color=(0.32, 0.39, 0.50))
    seller_lines = [seller_name]
    seller_lines.extend(_pdf_wrap_lines(seller_email, width=34) if seller_email else [])
    seller_lines.extend(_pdf_wrap_lines(seller_address, width=34) if seller_address else [])
    if seller_tax:
        seller_lines.extend(_pdf_wrap_lines(f"Tax ID: {seller_tax}", width=34))
    _pdf_draw_text_lines(commands, x=56, y=634, lines=seller_lines[:6], size=9.4, leading=12.0)

    _pdf_draw_text(commands, x=328, y=648, text="BILL TO", font="/F2", size=9.5, color=(0.32, 0.39, 0.50))
    buyer_lines = [buyer_name]
    buyer_lines.extend(_pdf_wrap_lines(buyer_email, width=34) if buyer_email else [])
    buyer_lines.extend(_pdf_wrap_lines(buyer_address, width=34) if buyer_address else [])
    _pdf_draw_text_lines(commands, x=328, y=634, lines=buyer_lines[:6], size=9.4, leading=12.0)

    # Description table
    _pdf_set_fill(commands, 0.17, 0.25, 0.40)
    _pdf_fill_rect(commands, 44, 530, 524, 24)
    _pdf_draw_text(commands, x=54, y=538, text="Description", font="/F2", size=10, color=(1.0, 1.0, 1.0))
    _pdf_draw_text(commands, x=516, y=538, text="Amount", font="/F2", size=10, color=(1.0, 1.0, 1.0))
    _pdf_set_stroke(commands, 0.84, 0.87, 0.93)
    _pdf_stroke_rect(commands, 44, 430, 524, 100)
    _pdf_draw_line(commands, 478, 430, 478, 530)

    desc_lines = _pdf_wrap_lines(description, width=66)[:6]
    _pdf_draw_text_lines(commands, x=54, y=514, lines=desc_lines, size=9.8, leading=12.5)
    _pdf_draw_text(commands, x=492, y=514, text=format_minor_amount(invoice.total_amount, invoice.currency), font="/F2", size=10.2)

    # Totals box
    _pdf_set_fill(commands, 0.98, 0.99, 1.0)
    _pdf_fill_rect(commands, 328, 322, 240, 98)
    _pdf_set_stroke(commands, 0.84, 0.87, 0.93)
    _pdf_stroke_rect(commands, 328, 322, 240, 98)

    totals = _invoice_totals_for_pdf(invoice)
    y = 404.0
    for label, value in totals:
        is_key_total = label in {"Total", "Amount due"}
        _pdf_draw_text(commands, x=340, y=y, text=label, font="/F2" if is_key_total else "/F1", size=9.6)
        _pdf_draw_text(commands, x=500, y=y, text=value, font="/F2" if is_key_total else "/F1", size=9.6)
        y -= 17.5

    # Notes area
    notes_title_y = 300.0
    _pdf_draw_text(commands, x=44, y=notes_title_y, text="Notes", font="/F2", size=10.0, color=(0.32, 0.39, 0.50))
    notes_lines = _pdf_wrap_lines(invoice.notes or "Thank you for your business.", width=94)[:6]
    _pdf_set_fill(commands, 0.99, 0.99, 1.0)
    _pdf_fill_rect(commands, 44, 212, 524, 82)
    _pdf_set_stroke(commands, 0.84, 0.87, 0.93)
    _pdf_stroke_rect(commands, 44, 212, 524, 82)
    _pdf_draw_text_lines(commands, x=54, y=277, lines=notes_lines, size=9.2, leading=11.8)

    # Footer
    _pdf_draw_line(commands, 44, 188, 568, 188)
    _pdf_draw_text(
        commands,
        x=44,
        y=174,
        text=f"Generated by AI Novel Creator billing - Invoice {invoice.public_number}",
        font="/F1",
        size=8.4,
        color=(0.44, 0.49, 0.57),
    )

    stream = "\n".join(commands).encode("latin-1", "replace")
    page_resources = "/ProcSet [/PDF /Text"
    content_object_id = 6
    image_object = None
    if logo:
        logo_width, logo_height, logo_data = logo
        image_object = (
            f"6 0 obj << /Type /XObject /Subtype /Image /Width {logo_width} /Height {logo_height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
            f"/DecodeParms << /Predictor 15 /Colors 3 /BitsPerComponent 8 /Columns {logo_width} >> "
            f"/Length {len(logo_data)} >> stream\n".encode("ascii")
            + logo_data
            + b"\nendstream endobj\n"
        )
        page_resources += " /ImageC"
        content_object_id = 7
    page_resources += "]"
    xobject_dict = " /XObject << /Im1 6 0 R >>" if logo else ""
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << {page_resources} /Font << /F1 4 0 R /F2 5 0 R >>{xobject_dict} >> /Contents {content_object_id} 0 R >> endobj\n".encode("ascii"),
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n",
        f"{content_object_id} 0 obj << /Length {len(stream)} >> stream\n".encode("ascii") + stream + b"\nendstream endobj\n",
    ]
    if image_object:
        objects.insert(5, image_object)

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


@login_required
def download_billing_invoice_pdf(request, pk):
    invoice = _get_billing_invoice_for_request(request, pk)
    response = HttpResponse(_build_invoice_pdf(invoice), content_type="application/pdf")
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", invoice.public_number or "invoice").strip("-") or "invoice"
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


@login_required
def download_full_novel_pdf(request, slug):
    project = _get_project_for_user(request, slug)
    full_novel_context = _build_full_novel_context(project)
    heading_token = "[[H]]"
    act_heading_token = "[[ACT]]"
    chapter_heading_token = "[[CHAPTER]]"

    pdf_lines: list[str] = []
    title = (project.title or "").strip() or "Untitled project"
    pdf_lines.append(f"{heading_token}{title} - Full novel")
    pdf_lines.append("")
    pdf_lines.append(f"{heading_token}Table of contents")
    pdf_lines.append("")

    outline_tree = full_novel_context["outline_tree"]
    if outline_tree:
        for act_item in outline_tree:
            act_title = (act_item["act"]["title"] or "").strip() or "Untitled act"
            pdf_lines.append(f"{heading_token}ACT: {act_title}")
            for chapter in act_item["chapters"]:
                chapter_title = (chapter["title"] or "").strip() or "Untitled chapter"
                pdf_lines.append(f"{heading_token}CHAPTER: {chapter_title}")
                for scene in chapter["scenes"]:
                    scene_title = (scene["title"] or "").strip() or "Untitled scene"
                    pdf_lines.append(f"{heading_token}SCENE: {scene_title}")
            pdf_lines.append("")
    else:
        pdf_lines.append("No outline entries yet.")
        pdf_lines.append("")

    pdf_lines.append(f"{heading_token}Manuscript")
    pdf_lines.append("")

    manuscript_acts = full_novel_context["manuscript_acts"]
    if manuscript_acts:
        for act in manuscript_acts:
            for chapter in act["chapters"]:
                pdf_lines.append("\f")
                pdf_lines.append(f"{act_heading_token}Act: {act['title']}")
                pdf_lines.append(f"{chapter_heading_token}{chapter['title']}")
                pdf_lines.append("")
                for scene in chapter["scenes"]:
                    scene_title = (scene["title"] or "").strip()
                    if scene_title:
                        pdf_lines.append(f"{heading_token}{scene_title}")
                    text = (scene["text"] or "").strip()
                    if text:
                        pdf_lines.extend(text.splitlines())
                    pdf_lines.append("")
    else:
        pdf_lines.append("No final text yet. Render scenes first.")

    response = HttpResponse(
        _build_paginated_text_pdf(
            pdf_lines,
            page_break_token="\f",
            heading_token=heading_token,
            act_heading_token=act_heading_token,
            chapter_heading_token=chapter_heading_token,
        ),
        content_type="application/pdf",
    )
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", project.slug or "full-novel").strip("-") or "full-novel"
    response["Content-Disposition"] = f'attachment; filename="{filename}-full-novel.pdf"'
    return response


@require_POST
@login_required
def archive_project(request, slug):
    project = _get_project_for_user(request, slug)
    if not project.is_archived:
        project.is_archived = True
        project.save(update_fields=["is_archived", "updated_at"])
        messages.success(request, "Project archived.")
    return HttpResponseRedirect(_get_project_redirect_url(request, reverse("project-archive-list")))


@require_POST
@login_required
def restore_project(request, slug):
    project = _get_project_for_user(request, slug)
    if project.is_archived:
        project.is_archived = False
        project.save(update_fields=["is_archived", "updated_at"])
        messages.success(request, "Project restored.")
    return HttpResponseRedirect(_get_project_redirect_url(request, reverse("project-list")))


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = NovelProject
    template_name = "main/project_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return _project_queryset_for_user(self.request.user)


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


def _coerce_text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(parts).strip()
    return str(value).strip()


def _clean_generated_home_update_title(value) -> str:
    title = _coerce_text_value(value).strip().strip('"').strip("'")
    title = re.sub(r"^\*+\s*|\s*\*+$", "", title)
    title = re.sub(r"^#+\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip(" .:-")
    if not title:
        return ""
    if len(title) > 80:
        shortened = title[:77].rsplit(" ", 1)[0].strip()
        title = (shortened or title[:77]).rstrip(" ,;:-") + "..."
    return title


def _clean_generated_home_update_body(value) -> str:
    text = _coerce_text_value(value).strip().strip('"').strip("'")
    if not text:
        return ""

    text = re.sub(r"^```(?:json|text)?\s*|\s*```$", "", text, flags=re.I)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        cleaned = re.sub(r"\s+", " ", paragraph).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs).strip()


def _extract_home_update_generation(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Model response was empty.")

    candidate_dicts = []
    try:
        data = json.loads(_extract_json_object(text))
        if isinstance(data, dict):
            candidate_dicts.append(data)
            for nested_key in ("result", "update", "response", "data", "output"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    candidate_dicts.append(nested)
    except Exception:
        pass

    title_keys = ("title", "headline", "subject", "name")
    body_keys = ("body", "description", "summary", "content", "copy", "update")

    for data in candidate_dicts:
        title = next(
            (_clean_generated_home_update_title(data.get(key)) for key in title_keys if _clean_generated_home_update_title(data.get(key))),
            "",
        )
        body = next(
            (_clean_generated_home_update_body(data.get(key)) for key in body_keys if _clean_generated_home_update_body(data.get(key))),
            "",
        )
        if title and body:
            return title, body

    title_match = re.search(r"(?im)^\s*(?:title|headline)\s*:\s*(.+?)\s*$", text)
    body_match = re.search(r"(?ims)^\s*(?:body|summary|description|content)\s*:\s*(.+)$", text)
    if title_match and body_match:
        title = _clean_generated_home_update_title(title_match.group(1))
        body = _clean_generated_home_update_body(body_match.group(1))
        if title and body:
            return title, body

    markdown_title_match = re.search(r"(?im)^\s*(?:\*\*|__)?(?:title|headline)(?:\*\*|__)?\s*:\s*(.+?)\s*$", text)
    markdown_body_match = re.search(
        r"(?ims)^\s*(?:\*\*|__)?(?:body|summary|description|content)(?:\*\*|__)?\s*:\s*(.+)$",
        text,
    )
    if markdown_title_match and markdown_body_match:
        title = _clean_generated_home_update_title(markdown_title_match.group(1))
        body = _clean_generated_home_update_body(markdown_body_match.group(1))
        if title and body:
            return title, body

    lines = [line.strip(" -*\t") for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        title = _clean_generated_home_update_title(lines[0].replace("Title:", "").replace("Headline:", "").strip())
        body = _clean_generated_home_update_body("\n".join(lines[1:]))
        if title and body:
            return title, body

    body = _clean_generated_home_update_body(text)
    if body and not _looks_like_low_signal_home_update_text(body):
        title = _summarize_home_update_title(body)
        if title:
            return title, body

    raise ValueError("Model response must include title and body.")


def _normalize_home_update_compare_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).strip(" .!?").lower()


def _extract_home_update_action_subject(text: str) -> tuple[str, str]:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    first_line = ""
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if line:
            first_line = line
            break

    if not first_line:
        return "", ""

    first_line = re.sub(r"^\s*(feat|fix|chore|refactor|docs|style|test|tests|perf)\s*:\s*", "", first_line, flags=re.I)
    first_line = re.sub(r"^\s*[-*#]+\s*", "", first_line)
    first_line = re.sub(r"\s+", " ", first_line).strip(" .,:;/-")

    match = re.match(
        r"(?i)^(add|create|introduce|improve|refine|rework|fix|update|polish|simplify|streamline|remove|rename|highlight|make)\s+(?:the\s+)?(.+?)(?:\s+to\b|,\s*|\s+with\b|\s+for\b|\s+so\b|\s+and\b|[.!?]|$)",
        first_line,
    )
    if not match:
        return "", ""

    verb = match.group(1).lower()
    subject = match.group(2).strip(" ,;:-")
    subject = re.sub(r"['\"`]+", "", subject)
    subject = re.sub(r"\b(html|css|js|django|gpt-?5|gpt-?4o|o3|o4-mini)\b", "", subject, flags=re.I)
    subject = re.sub(r"\s+", " ", subject).strip(" ,;:-")
    return verb, subject


def _summarize_home_update_title(text: str) -> str:
    normalized = _normalize_home_update_compare_text(text)
    if not normalized:
        return "Product update"

    if "model" in normalized and any(keyword in normalized for keyword in ("select", "selector", "switch", "choose", "preferred")):
        return "Added AI model selector"
    if "archive" in normalized and "project" in normalized:
        return "Added project archive"
    if "dashboard" in normalized and any(keyword in normalized for keyword in ("design", "layout", "graphic", "hero")):
        return "Redesigned dashboard"
    if "git commit" in normalized or ("commit description" in normalized and "git add" in normalized):
        return "Added commit helper"

    verb, subject = _extract_home_update_action_subject(text)
    if subject:
        verb_map = {
            "add": "Added",
            "create": "Added",
            "introduce": "Added",
            "improve": "Improved",
            "refine": "Refined",
            "rework": "Reworked",
            "fix": "Fixed",
            "update": "Updated",
            "polish": "Polished",
            "simplify": "Simplified",
            "streamline": "Streamlined",
            "remove": "Removed",
            "rename": "Renamed",
            "highlight": "Highlighted",
            "make": "Updated",
        }
        if len(subject) > 42:
            shortened = subject[:39].rsplit(" ", 1)[0].strip()
            subject = (shortened or subject[:39]).rstrip(" ,;:-") + "..."
        return f"{verb_map.get(verb, 'Updated')} {subject}"

    first_sentence_match = re.match(r"(.+?[.!?])(?:\s|$)", (text or "").strip())
    first_sentence = first_sentence_match.group(1) if first_sentence_match else (text or "")
    first_sentence = _clean_generated_home_update_title(first_sentence)
    if first_sentence:
        return first_sentence
    return "Product update"


def _looks_like_low_signal_home_update_text(text: str) -> bool:
    normalized = _normalize_home_update_compare_text(text)
    if not normalized:
        return True
    if normalized in {"text", "body", "copy", "content", "update", "summary", "description"}:
        return True
    words = [part for part in normalized.split(" ") if part]
    return len(words) <= 2 and len(normalized) <= 18


def _looks_like_raw_home_update_output(source: str, title: str, body: str) -> bool:
    combined = _normalize_home_update_compare_text(f"{title} {body}")
    source_text = _normalize_home_update_compare_text(source)
    if not combined:
        return True

    raw_markers = (
        "render as",
        "git add",
        "git commit",
        "git push",
        "/main description/",
        "/description/",
        "don't use \\n",
    )
    if any(marker in combined for marker in raw_markers):
        return True

    first_sentence_match = re.match(r"(.+?[.!?])(?:\s|$)", (body or "").strip())
    first_sentence = first_sentence_match.group(1) if first_sentence_match else (body or "").strip()
    normalized_title = _normalize_home_update_compare_text(title)
    normalized_first_sentence = _normalize_home_update_compare_text(first_sentence)
    if normalized_title == normalized_first_sentence:
        return True
    if normalized_first_sentence.startswith(normalized_title) and len(normalized_title) >= 28:
        return True

    if any(marker in source_text for marker in raw_markers):
        source_words = {word for word in re.findall(r"[a-z0-9]+", source_text) if len(word) > 2}
        output_words = {word for word in re.findall(r"[a-z0-9]+", combined) if len(word) > 2}
        if source_words and output_words:
            overlap_ratio = len(source_words & output_words) / max(1, len(output_words))
            if overlap_ratio >= 0.78:
                return True

    return _looks_like_low_signal_home_update_text(title) or _looks_like_low_signal_home_update_text(body)


def _build_home_update_fallback(text: str) -> tuple[str, str]:
    raw_text = (text or "").strip()
    if not raw_text:
        return "Product update", ""

    title = _summarize_home_update_title(raw_text)
    verb, subject = _extract_home_update_action_subject(raw_text)
    subject_text = subject or "the latest workflow changes"

    body_map = {
        "add": f"Added {subject_text} to make the workflow clearer and easier to use.",
        "create": f"Added {subject_text} to make the workflow clearer and easier to use.",
        "introduce": f"Added {subject_text} to make the workflow clearer and easier to use.",
        "improve": f"Improved {subject_text} to make the experience smoother for users.",
        "refine": f"Refined {subject_text} to make the experience smoother for users.",
        "rework": f"Reworked {subject_text} to improve clarity and day-to-day usability.",
        "fix": f"Fixed issues around {subject_text} so the workflow behaves more reliably.",
        "update": f"Updated {subject_text} to improve the overall experience.",
        "polish": f"Polished {subject_text} to make it feel cleaner and more consistent.",
        "simplify": f"Simplified {subject_text} to reduce friction for users.",
        "streamline": f"Streamlined {subject_text} to speed up the workflow.",
        "remove": f"Removed outdated controls around {subject_text} to simplify the interface.",
        "rename": f"Renamed {subject_text} to make the interface easier to understand.",
        "highlight": f"Highlighted {subject_text} so important status is easier to spot.",
        "make": f"Updated {subject_text} to improve the overall experience.",
    }
    body = body_map.get(verb, "Updated the app based on the latest internal changes to improve the user experience.")
    return title, body


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


def _extract_story_bible_suggestions(raw_text: str) -> dict[str, str]:
    text = (raw_text or "").strip()
    if not text:
        return {}

    try:
        payload = _extract_json_object(text)
        data = json.loads(payload) if payload else {}
        if isinstance(data, dict):
            out = {}
            for key in ("summary_md", "constraints", "facts"):
                value = str(data.get(key) or "").strip()
                if value:
                    out[key] = value
            if out:
                return out
    except Exception:
        pass

    label_map = {
        "summary": "summary_md",
        "summary_md": "summary_md",
        "summary md": "summary_md",
        "constraints": "constraints",
        "facts": "facts",
    }
    pattern = re.compile(
        r"(?ims)(?:^|\n)\s*(summary(?:[_\s]?md)?|constraints|facts)\s*:?\s*(.+?)(?=\n\s*(?:summary(?:[_\s]?md)?|constraints|facts)\s*:|\Z)"
    )
    labeled = {}
    for match in pattern.finditer(text):
        raw_label = re.sub(r"\s+", " ", (match.group(1) or "").replace("_", " ").strip().lower())
        key = label_map.get(raw_label)
        if not key:
            continue
        value = (match.group(2) or "").strip()
        value = re.sub(r"^\s*[-*]\s*", "", value)
        if value:
            labeled[key] = value
    if labeled:
        return labeled

    return {}


_BRACE_SEGMENT_RE = re.compile(r"\{[^{}]*\}")
_TARGETED_SEGMENT_RE = re.compile(r"!\{[^{}]*\}!")


def _split_braced_segments(text: str) -> list[dict[str, str | bool]]:
    parts = re.split(r"(\{[^{}]*\})", text)
    segments: list[dict[str, str | bool]] = []
    for part in parts:
        if part == "":
            continue
        protected = bool(_BRACE_SEGMENT_RE.fullmatch(part))
        segments.append({"text": part, "protected": protected})
    return segments


def _split_targeted_segments(text: str) -> list[dict[str, str | bool]]:
    parts = re.split(r"(!\{[^{}]*\}!)", text)
    segments: list[dict[str, str | bool]] = []
    for part in parts:
        if part == "":
            continue
        targeted = bool(_TARGETED_SEGMENT_RE.fullmatch(part))
        segments.append({"text": part[2:-2] if targeted else part, "targeted": targeted})
    return segments


def _strip_draft_markers(text: str) -> str:
    cleaned = re.sub(r"!\{([^{}]*)\}!", r"\1", text or "")
    return cleaned.replace("{", "").replace("}", "")


@require_POST
@login_required
def brainstorm_location_description(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if description:
        return JsonResponse({"ok": True, "suggestions": {}})

    try:
        objects_map = _parse_location_objects(request.POST)
    except Exception as e:
        return _json_internal_error()

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 450}
        data = _call_tracked_llm_json_object(
            project=project,
            action_label="Location Brainstorm",
            prompt=prompt,
            model_name=model_name,
            params=params,
        )
        text = str(data.get("description") or "").strip()
        if not text:
            return JsonResponse({"ok": True, "suggestions": {}})
        return JsonResponse({"ok": True, "suggestions": {"description": text}})
    except Exception as e:
        return _json_internal_error()


@require_POST
@login_required
def add_location_details(request, slug):
    project = _get_project_for_user(request, slug)
    blocked = _ensure_json_ai_request(request)
    if blocked is not None:
        return blocked

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    try:
        objects_map = _parse_location_objects(request.POST)
    except Exception as e:
        return _json_internal_error()

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.7, "max_tokens": 450}
        data = _call_tracked_llm_json_object(
            project=project,
            action_label="Location Add Details",
            prompt=prompt,
            model_name=model_name,
            params=params,
        )
        text = _dedupe_appended_text(description, str(data.get("description") or ""))
        if not text:
            return JsonResponse({"ok": True, "suggestions": {}})
        return JsonResponse({"ok": True, "suggestions": {"description": text}})
    except Exception as e:
        return _json_internal_error()


@require_POST
@login_required
def extract_location_objects(request, slug):
    project = _get_project_for_user(request, slug)
    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)
    blocked = _subscription_required_response(request, wants_json=True)
    if blocked is not None:
        return blocked

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not description:
        return JsonResponse({"ok": False, "error": "Description is required."}, status=400)

    try:
        existing_objects = _parse_location_objects(request.POST)
    except Exception as e:
        return _json_internal_error()

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
        model_name = get_user_text_model(request.user)
        params = {"temperature": 0.4, "max_tokens": 550}
        result = _call_tracked_llm(
            project=project,
            action_label="Location Extract Objects",
            prompt=prompt,
            model_name=model_name,
            params=params,
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
        return _json_internal_error()


@require_POST
@login_required
def generate_location_image(request, slug, pk):
    project = _get_project_for_user(request, slug)
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return JsonResponse({"ok": False, "error": "Image generation is not configured."}, status=400)
    blocked = _subscription_required_response(request, wants_json=True)
    if blocked is not None:
        return blocked

    location = get_object_or_404(Location, id=pk, project=project)

    def get_text(field, fallback):
        value = request.POST.get(field)
        if value is None:
            value = fallback
        return (value or "").strip()

    name = get_text("name", location.name)
    description = get_text("description", location.description)

    if not name:
        return JsonResponse({"ok": False, "error": "Location name is required."}, status=400)

    prompt_lines = [
        "Create a vivid establishing shot illustration of a fictional place.",
        "Style: semi-realistic, cinematic lighting, wide shot.",
        "No people in frame unless implied by the location.",
        "No text, no logos, no watermarks.",
        "",
        "Location details:",
        "Name: " + name,
    ]
    if description:
        prompt_lines.append("Description: " + description)

    try:
        objects_map = _parse_location_objects(request.POST)
        if objects_map:
            prompt_lines.append("Notable objects: " + json.dumps(objects_map, ensure_ascii=False))
    except Exception:
        pass

    model_name = getattr(settings, "OPENAI_IMAGE_MODEL", "gpt-image-1")
    fallback_model = getattr(settings, "OPENAI_IMAGE_FALLBACK_MODEL", "")
    if not fallback_model and model_name == "gpt-image-1":
        fallback_model = "dall-e-3"

    try:
        data_url = generate_image_data_url(
            prompt="\n".join(prompt_lines),
            model_name=model_name,
            size="1024x1024",
        )
    except Exception as e:
        if fallback_model and fallback_model != model_name:
            try:
                data_url = generate_image_data_url(
                    prompt="\n".join(prompt_lines),
                    model_name=fallback_model,
                    size="1024x1024",
                )
            except Exception as fallback_error:
                return JsonResponse({"ok": False, "error": str(fallback_error)}, status=400)
        else:
            return _json_internal_error()

    location.image_data_url = data_url
    location.save(update_fields=["image_data_url", "updated_at"])
    return JsonResponse({"ok": True, "image_url": data_url})


class LocationListView(LoginRequiredMixin, ListView):
    model = Location
    template_name = "main/location_list.html"
    context_object_name = "locations"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        Location.get_or_create_root_for_project(self.project)
        qs = Location.objects.filter(project=self.project).select_related("parent").order_by("name")
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["location_tree"] = build_location_tree(ctx["locations"])
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
        ctx["billing_enabled"] = billing_enabled()
        ctx["has_active_plan"] = user_has_active_plan(self.request.user)
        ctx["ai_billing_url"] = _get_billing_url(self.request, reason="active-plan")
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        ctx["object_rows"] = sorted((self.object.objects_map or {}).items(), key=lambda kv: kv[0].lower())
        ctx["next_url"] = (self.request.GET.get("next") or "").strip()
        ctx["billing_enabled"] = billing_enabled()
        ctx["has_active_plan"] = user_has_active_plan(self.request.user)
        ctx["ai_billing_url"] = _get_billing_url(self.request, reason="active-plan")
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
        self.object = self.get_object()
        if self.object.is_root:
            messages.error(self.request, "The root location cannot be deleted.")
            return HttpResponseRedirect(
                reverse("location-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})
            )
        try:
            self.object.delete()
        except ProtectedError:
            messages.error(
                self.request,
                "This location has nested child locations. Move or delete those first.",
            )
            return HttpResponseRedirect(
                reverse("location-edit", kwargs={"slug": self.project.slug, "pk": self.object.id})
            )

        messages.success(self.request, "Location deleted.")
        return HttpResponseRedirect(self.get_success_url())


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = NovelProject
    form_class = NovelProjectForm
    template_name = "main/project_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        Location.get_or_create_root_for_project(self.object)
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
        return _project_queryset_for_user(self.request.user)

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
        return _project_queryset_for_user(self.request.user)

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
        return _project_queryset_for_user(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        project = self.object

        try:
            ctx["bible"] = project.bible
        except StoryBible.DoesNotExist:
            ctx["bible"] = None

        ctx["recent_runs"] = project.runs.order_by("-created_at")[:10]

        if not OutlineNode.objects.filter(project=project).exists():
            generate_outline(str(project.id))

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
        rendered_texts = OutlineNode.objects.filter(
            project=project,
            node_type=OutlineNode.NodeType.SCENE,
        ).values_list("rendered_text", flat=True)
        current_word_count = 0
        for text in rendered_texts:
            if text:
                current_word_count += len(str(text).split())
        ctx["current_word_count"] = current_word_count
        ctx["act_count"] = len(acts)
        ctx["chapter_count"] = sum(len(item["chapters"]) for item in outline_tree)
        ctx["scene_count"] = sum(len(chapter_item["scenes"]) for act_item in outline_tree for chapter_item in act_item["chapters"])
        ctx["character_count"] = project.characters.count()
        ctx["location_count"] = project.locations.filter(is_root=False).count()
        ctx["run_count"] = project.runs.count()
        target_word_count = project.target_word_count or 0
        ctx["progress_percent"] = min(100, round((current_word_count / target_word_count) * 100)) if target_word_count else 0
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        project = self.object
        action = request.POST.get("action", "")

        if action in {"generate_bible", "generate_outline", "generate_all_scenes"}:
            blocked = _subscription_required_response(request)
            if blocked is not None:
                return blocked

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


def _build_full_novel_context(project: NovelProject) -> dict:
    nodes = (
        OutlineNode.objects.filter(project=project)
        .only("id", "node_type", "parent_id", "order", "created_at", "title", "rendered_text", "pov", "location")
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

    outline_tree = []
    manuscript_acts = []
    for act in acts:
        act_title = (act.title or "").strip() or "Untitled act"
        toc_chapters = []
        manuscript_chapters = []

        for chapter in chapters_by_act.get(act.id, []):
            chapter_title = (chapter.title or "").strip() or "Untitled chapter"
            toc_scenes = []
            manuscript_scenes = []

            for scene in scenes_by_chapter.get(chapter.id, []):
                text = (scene.rendered_text or "").strip()
                scene_title = (scene.title or "").strip() or "Untitled scene"
                scene_anchor = f"scene-{scene.id}" if text else ""
                toc_scenes.append(
                    {
                        "title": scene_title,
                        "anchor": scene_anchor,
                        "pov": (scene.pov or "").strip(),
                        "location": (scene.location or "").strip(),
                    }
                )
                if text:
                    manuscript_scenes.append(
                        {
                            "title": scene_title,
                            "anchor": scene_anchor,
                            "text": text,
                        }
                    )

            chapter_anchor = f"chapter-{chapter.id}" if manuscript_scenes else ""
            toc_chapters.append(
                {
                    "title": chapter_title,
                    "anchor": chapter_anchor,
                    "scenes": toc_scenes,
                }
            )
            if manuscript_scenes:
                manuscript_chapters.append(
                    {
                        "title": chapter_title,
                        "anchor": chapter_anchor,
                        "scenes": manuscript_scenes,
                    }
                )

        act_anchor = f"act-{act.id}" if manuscript_chapters else ""
        outline_tree.append(
            {
                "act": {
                    "title": act_title,
                    "anchor": act_anchor,
                },
                "chapters": toc_chapters,
            }
        )
        if manuscript_chapters:
            manuscript_acts.append(
                {
                    "title": act_title,
                    "anchor": act_anchor,
                    "chapters": manuscript_chapters,
                }
            )

    chapter_sections = [
        {
            "title": chapter["title"],
            "anchor": chapter["anchor"],
            "text": "\n\n".join(scene["text"] for scene in chapter["scenes"]),
        }
        for act in manuscript_acts
        for chapter in act["chapters"]
    ]

    return {
        "outline_tree": outline_tree,
        "manuscript_acts": manuscript_acts,
        "chapter_sections": chapter_sections,
        "full_text": "\n\n".join(chapter["text"] for chapter in chapter_sections),
    }


class FullNovelView(LoginRequiredMixin, DetailView):
    model = NovelProject
    template_name = "main/full_novel.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    context_object_name = "project"

    def get_queryset(self):
        return _project_queryset_for_user(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_build_full_novel_context(self.object))
        return ctx


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
        ctx.setdefault("upload_form", StoryBiblePdfUploadForm())
        try:
            ctx["uploaded_documents"] = self.object.documents.all()
        except (OperationalError, ProgrammingError):
            ctx["uploaded_documents"] = []
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Story bible saved.")
        return response

    def get_success_url(self):
        return reverse_lazy("bible-edit", kwargs={"slug": self.project.slug})

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if (request.POST.get("action") or "").strip() == "upload_pdf":
            upload_form = StoryBiblePdfUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                uploaded_file = upload_form.cleaned_data["pdf_file"]
                try:
                    extracted_text, page_count = _extract_story_bible_pdf(uploaded_file)
                except ValueError as exc:
                    upload_form.add_error("pdf_file", str(exc))
                else:
                    try:
                        uploaded_file.seek(0)
                    except Exception:
                        pass
                    StoryBibleDocument.objects.create(
                        story_bible=self.object,
                        file=uploaded_file,
                        original_name=(uploaded_file.name or "").strip(),
                        file_size=int(getattr(uploaded_file, "size", 0) or 0),
                        page_count=page_count,
                        extracted_text=extracted_text,
                        extracted_text_chars=len(extracted_text),
                    )
                    messages.success(request, "PDF reference uploaded.")
                    return HttpResponseRedirect(self.get_success_url())

            form = self.get_form()
            return self.render_to_response(self.get_context_data(form=form, upload_form=upload_form))

        return super().post(request, *args, **kwargs)


class StoryBibleDocumentDetailView(LoginRequiredMixin, DetailView):
    model = StoryBibleDocument
    template_name = "main/bible_document_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = _get_project_for_user(request, kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return StoryBibleDocument.objects.select_related("story_bible__project").filter(story_bible__project=self.project)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["project"] = self.project
        return ctx


@require_POST
@login_required
def delete_story_bible_document(request, slug: str, pk):
    project = _get_project_for_user(request, slug)
    try:
        document = get_object_or_404(
            StoryBibleDocument.objects.select_related("story_bible__project"),
            id=pk,
            story_bible__project=project,
        )
    except (OperationalError, ProgrammingError):
        messages.error(request, "PDF deletion is unavailable until migrations are applied.")
        return HttpResponseRedirect(reverse("bible-edit", kwargs={"slug": project.slug}))

    storage = document.file.storage
    file_name = document.file.name
    document.delete()
    if file_name:
        try:
            storage.delete(file_name)
        except Exception:
            pass

    messages.success(request, "PDF deleted.")
    return HttpResponseRedirect(reverse("bible-edit", kwargs={"slug": project.slug}))


class SuperuserRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return bool(self.request.user.is_authenticated and self.request.user.is_superuser)


class BillingCompanyProfileUpdateView(LoginRequiredMixin, SuperuserRequiredMixin, UpdateView):
    model = BillingCompanyProfile
    form_class = BillingCompanyProfileForm
    template_name = "main/billing_company_profile_form.html"

    def get_object(self, queryset=None):
        return get_billing_company_profile()

    def form_valid(self, form):
        messages.success(self.request, "Company details saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("billing")


class HomeUpdateCreateView(LoginRequiredMixin, SuperuserRequiredMixin, CreateView):
    model = HomeUpdate
    form_class = HomeUpdateForm
    template_name = "main/home_update_form.html"

    def form_valid(self, form):
        form.instance.title = (form.cleaned_data.get("title") or "").strip()
        form.instance.body = (form.cleaned_data.get("body") or "").strip()
        response = super().form_valid(form)
        messages.success(self.request, "Update posted.")
        return response

    def get_success_url(self):
        return reverse_lazy("home")


@require_POST
@login_required
def regenerate_home_update(request):
    if not request.user.is_superuser:
        return JsonResponse({"ok": False, "error": "Forbidden."}, status=403)

    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in (
        request.headers.get("accept") or ""
    )
    if not wants_json:
        return JsonResponse({"ok": False, "error": "JSON requests only."}, status=400)

    body = (request.POST.get("body") or "").strip()
    if not body:
        return JsonResponse({"ok": False, "error": "Paste raw git text into Body text first."}, status=400)

    prompt_lines = [
        "You are writing release notes for end users.",
        "Goal: turn raw git or technical change notes into a short user-facing update.",
        "Rules:",
        '- Return STRICT JSON only in the form: {"title":"...","body":"..."}',
        "- Title: 2 to 6 words, short product-style summary, no trailing period, no quotes.",
        "- Body: 1 to 3 concise sentences in plain language for users.",
        "- Explain the user-visible improvement or benefit.",
        "- Remove git commands, commit formatting, file names, and implementation details unless users need them.",
        "- Do not use bullet points or markdown.",
        '- Never answer with placeholder words like "text", "body", "copy", or "content".',
        "",
        "Raw developer notes:",
        body,
    ]
    prompt = "\n".join(prompt_lines).strip()

    try:
        result = call_llm(
            prompt=prompt,
            model_name=get_user_text_model(request.user),
            params={"temperature": 0.6, "max_tokens": 400},
        )
        title, rewritten_body = _extract_home_update_generation(result.text)
        if _looks_like_raw_home_update_output(body, title, rewritten_body):
            fallback_title, fallback_body = _build_home_update_fallback(body)
            return JsonResponse(
                {
                    "ok": True,
                    "title": fallback_title,
                    "body": fallback_body,
                    "warning": "Model returned unusable output; used fallback generation.",
                }
            )
        return JsonResponse({"ok": True, "title": title, "body": rewritten_body})
    except Exception as e:
        _log_exception("Failed to regenerate home update title/body.")
        fallback_title, fallback_body = _build_home_update_fallback(body)
        return JsonResponse(
            {
                "ok": True,
                "title": fallback_title,
                "body": fallback_body,
                "warning": "Model regeneration failed; used fallback generation.",
            }
        )


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
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            messages.warning(request, "That outline item no longer exists. Please pick it from the outline.")
            return HttpResponseRedirect(reverse("project-edit", kwargs={"slug": self.project.slug}))

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
            ctx["billing_enabled"] = billing_enabled()
            ctx["has_active_plan"] = user_has_active_plan(self.request.user)
            ctx["ai_billing_url"] = _get_billing_url(self.request, reason="active-plan")
            ctx["chapter_scenes"] = _get_chapter_scene_links(obj)
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

        if self.object.node_type == OutlineNode.NodeType.SCENE and action in {"structurize", "render", "reshuffle"}:
            blocked = _subscription_required_response(request)
            if blocked is not None:
                return blocked

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
                    "Keep it grounded in the provided summary, POV, location, selected location details, previous-scene continuity, and selected character details when available.",
                    "Avoid meta commentary and avoid explaining what you are doing.",
                    "",
                    "Title: " + (scene.title or ""),
                    "POV: " + (scene.pov or ""),
                    "Location: " + (scene.location or ""),
                    "Summary: " + summary,
                ]
                location_lines = _get_selected_location_context(scene.project, scene.location)
                if location_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(location_lines)
                character_lines = _get_selected_character_context(scene.project, scene.characters)
                if character_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(character_lines)
                previous_scene_lines = _get_previous_scene_context(scene)
                if previous_scene_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(previous_scene_lines)
                bible_lines = _get_story_bible_context(scene.project)
                if bible_lines:
                    prompt_lines.append("")
                    prompt_lines.extend(bible_lines)
                prompt = "\n".join(prompt_lines).strip()

                try:
                    model_name = get_user_text_model(request.user)
                    params = {"temperature": 0.7, "max_tokens": 900}
                    result = _call_tracked_llm(
                        project=scene.project,
                        action_label="Scene Draft from Scene Outline",
                        prompt=prompt,
                        model_name=model_name,
                        params=params,
                        outline_node=scene,
                    )
                    scene.structure_json = (result.text or "").strip()
                    scene.save()
                    messages.success(request, "Generated draft from scene outline.")
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

                targeted_segments = _split_targeted_segments(raw_draft)
                targeted_texts = [seg["text"] for seg in targeted_segments if seg["targeted"]]
                if targeted_texts:
                    prompt_lines = [
                        "Rewrite only the marked sections of the draft.",
                        "Rules:",
                        "- Return STRICT JSON only in the form: {\"segments\": [...]}",
                        "- Return one rewritten string for each !{...}! section, in order.",
                        "- Use the full draft as context.",
                        "- Rewrite ONLY the marked !{...}! sections; all other text is protected and will remain unchanged.",
                        "- Do not include !{...}! markers in the returned strings.",
                        "- Use prose (no bullet points, no JSON inside the strings, no markdown headings).",
                        "- Avoid meta commentary and avoid explaining what you are doing.",
                        "",
                        "Full draft with marked target sections:",
                        raw_draft,
                    ]
                    bible_lines = _get_story_bible_context(scene.project)
                    if bible_lines:
                        prompt_lines.append("")
                        prompt_lines.extend(bible_lines)
                    prompt = "\n".join(prompt_lines).strip()

                    try:
                        model_name = get_user_text_model(request.user)
                        params = {"temperature": 0.8, "max_tokens": 900}
                        result = _call_tracked_llm(
                            project=scene.project,
                            action_label="Scene Regenerate",
                            prompt=prompt,
                            model_name=model_name,
                            params=params,
                            outline_node=scene,
                        )
                        data = json.loads(_extract_json_object(result.text))
                        updated = data.get("segments")
                        if not isinstance(updated, list) or len(updated) != len(targeted_texts):
                            messages.warning(
                                request,
                                "Regenerate kept the existing draft. Try again.",
                            )
                            return HttpResponseRedirect(
                                reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                            )

                        rebuilt = []
                        highlight_ranges = []
                        cursor = 0
                        idx = 0
                        for seg in targeted_segments:
                            if not seg["targeted"]:
                                segment_text = str(seg["text"])
                                rebuilt.append(segment_text)
                                cursor += len(segment_text)
                                continue
                            original = str(seg["text"])
                            replacement = updated[idx] if idx < len(updated) else ""
                            idx += 1
                            if replacement is None:
                                final_text = original
                            else:
                                replacement_text = str(replacement).replace("!{", "").replace("}!", "")
                                final_text = replacement_text if replacement_text.strip() else original
                            rebuilt.append(final_text)
                            if final_text:
                                highlight_ranges.append((cursor, cursor + len(final_text)))
                            cursor += len(final_text)
                        scene.structure_json = "".join(rebuilt)
                        scene.save()
                        messages.success(request, "Regenerated selected draft text.")
                        redirect_url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                        if highlight_ranges:
                            encoded_ranges = ",".join(f"{start}:{end}" for start, end in highlight_ranges)
                            redirect_url = _add_query_params(redirect_url, hl=encoded_ranges)
                        return HttpResponseRedirect(redirect_url)
                    except Exception:
                        messages.warning(
                            request,
                            "Regenerate kept the existing draft. Try again.",
                        )
                else:
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
                        model_name = get_user_text_model(request.user)
                        params = {"temperature": 0.8, "max_tokens": 900}
                        result = _call_tracked_llm(
                            project=scene.project,
                            action_label="Scene Reshuffle",
                            prompt=prompt,
                            model_name=model_name,
                            params=params,
                            outline_node=scene,
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
                    cleaned = _strip_draft_markers(raw_draft)
                    scene.rendered_text = cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else "")
                    scene.save()
                    messages.success(request, "Imported draft into final text.")
                    return HttpResponseRedirect(
                        reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": scene.id})
                    )

                cleaned_draft = _strip_draft_markers(raw_draft)
                prompt = "\n".join(
                    [
                        "Rewrite the draft into polished novel prose (no bullet points, no JSON, no markdown headings).",
                        "Write in continuous prose with paragraphs; do not include section headers.",
                        "Preserve the story beats, POV, and location implied by the draft.",
                        "Avoid meta commentary and avoid explaining what you are doing.",
                        "",
                        "Draft:",
                        cleaned_draft,
                    ]
                ).strip()

                try:
                    model_name = get_user_text_model(request.user)
                    params = {
                        "temperature": 0.7,
                        "max_tokens": 1200,
                    }
                    result = _call_tracked_llm(
                        project=scene.project,
                        action_label="Scene Render Prose",
                        prompt=prompt,
                        model_name=model_name,
                        params=params,
                        outline_node=scene,
                    )
                    scene.rendered_text = (result.text or "").strip() + "\n"
                    scene.save()
                    messages.success(request, "Rendered novel prose from draft.")
                except Exception:
                    scene.rendered_text = cleaned_draft + ("\n" if not cleaned_draft.endswith("\n") else "")
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
