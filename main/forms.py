from django import forms
import json

from allauth.account.forms import RequestLoginCodeForm, ResetPasswordForm

from .location_hierarchy import build_location_label_map, collect_descendant_ids
from .models import BillingInvoice, Character, HomeUpdate, Location, NovelProject, OutlineNode, StoryBible
from .signals import sync_legacy_account_emails

STORY_BIBLE_PDF_MAX_BYTES = 5 * 1024 * 1024


class LegacyVerifiedResetPasswordForm(ResetPasswordForm):
    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email:
            sync_legacy_account_emails(email=email)
        return super().clean_email()


class LegacyVerifiedRequestLoginCodeForm(RequestLoginCodeForm):
    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email:
            sync_legacy_account_emails(email=email)
        return super().clean_email()


class NovelProjectForm(forms.ModelForm):
    class Meta:
        model = NovelProject
        fields = [
            "title",
            "slug",
            "seed_idea",
            "genre",
            "tone",
            "style_notes",
            "target_word_count",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. dune-clone"}),
            "seed_idea": forms.Textarea(
                attrs={
                    "class": "form-control auto-grow",
                    "rows": 6,
                    "data-autogrow": "true",
                }
            ),
            "genre": forms.TextInput(attrs={"class": "form-control"}),
            "tone": forms.TextInput(attrs={"class": "form-control"}),
            "style_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "target_word_count": forms.NumberInput(attrs={"class": "form-control", "min": 1000, "step": 500}),
        }


class StoryBibleForm(forms.ModelForm):
    constraints = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "Write constraints and guardrails in prose.",
            }
        ),
        help_text="Constraints and guardrails written in prose.",
    )
    facts = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 8,
                "placeholder": "Write canonical facts in prose.",
            }
        ),
        help_text="Canonical facts written in prose.",
    )

    class Meta:
        model = StoryBible
        fields = [
            "summary_md",
            "constraints",
            "facts",
        ]
        help_texts = {
            "summary_md": "Prose summary and reference notes for the project.",
        }
        labels = {
            "summary_md": "Summary",
        }
        widgets = {
            "summary_md": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 14,
                    "placeholder": "Write the story bible summary in prose.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            return

        constraints = self.instance.constraints
        if isinstance(constraints, list):
            self.initial["constraints"] = "\n".join(str(item) for item in constraints if item is not None).strip()
        elif isinstance(constraints, dict):
            self.initial["constraints"] = "\n".join(
                f"{key}: {value}" for key, value in constraints.items()
            ).strip()
        elif isinstance(constraints, str):
            self.initial["constraints"] = constraints

        facts = self.instance.facts
        if isinstance(facts, dict):
            self.initial["facts"] = "\n".join(f"{key}: {value}" for key, value in facts.items()).strip()
        elif isinstance(facts, list):
            self.initial["facts"] = "\n".join(str(item) for item in facts if item is not None).strip()
        elif isinstance(facts, str):
            self.initial["facts"] = facts

    def clean_constraints(self):
        value = (self.cleaned_data.get("constraints") or "").strip()
        if not value:
            return []
        return value

    def clean_facts(self):
        value = (self.cleaned_data.get("facts") or "").strip()
        if not value:
            return {}
        return value


class StoryBiblePdfUploadForm(forms.Form):
    pdf_file = forms.FileField(
        label="Upload PDF",
        help_text="Upload a PDF reference for the story bible. Maximum size: 5 MB.",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": "application/pdf,.pdf",
            }
        ),
    )

    def clean_pdf_file(self):
        uploaded = self.cleaned_data.get("pdf_file")
        if uploaded is None:
            raise forms.ValidationError("Choose a PDF file to upload.")

        name = (uploaded.name or "").strip()
        if not name.lower().endswith(".pdf"):
            raise forms.ValidationError("Upload a PDF file.")

        if uploaded.size and uploaded.size > STORY_BIBLE_PDF_MAX_BYTES:
            raise forms.ValidationError("PDF is too large. Keep uploads at 5 MB or less.")

        content_type = str(getattr(uploaded, "content_type", "") or "").strip().lower()
        if content_type and content_type not in {"application/pdf", "application/x-pdf"}:
            raise forms.ValidationError("Upload a valid PDF file.")

        return uploaded


