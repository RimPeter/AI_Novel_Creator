from django import forms
from .models import NovelProject


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
