from django import forms


class IssueContactForm(forms.Form):
    issue_subject = forms.CharField(
        required=True,
        label="Subject",
        max_length=160,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Short summary of the problem"}),
    )
    issue_message = forms.CharField(
        required=True,
        label="Message",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "Describe the issue, what you were doing, and what happened instead.",
            }
        ),
    )

    def clean_issue_subject(self):
        return (self.cleaned_data.get("issue_subject") or "").strip()

    def clean_issue_message(self):
        return (self.cleaned_data.get("issue_message") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        issue_subject = cleaned_data.get("issue_subject") or ""
        issue_message = cleaned_data.get("issue_message") or ""

        if issue_message and not issue_subject:
            self.add_error("issue_subject", "Add a short subject for the issue.")
        if issue_subject and not issue_message:
            self.add_error("issue_message", "Describe the issue.")

        return cleaned_data


class RequestContactForm(forms.Form):
    request_want = forms.CharField(
        required=True,
        label="As a user I want",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Describe the change or fix you need"}),
    )
    request_benefit = forms.CharField(
        required=True,
        label="So I can",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Describe the outcome you need"}),
    )
    additional_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "Add extra context, steps, errors, or screenshots links if needed.",
            }
        ),
        label="Additional notes",
    )

    def clean_request_want(self):
        return (self.cleaned_data.get("request_want") or "").strip()

    def clean_request_benefit(self):
        return (self.cleaned_data.get("request_benefit") or "").strip()

    def clean_additional_notes(self):
        return (self.cleaned_data.get("additional_notes") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        request_want = cleaned_data.get("request_want") or ""
        request_benefit = cleaned_data.get("request_benefit") or ""

        if request_want and not request_benefit:
            self.add_error("request_benefit", 'Complete the "So I can" part of the request.')
        if request_benefit and not request_want:
            self.add_error("request_want", 'Complete the "As a user I want" part of the request.')

        return cleaned_data
