from django import forms

from .models import ComicBible, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


def _autogrow_textarea(*, rows: int):
    return forms.Textarea(attrs={"class": "form-control", "rows": rows, "data-autogrow": "true"})


class ComicProjectForm(forms.ModelForm):
    class Meta:
        model = ComicProject
        fields = [
            "title",
            "slug",
            "logline",
            "genre",
            "tone",
            "target_audience",
            "art_style_notes",
            "format_notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. neon-afterglow"}),
            "logline": _autogrow_textarea(rows=5),
            "genre": forms.TextInput(attrs={"class": "form-control"}),
            "tone": forms.TextInput(attrs={"class": "form-control"}),
            "target_audience": forms.TextInput(attrs={"class": "form-control"}),
            "art_style_notes": _autogrow_textarea(rows=5),
            "format_notes": _autogrow_textarea(rows=4),
        }


class ComicBibleForm(forms.ModelForm):
    class Meta:
        model = ComicBible
        fields = [
            "premise",
            "world_rules",
            "visual_rules",
            "continuity_rules",
            "cast_notes",
        ]
        widgets = {
            "premise": _autogrow_textarea(rows=6),
            "world_rules": _autogrow_textarea(rows=6),
            "visual_rules": _autogrow_textarea(rows=6),
            "continuity_rules": _autogrow_textarea(rows=6),
            "cast_notes": _autogrow_textarea(rows=6),
        }


class ComicCharacterForm(forms.ModelForm):
    class Meta:
        model = ComicCharacter
        fields = [
            "name",
            "role",
            "description",
            "costume_notes",
            "visual_notes",
            "voice_notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "role": forms.TextInput(attrs={"class": "form-control"}),
            "description": _autogrow_textarea(rows=5),
            "costume_notes": _autogrow_textarea(rows=4),
            "visual_notes": _autogrow_textarea(rows=4),
            "voice_notes": _autogrow_textarea(rows=4),
        }


class ComicLocationForm(forms.ModelForm):
    class Meta:
        model = ComicLocation
        fields = [
            "name",
            "description",
            "visual_notes",
            "continuity_notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": _autogrow_textarea(rows=5),
            "visual_notes": _autogrow_textarea(rows=4),
            "continuity_notes": _autogrow_textarea(rows=4),
        }


class ComicIssueForm(forms.ModelForm):
    class Meta:
        model = ComicIssue
        fields = [
            "number",
            "title",
            "summary",
            "theme",
            "status",
            "planned_page_count",
            "opening_hook",
            "closing_hook",
            "notes",
        ]
        widgets = {
            "number": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": _autogrow_textarea(rows=5),
            "theme": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "planned_page_count": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "opening_hook": _autogrow_textarea(rows=4),
            "closing_hook": _autogrow_textarea(rows=4),
            "notes": _autogrow_textarea(rows=5),
        }


class ComicPageForm(forms.ModelForm):
    class Meta:
        model = ComicPage
        fields = [
            "page_number",
            "title",
            "summary",
            "page_role",
            "layout_type",
            "page_turn_hook",
            "notes",
        ]
        widgets = {
            "page_number": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": _autogrow_textarea(rows=5),
            "page_role": forms.Select(attrs={"class": "form-control"}),
            "layout_type": forms.Select(attrs={"class": "form-control"}),
            "page_turn_hook": _autogrow_textarea(rows=4),
            "notes": _autogrow_textarea(rows=4),
        }


class ComicPanelForm(forms.ModelForm):
    class Meta:
        model = ComicPanel
        fields = [
            "panel_number",
            "title",
            "shot_type",
            "focus",
            "location",
            "characters",
            "action",
            "dialogue",
            "caption",
            "sfx",
            "notes",
        ]
        widgets = {
            "panel_number": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "shot_type": forms.Select(attrs={"class": "form-control"}),
            "focus": forms.TextInput(attrs={"class": "form-control"}),
            "location": forms.Select(attrs={"class": "form-control"}),
            "characters": forms.SelectMultiple(attrs={"class": "form-control multi-select", "size": 6}),
            "action": _autogrow_textarea(rows=5),
            "dialogue": _autogrow_textarea(rows=4),
            "caption": _autogrow_textarea(rows=4),
            "sfx": _autogrow_textarea(rows=3),
            "notes": _autogrow_textarea(rows=4),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        resolved_project = project
        if resolved_project is None and getattr(self.instance, "page_id", None):
            resolved_project = self.instance.page.issue.project

        if resolved_project is None:
            self.fields["location"].queryset = ComicLocation.objects.none()
            self.fields["characters"].queryset = ComicCharacter.objects.none()
            return

        self.fields["location"].queryset = ComicLocation.objects.filter(project=resolved_project).order_by("name")
        self.fields["characters"].queryset = ComicCharacter.objects.filter(project=resolved_project).order_by("name")
