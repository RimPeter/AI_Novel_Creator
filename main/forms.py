from django import forms

from .models import NovelProject, StoryBible


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
