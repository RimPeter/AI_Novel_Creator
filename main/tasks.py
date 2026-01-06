from __future__ import annotations

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

from .models import OutlineNode


@shared_task
def generate_bible(project_id: str, *, model_name: str = "your-model", params: dict | None = None) -> str:
    return f"TODO: generate bible for project {project_id} (model={model_name})"


@shared_task
def generate_outline(project_id: str, *, model_name: str = "your-model", params: dict | None = None) -> str:
    return f"TODO: generate outline for project {project_id} (model={model_name})"


@shared_task
def generate_scene(scene_id: str, *, model_name: str = "your-model", params: dict | None = None, target_words: int = 1200) -> str:
    return f"TODO: generate scene {scene_id} (model={model_name}, target_words={target_words})"

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