class HomeUpdateForm(forms.ModelForm):
    class Meta:
        model = HomeUpdate
        fields = [
            "title",
            "date",
            "body",
        ]
        labels = {
            "body": "Body text",
        }
        help_texts = {
            "title": "AI can generate this from the body text, and you can edit it before posting.",
            "body": "Paste raw git or technical change notes here, then use Generate with AI.",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Short user-facing update title",
                }
            ),
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "body": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 10,
                    "placeholder": "Paste raw git text or technical notes here.",
                }
            ),
        }


class BillingInvoiceForm(forms.ModelForm):
    class Meta:
        model = BillingInvoice
        fields = [
            "invoice_number",
            "source_type",
            "status",
            "currency",
            "issue_date",
            "due_date",
            "paid_at",
            "seller_name",
            "seller_email",
            "seller_address",
            "buyer_name",
            "buyer_email",
            "buyer_address",
            "description",
            "subtotal_amount",
            "tax_amount",
            "total_amount",
            "amount_paid",
            "notes",
        ]
        widgets = {
            "invoice_number": forms.TextInput(attrs={"class": "form-control"}),
            "source_type": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.TextInput(attrs={"class": "form-control"}),
            "currency": forms.TextInput(attrs={"class": "form-control"}),
            "issue_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "paid_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "seller_name": forms.TextInput(attrs={"class": "form-control"}),
            "seller_email": forms.EmailInput(attrs={"class": "form-control"}),
            "seller_address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "buyer_name": forms.TextInput(attrs={"class": "form-control"}),
            "buyer_email": forms.EmailInput(attrs={"class": "form-control"}),
            "buyer_address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "subtotal_amount": forms.NumberInput(attrs={"class": "form-control", "step": 1}),
            "tax_amount": forms.NumberInput(attrs={"class": "form-control", "step": 1}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control", "step": 1}),
            "amount_paid": forms.NumberInput(attrs={"class": "form-control", "step": 1}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }


class OutlineChapterForm(forms.ModelForm):
    class Meta:
        model = OutlineNode
        fields = [
            "order",
            "title",
            "summary",
        ]
        widgets = {
            "order": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }


class OutlineSceneForm(forms.ModelForm):
    LOCATION_CREATE_SENTINEL = "__create__"
    structure_json = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 14,
                "placeholder": "Draft the scene in prose. For stronger generation results, make sure all relevant locations and characters have been created and added first.",
            }
        ),
        label="Draft",
        help_text="Draft prose (editable).",
    )
    characters = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "checkbox-list"}),
        label="Characters",
        help_text="Tick all characters who appear in this scene.",
    )

    class Meta:
        model = OutlineNode
        fields = [
            "order",
            "title",
            "summary",
            "pov",
            "location",
            "characters",
            "structure_json",
            "rendered_text",
        ]
        widgets = {
            "order": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "pov": forms.TextInput(attrs={"class": "form-control"}),
            "rendered_text": forms.Textarea(
                attrs={
                    "class": "form-control auto-grow",
                    "rows": 18,
                    "data-autogrow": "true",
                    "placeholder": "Refine or paste the final scene prose here once the draft is ready.",
                }
            ),
        }
        help_texts = {
            "rendered_text": "Final scene prose (editable).",
        }
        labels = {
            "summary": "Scene Outline",
            "rendered_text": "Final text",
        }

    def __init__(self, *args, project=None, prefill_location=None, **kwargs):
        super().__init__(*args, **kwargs)

        resolved_project = project or getattr(self.instance, "project", None)
        characters_field = self.fields.get("characters")
        if characters_field is not None and resolved_project:
            characters_qs = Character.objects.filter(project=resolved_project).order_by("name")
            characters_field.choices = [(str(obj.id), obj.name) for obj in characters_qs]
            selected = [str(pk) for pk in (getattr(self.instance, "characters", None) or [])]
            if selected:
                valid_ids = set(characters_qs.filter(id__in=selected).values_list("id", flat=True))
                self.initial.setdefault("characters", [str(pk) for pk in valid_ids])

        location_field = self.fields.get("location")
        if location_field is None:
            return

        location_field.widget = forms.Select(attrs={"class": "form-control"})

        existing_names = []
        if resolved_project:
            existing_names = list(
                Location.objects.filter(project=resolved_project).order_by("name").values_list("name", flat=True)
            )

        current = (getattr(self.instance, "location", "") or "").strip()

        choices = [("", "— Select —")]
        for name in existing_names:
            choices.append((name, name))
        if current and current not in existing_names:
            choices.insert(1, (current, f"{current} (current)"))
        choices.append((self.LOCATION_CREATE_SENTINEL, "Create new location..."))
        location_field.choices = choices
        location_field.widget.choices = choices

        if prefill_location:
            self.initial["location"] = prefill_location
        elif current:
            self.initial.setdefault("location", current)

    def clean_location(self):
        value = (self.cleaned_data.get("location") or "").strip()
        if value == self.LOCATION_CREATE_SENTINEL:
            raise forms.ValidationError("Create a new location first, then select it.")
        return value

    def clean_structure_json(self):
        return self.cleaned_data.get("structure_json") or ""

    def save(self, commit=True):
        instance = super().save(commit=False)
        characters = self.cleaned_data.get("characters")
        if characters is not None:
            instance.characters = [str(pk) for pk in characters]
        if commit:
            instance.save()
            self.save_m2m()
        return instance



