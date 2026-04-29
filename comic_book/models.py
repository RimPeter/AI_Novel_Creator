from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ComicProject(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comic_projects",
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120)
    logline = models.TextField(blank=True, default="")
    genre = models.CharField(max_length=120, blank=True, default="")
    tone = models.CharField(max_length=120, blank=True, default="")
    target_audience = models.CharField(max_length=120, blank=True, default="")
    art_style_notes = models.TextField(blank=True, default="")
    format_notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["title", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "slug"], name="uniq_comic_project_owner_slug"),
        ]

    def __str__(self) -> str:
        return self.title


class ComicBible(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(ComicProject, on_delete=models.CASCADE, related_name="bible")
    premise = models.TextField(blank=True, default="")
    world_rules = models.TextField(blank=True, default="")
    visual_rules = models.TextField(blank=True, default="")
    continuity_rules = models.TextField(blank=True, default="")
    cast_notes = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"Comic bible for {self.project.title}"


class ComicCharacter(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ComicProject, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120, blank=True, default="")
    age = models.PositiveIntegerField(blank=True, null=True)
    gender = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True, default="")
    costume_notes = models.TextField(blank=True, default="")
    visual_notes = models.TextField(blank=True, default="")
    voice_notes = models.TextField(blank=True, default="")
    frontal_face_image_data_url = models.TextField(blank=True, default="")
    sideways_face_image_data_url = models.TextField(blank=True, default="")
    full_body_image_data_url = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_comic_character_project_name"),
        ]

    def __str__(self) -> str:
        return self.name


class ComicLocation(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ComicProject, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    visual_notes = models.TextField(blank=True, default="")
    continuity_notes = models.TextField(blank=True, default="")
    image_data_url = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_comic_location_project_name"),
        ]

    def __str__(self) -> str:
        return self.name


class ComicIssue(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNING = "PLANNING", "Planning"
        SCRIPTING = "SCRIPTING", "Scripting"
        REVIEW = "REVIEW", "Review"
        READY = "READY", "Ready"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ComicProject, on_delete=models.CASCADE, related_name="issues")
    number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    theme = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    planned_page_count = models.PositiveIntegerField(default=22)
    opening_hook = models.TextField(blank=True, default="")
    closing_hook = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["number", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="uniq_comic_issue_project_number"),
        ]

    def __str__(self) -> str:
        return f"Issue {self.number}: {self.title}"

    @property
    def actual_page_count(self) -> int:
        return self.pages.count()


class ComicPage(TimeStampedModel):
    class PageRole(models.TextChoices):
        OPENING = "OPENING", "Opening"
        STORY = "STORY", "Story"
        TURN = "TURN", "Turn"
        SPLASH = "SPLASH", "Splash"
        CLIMAX = "CLIMAX", "Climax"
        CLIFFHANGER = "CLIFFHANGER", "Cliffhanger"

    class LayoutType(models.TextChoices):
        STANDARD = "STANDARD", "Standard"
        GRID = "GRID", "Grid"
        SPLASH = "SPLASH", "Splash"
        WIDESCREEN = "WIDESCREEN", "Widescreen"
        DECOMPRESSED = "DECOMPRESSED", "Decompressed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(ComicIssue, on_delete=models.CASCADE, related_name="pages")
    page_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    page_role = models.CharField(max_length=20, choices=PageRole.choices, default=PageRole.STORY)
    layout_type = models.CharField(max_length=20, choices=LayoutType.choices, default=LayoutType.STANDARD)
    page_turn_hook = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    panel_layout = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["page_number", "created_at", "id"]

    def __str__(self) -> str:
        return f"Page {self.page_number}"

    @property
    def dialogue_word_count(self) -> int:
        total = 0
        for panel in self.panels.all():
            total += panel.balloon_word_count
        return total


