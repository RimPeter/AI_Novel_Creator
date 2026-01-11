from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class NovelProject(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="novel_projects",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True)

    seed_idea = models.TextField(blank=True, default="")

    genre = models.CharField(max_length=120, blank=True, default="")
    tone = models.CharField(max_length=120, blank=True, default="")
    style_notes = models.TextField(blank=True, default="")

    target_word_count = models.PositiveIntegerField(default=80000)

    def __str__(self) -> str:
        return self.title


class StoryBible(TimeStampedModel):
    """
    One per project.
    Store both human-readable markdown and structured constraints/facts.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(NovelProject, on_delete=models.CASCADE, related_name="bible")

    summary_md = models.TextField(blank=True, default="")
    constraints = models.JSONField(blank=True, default=list)
    facts = models.JSONField(blank=True, default=dict)

    def __str__(self) -> str:
        return f"StoryBible: {self.project.title}"


class Character(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NovelProject, on_delete=models.CASCADE, related_name="characters")

    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120, blank=True, default="")
    age = models.PositiveIntegerField(blank=True, null=True)
    gender = models.CharField(max_length=60, blank=True, default="")
    personality = models.TextField(blank=True, default="")
    appearance = models.TextField(blank=True, default="")
    background = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    goals = models.TextField(blank=True, default="")
    voice_notes = models.TextField(blank=True, default="")
    extra_fields = models.JSONField(blank=True, default=dict)
    portrait_data_url = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_character_project_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Location(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NovelProject, on_delete=models.CASCADE, related_name="locations")

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    objects_map = models.JSONField(blank=True, default=dict)
    image_data_url = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_location_project_name"),
        ]

    def __str__(self) -> str:
        return self.name


class OutlineNode(TimeStampedModel):
    class NodeType(models.TextChoices):
        ACT = "ACT", "Act"
        CHAPTER = "CHAPTER", "Chapter"
        SCENE = "SCENE", "Scene"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NovelProject, on_delete=models.CASCADE, related_name="outline_nodes")

    node_type = models.CharField(max_length=10, choices=NodeType.choices)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="children",
        blank=True,
        null=True,
    )

    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255, blank=True, default="")
    summary = models.TextField(blank=True, default="")

    pov = models.CharField(max_length=120, blank=True, default="")
    location = models.CharField(max_length=120, blank=True, default="")

    objectives = models.JSONField(blank=True, default=list)
    beats = models.JSONField(blank=True, default=list)
    tags = models.JSONField(blank=True, default=list)
    structure_json = models.TextField(blank=True, default="")
    rendered_text = models.TextField(blank=True, default="")
    characters = models.JSONField(blank=True, default=list)

    class Meta:
        ordering = ["project", "parent_id", "order", "created_at"]

    def clean(self):
        """
        Validate that the parent exists AND is in the same project.

        Important: do not dereference self.parent here, because if someone sets parent_id
        directly to a non-existent UUID, accessing self.parent can raise DoesNotExist.
        """
        super().clean()
        if self.parent_id:
            ok = OutlineNode.objects.filter(id=self.parent_id, project_id=self.project_id).exists()
            if not ok:
                raise ValidationError({"parent": "Parent must exist and belong to the same project."})

    def save(self, *args, **kwargs):
        """
        Avoid calling full_clean() on every save (can be expensive/surprising).
        Enforce only the critical integrity rule when parent_id is set.
        """
        if self.parent_id:
            ok = OutlineNode.objects.filter(id=self.parent_id, project_id=self.project_id).exists()
            if not ok:
                raise ValidationError("Invalid parent: must exist and belong to the same project.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_node_type_display()}: {self.title or str(self.id)[:8]}"


class ManuscriptChunk(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    outline_node = models.ForeignKey(OutlineNode, on_delete=models.CASCADE, related_name="chunks")

    version = models.PositiveIntegerField(default=1)
    text = models.TextField()
    word_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["outline_node", "-version", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["outline_node", "version"], name="uniq_chunk_outline_node_version"),
        ]

    def save(self, *args, **kwargs):
        # Keep stored word_count in sync with text.
        self.word_count = len((self.text or "").split())

        # If update_fields is used and includes text, ensure word_count is also written.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if "text" in update_fields:
                update_fields.add("word_count")
            kwargs["update_fields"] = list(update_fields)

        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.outline_node} v{self.version}"


class GenerationRun(TimeStampedModel):
    class RunType(models.TextChoices):
        BIBLE = "BIBLE", "Bible"
        OUTLINE = "OUTLINE", "Outline"
        SCENE = "SCENE", "Scene"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project = models.ForeignKey(NovelProject, on_delete=models.CASCADE, related_name="runs")
    outline_node = models.ForeignKey(
        OutlineNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )

    run_type = models.CharField(max_length=20, choices=RunType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)

    prompt = models.TextField(blank=True, default="")
    model_name = models.CharField(max_length=120, blank=True, default="")
    params = models.JSONField(blank=True, default=dict)

    output_text = models.TextField(blank=True, default="")
    usage = models.JSONField(blank=True, default=dict)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.run_type} {self.status} ({self.project.title})"