class CharacterForm(forms.ModelForm):
    class Meta:
        model = Character
        fields = [
            "name",
            "role",
            "age",
            "gender",
            "personality",
            "appearance",
            "background",
            "description",
            "goals",
            "voice_notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "role": forms.TextInput(attrs={"class": "form-control"}),
            "age": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1}),
            "gender": forms.TextInput(attrs={"class": "form-control"}),
            "personality": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "appearance": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "background": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
            "goals": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "voice_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }
        labels = {
            "personality": "Personality",
            "appearance": "Body features / appearance",
            "background": "Backstory",
            "description": "Other notes",
        }


class NestedLocationChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, label_map=None, **kwargs):
        self.label_map = label_map or {}
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return self.label_map.get(obj.id, obj.name)


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = [
            "parent",
            "name",
            "description",
        ]
        widgets = {
            "parent": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)

        resolved_project = project or getattr(self.instance, "project", None)
        if resolved_project and not getattr(self.instance, "project_id", None):
            self.instance.project = resolved_project
        parent_field = self.fields.get("parent")
        if parent_field is None:
            return

        parent_field.label = "Parent location"

        if not resolved_project:
            parent_field.queryset = Location.objects.none()
            parent_field.required = False
            parent_field.help_text = "Save the project first so locations can be linked."
            return

        root_location = Location.get_or_create_root_for_project(resolved_project)
        project_locations = list(
            Location.objects.filter(project=resolved_project).only("id", "name", "project_id", "parent_id", "is_root")
        )
        excluded_ids = set()
        if self.instance.pk:
            excluded_ids.add(self.instance.pk)
            excluded_ids.update(collect_descendant_ids(project_locations, self.instance.pk))

        available_locations = [loc for loc in project_locations if loc.id not in excluded_ids]
        label_map = build_location_label_map(project_locations)

        self.fields["parent"] = NestedLocationChoiceField(
            queryset=Location.objects.filter(id__in=[loc.id for loc in available_locations]).order_by("name"),
            required=False,
            empty_label=None,
            label="Parent location",
            help_text=(
                "Choose the location that contains this one. If left unchanged, it will stay under the root location."
                if available_locations
                else "This project uses a single root location."
            ),
            widget=forms.Select(attrs={"class": "form-control"}),
            label_map=label_map,
        )

        if self.instance.is_root:
            self.fields["parent"].disabled = True
            self.fields["parent"].required = False
            self.fields["parent"].help_text = "This is the project's top-level root location and cannot be nested."
            self.initial["parent"] = None
            return

        if self.instance.pk and self.instance.parent_id:
            self.initial.setdefault("parent", self.instance.parent_id)
        else:
            self.initial.setdefault("parent", root_location.id)

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if getattr(self.instance, "is_root", False):
            return None

        project = getattr(self.instance, "project", None)
        if project is None:
            return parent

        return parent or Location.get_or_create_root_for_project(project)
