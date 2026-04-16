from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Count, Q

from .models import Label, Track, Submission
from .forms import LabelForm, TrackForm, SubmissionForm


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def dashboard(request):
    total_labels = Label.objects.count()
    total_tracks = Track.objects.count()
    total_submissions = Submission.objects.count()
    pending = Submission.objects.filter(status=Submission.Status.PENDING).count()
    accepted = Submission.objects.filter(status=Submission.Status.ACCEPTED).count()
    rejected = Submission.objects.filter(status=Submission.Status.REJECTED).count()

    recent_submissions = Submission.objects.select_related("track", "label").order_by("-submitted_date")[:5]
    finished_tracks = Track.objects.filter(status=Track.Status.FINISHED).order_by("-updated_at")[:5]

    context = {
        "total_labels": total_labels,
        "total_tracks": total_tracks,
        "total_submissions": total_submissions,
        "pending": pending,
        "accepted": accepted,
        "rejected": rejected,
        "recent_submissions": recent_submissions,
        "finished_tracks": finished_tracks,
    }
    return render(request, "crm/dashboard.html", context)


# ---------------------------------------------------------------------------
# Label views
# ---------------------------------------------------------------------------

def label_list(request):
    query = request.GET.get("q", "")
    genre = request.GET.get("genre", "")
    labels = Label.objects.annotate(
        sub_count=Count("submissions"),
        accepted=Count("submissions", filter=Q(submissions__status=Submission.Status.ACCEPTED)),
    )
    if query:
        labels = labels.filter(Q(name__icontains=query) | Q(contact_name__icontains=query))
    if genre:
        labels = labels.filter(genre=genre)

    from .models import Genre
    context = {
        "labels": labels,
        "query": query,
        "selected_genre": genre,
        "genres": Genre.choices,
    }
    return render(request, "crm/label_list.html", context)


def label_detail(request, pk):
    label = get_object_or_404(Label, pk=pk)
    submissions = label.submissions.select_related("track").order_by("-submitted_date")
    context = {"label": label, "submissions": submissions}
    return render(request, "crm/label_detail.html", context)


def label_create(request):
    if request.method == "POST":
        form = LabelForm(request.POST)
        if form.is_valid():
            label = form.save()
            messages.success(request, f'Label "{label.name}" added successfully.')
            return redirect(label.get_absolute_url())
    else:
        form = LabelForm()
    return render(request, "crm/label_form.html", {"form": form, "title": "Add Label"})


def label_edit(request, pk):
    label = get_object_or_404(Label, pk=pk)
    if request.method == "POST":
        form = LabelForm(request.POST, instance=label)
        if form.is_valid():
            form.save()
            messages.success(request, f'Label "{label.name}" updated.')
            return redirect(label.get_absolute_url())
    else:
        form = LabelForm(instance=label)
    return render(request, "crm/label_form.html", {"form": form, "title": "Edit Label", "object": label})


def label_delete(request, pk):
    label = get_object_or_404(Label, pk=pk)
    if request.method == "POST":
        name = label.name
        label.delete()
        messages.success(request, f'Label "{name}" deleted.')
        return redirect("crm:label_list")
    return render(request, "crm/confirm_delete.html", {"object": label, "type": "Label", "cancel_url": label.get_absolute_url()})


# ---------------------------------------------------------------------------
# Track views
# ---------------------------------------------------------------------------

def track_list(request):
    query = request.GET.get("q", "")
    genre = request.GET.get("genre", "")
    status = request.GET.get("status", "")
    tracks = Track.objects.annotate(sub_count=Count("submissions"))

    if query:
        tracks = tracks.filter(Q(title__icontains=query) | Q(description__icontains=query))
    if genre:
        tracks = tracks.filter(genre=genre)
    if status:
        tracks = tracks.filter(status=status)

    from .models import Genre
    context = {
        "tracks": tracks,
        "query": query,
        "selected_genre": genre,
        "selected_status": status,
        "genres": Genre.choices,
        "statuses": Track.Status.choices,
    }
    return render(request, "crm/track_list.html", context)


def track_detail(request, pk):
    track = get_object_or_404(Track, pk=pk)
    submissions = track.submissions.select_related("label").order_by("-submitted_date")
    context = {"track": track, "submissions": submissions}
    return render(request, "crm/track_detail.html", context)


def track_create(request):
    if request.method == "POST":
        form = TrackForm(request.POST, request.FILES)
        if form.is_valid():
            track = form.save()
            messages.success(request, f'Track "{track.title}" added successfully.')
            return redirect(track.get_absolute_url())
    else:
        form = TrackForm()
    return render(request, "crm/track_form.html", {"form": form, "title": "Add Track"})


def track_edit(request, pk):
    track = get_object_or_404(Track, pk=pk)
    if request.method == "POST":
        form = TrackForm(request.POST, request.FILES, instance=track)
        if form.is_valid():
            form.save()
            messages.success(request, f'Track "{track.title}" updated.')
            return redirect(track.get_absolute_url())
    else:
        form = TrackForm(instance=track)
    return render(request, "crm/track_form.html", {"form": form, "title": "Edit Track", "object": track})


def track_delete(request, pk):
    track = get_object_or_404(Track, pk=pk)
    if request.method == "POST":
        title = track.title
        track.delete()
        messages.success(request, f'Track "{title}" deleted.')
        return redirect("crm:track_list")
    return render(request, "crm/confirm_delete.html", {"object": track, "type": "Track", "cancel_url": track.get_absolute_url()})


# ---------------------------------------------------------------------------
# Submission views
# ---------------------------------------------------------------------------

def submission_list(request):
    status = request.GET.get("status", "")
    label_id = request.GET.get("label", "")
    submissions = Submission.objects.select_related("track", "label").order_by("-submitted_date")

    if status:
        submissions = submissions.filter(status=status)
    if label_id:
        submissions = submissions.filter(label_id=label_id)

    context = {
        "submissions": submissions,
        "selected_status": status,
        "selected_label": label_id,
        "statuses": Submission.Status.choices,
        "labels": Label.objects.all(),
    }
    return render(request, "crm/submission_list.html", context)


def submission_detail(request, pk):
    submission = get_object_or_404(Submission.objects.select_related("track", "label"), pk=pk)
    return render(request, "crm/submission_detail.html", {"submission": submission})


def submission_create(request):
    track_id = request.GET.get("track")
    label_id = request.GET.get("label")
    initial = {}
    if track_id:
        initial["track"] = track_id
    if label_id:
        initial["label"] = label_id

    if request.method == "POST":
        form = SubmissionForm(request.POST)
        if form.is_valid():
            submission = form.save()
            messages.success(request, f'Submission of "{submission.track}" to {submission.label} recorded.')
            return redirect(submission.get_absolute_url())
    else:
        form = SubmissionForm(initial=initial)
    return render(request, "crm/submission_form.html", {"form": form, "title": "Log Submission"})


def submission_edit(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if request.method == "POST":
        form = SubmissionForm(request.POST, instance=submission)
        if form.is_valid():
            form.save()
            messages.success(request, "Submission updated.")
            return redirect(submission.get_absolute_url())
    else:
        form = SubmissionForm(instance=submission)
    return render(request, "crm/submission_form.html", {"form": form, "title": "Edit Submission", "object": submission})


def submission_delete(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if request.method == "POST":
        submission.delete()
        messages.success(request, "Submission deleted.")
        return redirect("crm:submission_list")
    return render(request, "crm/confirm_delete.html", {
        "object": submission,
        "type": "Submission",
        "cancel_url": submission.get_absolute_url(),
    })