class ComicPanelNode(TimeStampedModel):
    class NodeType(models.TextChoices):
        PANEL = "PANEL", "Panel"
        SPLIT = "SPLIT", "Split"

    class SplitDirection(models.TextChoices):
        HORIZONTAL = "horizontal", "Horizontal"
        VERTICAL = "vertical", "Vertical"

    class ImageStatus(models.TextChoices):
        IDLE = "IDLE", "Idle"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    class ShotType(models.TextChoices):
        FULL = "FULL", "Full"
        WIDE = "WIDE", "Wide"
        MEDIUM = "MEDIUM", "Medium"
        CLOSE = "CLOSE", "Close"
        EXTREME_CLOSE = "EXTREME_CLOSE", "Extreme close"
        INSERT = "INSERT", "Insert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(ComicPage, on_delete=models.CASCADE, related_name="panel_nodes")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="children",
        blank=True,
        null=True,
    )
    panel_key = models.CharField(max_length=120)
    node_type = models.CharField(max_length=10, choices=NodeType.choices, default=NodeType.PANEL)
    child_index = models.PositiveSmallIntegerField(default=0)
    split_direction = models.CharField(max_length=20, choices=SplitDirection.choices, blank=True, default="")
    split_ratio = models.DecimalField(max_digits=4, decimal_places=3, blank=True, null=True)
    focus = models.CharField(max_length=160, blank=True, default="")
    action = models.TextField(blank=True, default="")
    shot_type = models.CharField(max_length=20, choices=ShotType.choices, default=ShotType.MEDIUM)
    camera_angle = models.CharField(max_length=120, blank=True, default="")
    mood = models.CharField(max_length=120, blank=True, default="")
    lighting_notes = models.TextField(blank=True, default="")
    dialogue_space = models.CharField(max_length=120, blank=True, default="")
    must_include = models.TextField(blank=True, default="")
    must_avoid = models.TextField(blank=True, default="")
    style_override = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    location = models.ForeignKey(
        ComicLocation,
        on_delete=models.SET_NULL,
        related_name="panel_nodes",
        blank=True,
        null=True,
    )
    characters = models.ManyToManyField(ComicCharacter, related_name="panel_nodes", blank=True)
    image_prompt = models.TextField(blank=True, default="")
    image_data_url = models.TextField(blank=True, default="")
    image_status = models.CharField(max_length=12, choices=ImageStatus.choices, default=ImageStatus.IDLE)
    last_generated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["page", "panel_key"], name="uniq_comic_panel_node_page_key"),
        ]

    def __str__(self) -> str:
        return f"{self.page} / {self.panel_key}"


class ComicPanel(TimeStampedModel):
    class ShotType(models.TextChoices):
        FULL = "FULL", "Full"
        WIDE = "WIDE", "Wide"
        MEDIUM = "MEDIUM", "Medium"
        CLOSE = "CLOSE", "Close"
        EXTREME_CLOSE = "EXTREME_CLOSE", "Extreme close"
        INSERT = "INSERT", "Insert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(ComicPage, on_delete=models.CASCADE, related_name="panels")
    panel_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255, blank=True, default="")
    shot_type = models.CharField(max_length=20, choices=ShotType.choices, default=ShotType.MEDIUM)
    focus = models.CharField(max_length=160, blank=True, default="")
    action = models.TextField(blank=True, default="")
    dialogue = models.TextField(blank=True, default="")
    caption = models.TextField(blank=True, default="")
    sfx = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    location = models.ForeignKey(
        ComicLocation,
        on_delete=models.SET_NULL,
        related_name="panels",
        blank=True,
        null=True,
    )
    characters = models.ManyToManyField(ComicCharacter, related_name="panels", blank=True)

    class Meta:
        ordering = ["panel_number", "created_at", "id"]

    def __str__(self) -> str:
        return f"Panel {self.panel_number}"

    @property
    def balloon_word_count(self) -> int:
        text = " ".join(part for part in [self.dialogue, self.caption] if part)
        return len(text.split())
