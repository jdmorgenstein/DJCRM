from django import forms
from .models import Label, Track, Submission


class LabelForm(forms.ModelForm):
    class Meta:
        model = Label
        fields = [
            "name", "genre", "website", "email", "contact_name",
            "instagram", "soundcloud", "demo_submission_url", "notes",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Label name"}),
            "genre": forms.Select(attrs={"class": "form-select"}),
            "website": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "demos@label.com"}),
            "contact_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "A&R contact name"}),
            "instagram": forms.TextInput(attrs={"class": "form-control", "placeholder": "@labelname"}),
            "soundcloud": forms.TextInput(attrs={"class": "form-control", "placeholder": "soundcloud.com/label"}),
            "demo_submission_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Any notes about this label..."}),
        }


class TrackForm(forms.ModelForm):
    class Meta:
        model = Track
        fields = [
            "title", "genre", "bpm", "key", "duration",
            "status", "description", "notes", "audio_file",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Track title"}),
            "genre": forms.Select(attrs={"class": "form-select"}),
            "bpm": forms.NumberInput(attrs={"class": "form-control", "placeholder": "128", "min": 60, "max": 300}),
            "key": forms.TextInput(attrs={"class": "form-control", "placeholder": "Am"}),
            "duration": forms.TextInput(attrs={"class": "form-control", "placeholder": "6:30"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Describe the track vibe, influences..."}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Production notes, mix versions..."}),
            "audio_file": forms.FileInput(attrs={"class": "form-control"}),
        }


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = [
            "track", "label", "submitted_date", "status",
            "response_date", "terms", "notes",
        ]
        widgets = {
            "track": forms.Select(attrs={"class": "form-select"}),
            "label": forms.Select(attrs={"class": "form-select"}),
            "submitted_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "response_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "terms": forms.Textarea(attrs={
                "class": "form-control", "rows": 4,
                "placeholder": "e.g. 70/30 revenue split, 2-year exclusive, digital only...",
            }),
            "notes": forms.Textarea(attrs={
                "class": "form-control", "rows": 3,
                "placeholder": "Notes about the submission or response...",
            }),
        }

    def __init__(self, *args, track=None, label=None, **kwargs):
        super().__init__(*args, **kwargs)
        if track:
            self.fields["track"].initial = track
            self.fields["track"].widget.attrs["readonly"] = True
        if label:
            self.fields["label"].initial = label
            self.fields["label"].widget.attrs["readonly"] = True
