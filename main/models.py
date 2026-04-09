from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .location_hierarchy import collect_descendant_ids


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
    is_archived = models.BooleanField(default=False, db_index=True)

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


def story_bible_pdf_upload_to(instance, filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    extension = ext.lower() if ext else ".pdf"
    project_slug = getattr(instance.story_bible.project, "slug", "project")
    return f"story_bible_pdfs/{project_slug}/{uuid.uuid4().hex}{extension}"


class StoryBibleDocument(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story_bible = models.ForeignKey(StoryBible, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=story_bible_pdf_upload_to)
    original_name = models.CharField(max_length=255, blank=True, default="")
    file_size = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    extracted_text = models.TextField(blank=True, default="")
    extracted_text_chars = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        name = self.original_name or os.path.basename(getattr(self.file, "name", "") or "")
        return f"{self.story_bible.project.title}: {name or 'PDF reference'}"


class HomeUpdate(TimeStampedModel):
    date = models.DateField(default=timezone.localdate)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.date}: {self.title}"


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
    DEFAULT_ROOT_NAME = "World"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NovelProject, on_delete=models.CASCADE, related_name="locations")
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        blank=True,
        null=True,
    )
    is_root = models.BooleanField(default=False)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    objects_map = models.JSONField(blank=True, default=dict)
    image_data_url = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_location_project_name"),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(is_root=True),
                name="uniq_root_location_per_project",
            ),
        ]

    @classmethod
    def get_or_create_root_for_project(cls, project):
        root = cls.objects.filter(project=project, is_root=True).first()
        if root is not None:
            return root

        fallback = cls.objects.filter(project=project, name=cls.DEFAULT_ROOT_NAME).order_by("created_at", "id").first()
        if fallback is not None:
            if fallback.parent_id is not None or not fallback.is_root:
                fallback.parent = None
                fallback.is_root = True
                fallback.save(update_fields=["parent", "is_root", "updated_at"])
            return fallback

        return cls.objects.create(
            project=project,
            name=cls.DEFAULT_ROOT_NAME,
            parent=None,
            is_root=True,
        )

    def clean(self):
        super().clean()
        if self.is_root:
            if self.parent_id:
                raise ValidationError({"parent": "The root location cannot be nested inside another location."})
            existing_root = (
                Location.objects.filter(project_id=self.project_id, is_root=True).exclude(pk=self.pk).exists()
                if self.project_id
                else False
            )
            if existing_root:
                raise ValidationError({"is_root": "Only one root location is allowed per project."})
            return

        if not self.parent_id:
            return

        if self.parent_id == self.id:
            raise ValidationError({"parent": "A location cannot contain itself."})

        parent = Location.objects.filter(id=self.parent_id).only("id", "project_id", "is_root").first()
        if parent is None or parent.project_id != self.project_id:
            raise ValidationError({"parent": "Parent location must belong to the same project."})

        if self.pk:
            project_locations = list(
                Location.objects.filter(project_id=self.project_id).only("id", "parent_id", "name", "is_root")
            )
            if self.parent_id in collect_descendant_ids(project_locations, self.pk):
                raise ValidationError({"parent": "Choose a parent outside this location's subtree."})

    def save(self, *args, **kwargs):
        if self.is_root:
            self.parent = None
        elif self.project_id and not self.parent_id:
            root = self.get_or_create_root_for_project(self.project)
            if root.id != self.id:
                self.parent = root
        return super().save(*args, **kwargs)

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


class UserTextModelPreference(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="text_model_preference",
    )
    text_model_name = models.CharField(max_length=120, blank=True, default="")

    def __str__(self) -> str:
        return f"Text model preference for {self.user}"


class UserSubscription(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription_record",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    stripe_product_id = models.CharField(max_length=255, blank=True, default="")
    stripe_price_id = models.CharField(max_length=255, blank=True, default="")
    billing_interval = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(max_length=40, blank=True, default="")
    cancel_at_period_end = models.BooleanField(default=False)
    current_period_start = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    trial_end = models.DateTimeField(blank=True, null=True)
    last_checkout_session_id = models.CharField(max_length=255, blank=True, default="")
    raw_data = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["user_id"]

    @property
    def is_active(self) -> bool:
        if self.status not in {"active", "trialing"}:
            return False
        if self.current_period_end and self.current_period_end <= timezone.now():
            return False
        return True

    def __str__(self) -> str:
        return f"Subscription for {self.user}"


def default_invoice_seller_name() -> str:
    return str(getattr(settings, "SITE_NAME", "") or "AI Novel Creator").strip() or "AI Novel Creator"


def default_invoice_seller_email() -> str:
    default_from = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if "<" in default_from and ">" in default_from:
        start = default_from.find("<") + 1
        end = default_from.find(">", start)
        if end > start:
            return default_from[start:end].strip()
    return default_from


class BillingInvoice(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_invoices",
    )
    subscription_record = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name="invoices",
        blank=True,
        null=True,
    )
    stripe_invoice_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    invoice_number = models.CharField(max_length=120, blank=True, default="", db_index=True)
    source_type = models.CharField(max_length=40, blank=True, default="")
    status = models.CharField(max_length=40, blank=True, default="")
    currency = models.CharField(max_length=12, blank=True, default="GBP")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    seller_name = models.CharField(max_length=255, blank=True, default=default_invoice_seller_name)
    seller_email = models.CharField(max_length=255, blank=True, default=default_invoice_seller_email)
    seller_address = models.TextField(blank=True, default="")
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    buyer_email = models.CharField(max_length=255, blank=True, default="")
    buyer_address = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    subtotal_amount = models.IntegerField(default=0)
    tax_amount = models.IntegerField(default=0)
    total_amount = models.IntegerField(default=0)
    amount_paid = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    raw_data = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["-issue_date", "-created_at"]

    @property
    def public_number(self) -> str:
        return (self.invoice_number or self.stripe_invoice_id or str(self.pk)).strip()

    @property
    def amount_due(self) -> int:
        return max(int(self.total_amount or 0) - int(self.amount_paid or 0), 0)

    def __str__(self) -> str:
        return f"Invoice {self.public_number} for {self.user}"


class ProcessedStripeEvent(TimeStampedModel):
    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=120, blank=True, default="")
    payload = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type or 'stripe.event'} {self.stripe_event_id}"
