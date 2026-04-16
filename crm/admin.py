from django.contrib import admin
from .models import Label, Track, Submission


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ["name", "genre", "contact_name", "email", "submission_count", "accepted_count"]
    list_filter = ["genre"]
    search_fields = ["name", "contact_name", "email"]


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ["title", "genre", "bpm", "key", "status", "submission_count", "created_at"]
    list_filter = ["genre", "status"]
    search_fields = ["title"]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["track", "label", "submitted_date", "status", "response_date"]
    list_filter = ["status", "label"]
    search_fields = ["track__title", "label__name"]
    date_hierarchy = "submitted_date"
