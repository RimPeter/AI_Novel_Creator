from __future__ import annotations

import math

try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*decorator_args, **decorator_kwargs):
        def decorator(func):
            func.delay = func
            return func

        if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
            return decorator(decorator_args[0])

        return decorator

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import GenerationRun, ManuscriptChunk, NovelProject, OutlineNode, StoryBible


def _now_iso() -> str:
    return timezone.now().replace(microsecond=0).isoformat()


def _clamp_int(value: int, *, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(value)))


def _run_failed(run: GenerationRun, error: Exception) -> None:
    run.status = GenerationRun.Status.FAILED
    run.error = str(error)
    run.save(update_fields=["status", "error", "updated_at"])


@shared_task
def generate_bible(project_id: str, *, model_name: str = "local-template", params: dict | None = None) -> str:
    params = params or {}
    project = NovelProject.objects.get(id=project_id)

    run = GenerationRun.objects.create(
        project=project,
        run_type=GenerationRun.RunType.BIBLE,
        status=GenerationRun.Status.RUNNING,
        model_name=model_name,
        params=params,
        prompt="Generate story bible (local template).",
    )

    try:
        summary_md = "\n".join(
            [
                f"# Story Bible - {project.title}",
                "",
                f"_Generated: {_now_iso()}_",
                "",
                "## Logline",
                project.seed_idea.strip() or "A compelling story idea goes here.",
                "",
                "## Genre / Tone",
                f"- Genre: {project.genre or '-'}",
                f"- Tone: {project.tone or '-'}",
                "",
                "## Style Notes",
                project.style_notes.strip() or "-",
                "",
                "## Constraints",
                "- Keep internal consistency and preserve established facts.",
                "- Maintain the chosen tone and genre conventions.",
                "",
                "## Facts",
                f"- Target word count: {project.target_word_count}",
            ]
        )

        constraints = [
            f"Tone: {project.tone}" if project.tone else "Tone: (unspecified)",
            f"Genre: {project.genre}" if project.genre else "Genre: (unspecified)",
            f"Target words: {project.target_word_count}",
        ]
        facts = {
            "title": project.title,
            "slug": project.slug,
            "genre": project.genre,
            "tone": project.tone,
            "target_word_count": project.target_word_count,
        }

        with transaction.atomic():
            bible, _created = StoryBible.objects.select_for_update().get_or_create(project=project)
            bible.summary_md = summary_md
            bible.constraints = constraints
            bible.facts = facts
            bible.save()

            run.status = GenerationRun.Status.SUCCEEDED
            run.output_text = summary_md
            run.usage = {"generator": "local-template", "chars": len(summary_md)}
            run.save(update_fields=["status", "output_text", "usage", "updated_at"])

        return str(bible.id)
    except Exception as e:  # pragma: no cover
        _run_failed(run, e)
        raise


@shared_task
def generate_outline(project_id: str, *, model_name: str = "local-template", params: dict | None = None) -> str:
    params = params or {}
    project = NovelProject.objects.get(id=project_id)

    run = GenerationRun.objects.create(
        project=project,
        run_type=GenerationRun.RunType.OUTLINE,
        status=GenerationRun.Status.RUNNING,
        model_name=model_name,
        params=params,
        prompt="Generate outline (acts/chapters/scenes) (local template).",
    )

    try:
        existing = OutlineNode.objects.filter(project=project).count()
        if existing:
            run.status = GenerationRun.Status.SUCCEEDED
            run.output_text = f"Outline already exists ({existing} nodes). No changes made."
            run.usage = {"existing_nodes": existing, "generator": "local-template"}
            run.save(update_fields=["status", "output_text", "usage", "updated_at"])
            return run.output_text

        target_words = int(project.target_word_count or 80000)
        assumed_scene_words = 1200
        scenes_total = _clamp_int(round(target_words / assumed_scene_words), min_value=6, max_value=30)
        chapters_total = int(math.ceil(scenes_total / 3))

        act_specs = [
            ("Act I - Setup", "Introduce the world, protagonists, and the inciting incident."),
            ("Act II - Confrontation", "Escalate stakes; complications and reversals reshape the plan."),
            ("Act III - Resolution", "Climax, consequences, and a satisfying resolution."),
        ]

        chapter_beats = [
            "An opening that establishes the core problem.",
            "A decision that commits the protagonist to the journey.",
            "A setback that raises the stakes.",
            "A revelation that changes the plan.",
            "A major confrontation that forces growth.",
            "A low point that clarifies what matters.",
            "A final push toward the climax.",
            "A turning point that unlocks victory.",
            "A wrap-up that shows the new normal.",
        ]
        scene_beats = [
            "A concrete goal meets an unexpected obstacle.",
            "New information changes the direction of the plan.",
            "A choice has a cost, and the cost is paid.",
        ]

        chapters_base = chapters_total // 3
        chapters_extra = chapters_total % 3
        chapters_per_act = [chapters_base + (1 if i < chapters_extra else 0) for i in range(3)]

        with transaction.atomic():
            acts: list[OutlineNode] = []
            for act_order, (title, summary) in enumerate(act_specs, start=1):
                act = OutlineNode.objects.create(
                    project=project,
                    node_type=OutlineNode.NodeType.ACT,
                    parent=None,
                    order=act_order,
                    title=title,
                    summary=summary,
                )
                acts.append(act)

            chapter_index = 0
            scene_index = 0
            for act, act_chapters in zip(acts, chapters_per_act):
                for chapter_order in range(1, act_chapters + 1):
                    chapter_index += 1
                    beat = chapter_beats[min(chapter_index - 1, len(chapter_beats) - 1)]
                    chapter = OutlineNode.objects.create(
                        project=project,
                        node_type=OutlineNode.NodeType.CHAPTER,
                        parent=act,
                        order=chapter_order,
                        title=f"Chapter {chapter_index}",
                        summary=beat,
                    )

                    scenes_in_chapter = min(3, scenes_total - scene_index)
                    for s in range(scenes_in_chapter):
                        scene_index += 1
                        scene_beat = scene_beats[s % len(scene_beats)]
                        OutlineNode.objects.create(
                            project=project,
                            node_type=OutlineNode.NodeType.SCENE,
                            parent=chapter,
                            order=s + 1,
                            title=f"Scene {scene_index}",
                            summary=scene_beat,
                        )

        created_nodes = OutlineNode.objects.filter(project=project).count()
        run.status = GenerationRun.Status.SUCCEEDED
        run.output_text = f"Created outline: {created_nodes} nodes ({len(acts)} acts, {chapters_total} chapters, {scenes_total} scenes)."
        run.usage = {"created_nodes": created_nodes, "chapters": chapters_total, "scenes": scenes_total, "generator": "local-template"}
        run.save(update_fields=["status", "output_text", "usage", "updated_at"])
        return run.output_text
    except Exception as e:  # pragma: no cover
        _run_failed(run, e)
        raise


