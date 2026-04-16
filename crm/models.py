from django.db import models
from django.urls import reverse


class Genre(models.TextChoices):
    TECHNO = "techno", "Techno"
    HOUSE = "house", "House"
    DEEP_HOUSE = "deep_house", "Deep House"
    TECH_HOUSE = "tech_house", "Tech House"
    MINIMAL = "minimal", "Minimal"
    MELODIC_TECHNO = "melodic_techno", "Melodic Techno / House"
    DRUM_AND_BASS = "drum_and_bass", "Drum & Bass"
    AMBIENT = "ambient", "Ambient"
    TRANCE = "trance", "Trance"
    PROGRESSIVE = "progressive", "Progressive"
    ELECTRO = "electro", "Electro"
    OTHER = "other", "Other"


class Label(models.Model):
    name = models.CharField(max_length=200)
    genre = models.CharField(max_length=50, choices=Genre.choices, default=Genre.OTHER)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    instagram = models.CharField(max_length=200, blank=True)
    soundcloud = models.CharField(max_length=200, blank=True)
    demo_submission_url = models.URLField(blank=True, help_text="Demo submission portal URL if available")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("crm:label_detail", kwargs={"pk": self.pk})

    @property
    def submission_count(self):
        return self.submissions.count()

    @property
    def accepted_count(self):
        return self.submissions.filter(status=Submission.Status.ACCEPTED).count()


class Track(models.Model):
    class Status(models.TextChoices):
        DEMO = "demo", "Demo"
        IN_PROGRESS = "in_progress", "In Progress"
        FINISHED = "finished", "Finished"
        RELEASED = "released", "Released"
        SHELVED = "shelved", "Shelved"

    title = models.CharField(max_length=200)
    genre = models.CharField(max_length=50, choices=Genre.choices, default=Genre.OTHER)
    bpm = models.PositiveSmallIntegerField(null=True, blank=True)
    key = models.CharField(max_length=20, blank=True, help_text="e.g. Am, F#m, G Major")
    duration = models.CharField(max_length=10, blank=True, help_text="e.g. 6:30")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DEMO)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    audio_file = models.FileField(upload_to="tracks/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("crm:track_detail", kwargs={"pk": self.pk})

    @property
    def submission_count(self):
        return self.submissions.count()


class Submission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        NO_RESPONSE = "no_response", "No Response"

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="submissions")
    label = models.ForeignKey(Label, on_delete=models.CASCADE, related_name="submissions")
    submitted_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    response_date = models.DateField(null=True, blank=True)
    terms = models.TextField(
        blank=True,
        help_text="Deal terms if accepted (e.g. revenue split, exclusivity period, release date)",
    )
    notes = models.TextField(blank=True, help_text="Any notes about this submission or the label's response")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_date"]
        unique_together = [["track", "label"]]

    def __str__(self):
        return f"{self.track} → {self.label} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse("crm:submission_detail", kwargs={"pk": self.pk})

    @property
    def is_accepted(self):
        return self.status == self.Status.ACCEPTED
