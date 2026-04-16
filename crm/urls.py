from django.urls import path
from . import views

app_name = "crm"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Labels
    path("labels/", views.label_list, name="label_list"),
    path("labels/add/", views.label_create, name="label_create"),
    path("labels/<int:pk>/", views.label_detail, name="label_detail"),
    path("labels/<int:pk>/edit/", views.label_edit, name="label_edit"),
    path("labels/<int:pk>/delete/", views.label_delete, name="label_delete"),

    # Tracks
    path("tracks/", views.track_list, name="track_list"),
    path("tracks/add/", views.track_create, name="track_create"),
    path("tracks/<int:pk>/", views.track_detail, name="track_detail"),
    path("tracks/<int:pk>/edit/", views.track_edit, name="track_edit"),
    path("tracks/<int:pk>/delete/", views.track_delete, name="track_delete"),

    # Submissions
    path("submissions/", views.submission_list, name="submission_list"),
    path("submissions/add/", views.submission_create, name="submission_create"),
    path("submissions/<int:pk>/", views.submission_detail, name="submission_detail"),
    path("submissions/<int:pk>/edit/", views.submission_edit, name="submission_edit"),
    path("submissions/<int:pk>/delete/", views.submission_delete, name="submission_delete"),
]
