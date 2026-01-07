from django import forms
import json

from .models import Character, Location, NovelProject, OutlineNode, StoryBible


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
            "seed_idea": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
            "genre": forms.TextInput(attrs={"class": "form-control"}),
            "tone": forms.TextInput(attrs={"class": "form-control"}),
            "style_notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "target_word_count": forms.NumberInput(attrs={"class": "form-control", "min": 1000, "step": 500}),
        }


class StoryBibleForm(forms.ModelForm):
    class Meta:
        model = StoryBible
        fields = [
            "summary_md",
            "constraints",
            "facts",
        ]
        help_texts = {
            "summary_md": "Markdown summary and reference notes for the project.",
            "constraints": "JSON list of constraints (advanced).",
            "facts": "JSON object of canonical facts (advanced).",
        }
        widgets = {
            "summary_md": forms.Textarea(attrs={"class": "form-control", "rows": 14}),
            "constraints": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 6,
                    "placeholder": 'Example: ["No time travel", "First-person POV"]',
                }
            ),
            "facts": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 8,
                    "placeholder": 'Example: {"protagonist": "Ava", "setting": "Orbital colony"}',
                }
            ),
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

    class Meta:
        model = OutlineNode
        fields = [
            "order",
            "title",
            "summary",
            "pov",
            "location",
            "structure_json",
            "rendered_text",
        ]
        widgets = {
            "order": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "pov": forms.TextInput(attrs={"class": "form-control"}),
            "structure_json": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 14,
                    "placeholder": '{\n  "schema_version": 1,\n  "title": "Scene 1",\n  "summary": "...",\n  "pov": "",\n  "location": "",\n  "beats": ["...", "..."]\n}',
                }
            ),
            "rendered_text": forms.Textarea(attrs={"class": "form-control", "rows": 18}),
        }
        help_texts = {
            "structure_json": "Scene structure JSON (editable).",
            "rendered_text": "Rendered scene prose (editable).",
        }

    def __init__(self, *args, project=None, prefill_location=None, **kwargs):
        super().__init__(*args, **kwargs)

        resolved_project = project or getattr(self.instance, "project", None)
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
        value = self.cleaned_data.get("structure_json") or ""
        if not value.strip():
            return ""
        try:
            json.loads(value)
        except Exception as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")
        return value


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


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = [
            "name",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
        }