@shared_task
def generate_scene(scene_id: str, *, model_name: str = "local-template", params: dict | None = None, target_words: int = 1200) -> str:
    params = params or {}
    scene = (
        OutlineNode.objects.select_related("project", "parent", "parent__parent")
        .only("id", "project_id", "node_type", "title", "summary", "pov", "location", "parent_id")
        .get(id=scene_id)
    )
    if scene.node_type != OutlineNode.NodeType.SCENE:
        raise ValidationError("generate_scene expects an OutlineNode with node_type=SCENE.")

    project = NovelProject.objects.get(id=scene.project_id)
    run = GenerationRun.objects.create(
        project=project,
        outline_node=scene,
        run_type=GenerationRun.RunType.SCENE,
        status=GenerationRun.Status.RUNNING,
        model_name=model_name,
        params={**params, "target_words": target_words},
        prompt="Generate scene draft (local template).",
    )

    try:
        parent_chapter = getattr(scene, "parent", None)
        parent_act = getattr(parent_chapter, "parent", None) if parent_chapter else None

        header = f"{scene.title}\n"
        meta = " / ".join(
            part
            for part in [
                parent_act.title if parent_act and parent_act.title else None,
                parent_chapter.title if parent_chapter and parent_chapter.title else None,
                f"POV: {scene.pov}" if scene.pov else None,
                f"Location: {scene.location}" if scene.location else None,
            ]
            if part
        )

        body = "\n\n".join(
            [
                "This is an autogenerated draft placeholder. Replace it with your real scene text later.",
                (scene.summary or "").strip() or "Scene summary goes here.",
                "A clear objective drives the moment forward, but a complication forces a choice. The choice leaves a trace - new information, a damaged relationship, or a sharpened resolve - pushing the story into the next beat.",
            ]
        )

        text = header + (meta + "\n\n" if meta else "\n") + body + "\n"

        with transaction.atomic():
            max_version = (
                ManuscriptChunk.objects.filter(outline_node=scene).aggregate(Max("version")).get("version__max") or 0
            )
            chunk = ManuscriptChunk.objects.create(
                outline_node=scene,
                version=max_version + 1,
                text=text,
            )

            run.status = GenerationRun.Status.SUCCEEDED
            run.output_text = text
            run.usage = {"generator": "local-template", "chars": len(text), "chunk_version": chunk.version}
            run.save(update_fields=["status", "output_text", "usage", "updated_at"])

        return str(chunk.id)
    except Exception as e:  # pragma: no cover
        _run_failed(run, e)
        raise

@shared_task
def generate_all_scenes(project_id: str, *, model_name: str = "your-model", params: dict | None = None, target_words: int = 1200) -> int:
    """
    Enqueue generation for every scene in the project (in outline order).
    Returns number of scenes queued.
    """
    scenes = (
        OutlineNode.objects
        .filter(project_id=project_id, node_type=OutlineNode.NodeType.SCENE)
        .select_related("parent", "parent__parent")  # chapter + act
        .order_by("parent__parent__order", "parent__order", "order", "created_at")
    )

    count = 0
    for scene in scenes:
        generate_scene.delay(str(scene.id), model_name=model_name, params=params or {}, target_words=target_words)
        count += 1
    return count


@shared_task
def celery_ping() -> str:
    return "pong"
