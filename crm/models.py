"""CRM domain models.

The guiding rule for this app: a lead is never lost. `capture_lead()` in
`crm/services.py` writes the Lead row inside a transaction *before* any email
is attempted, so an SMTP outage degrades to "no notification" rather than
"inquiry gone". Everything else here exists to make that record actionable:
who owns it, what stage it is in, what happened to it, and what to do next.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


class Service(models.Model):
    """Canonical catalogue of what the business sells.

    The public forms historically posted two different shapes for this field
    (display strings on the home page, slugs on the contact page) and each
    view re-mapped them independently. This table is the single contract.
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "services"

    def __str__(self):
        return self.name


class LeadStatus(models.TextChoices):
    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    QUALIFIED = "qualified", "Qualified"
    ESTIMATE_SENT = "estimate_sent", "Estimate sent"
    WON = "won", "Won"
    LOST = "lost", "Lost"


class LeadSource(models.TextChoices):
    HOME_FORM = "home_form", "Home page form"
    CONTACT_FORM = "contact_form", "Contact page form"
    PHONE = "phone", "Phone"
    REFERRAL = "referral", "Referral"
    OTHER = "other", "Other"


class Priority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Lead(models.Model):
    """One inbound enquiry."""

    # --- who -----------------------------------------------------------
    name = models.CharField(max_length=160)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=40, blank=True)
    city_or_zip = models.CharField(
        "City / ZIP", max_length=120, blank=True,
        help_text="Used to route the lead to a service-area owner.",
    )

    # --- what they want -------------------------------------------------
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="leads",
    )
    service_raw = models.CharField(
        "Service (as submitted)", max_length=160, blank=True,
        help_text="Original form value, kept verbatim for audit even when it "
                  "does not resolve to a Service.",
    )
    message = models.TextField(blank=True)

    # --- pipeline -------------------------------------------------------
    status = models.CharField(
        max_length=20, choices=LeadStatus.choices,
        default=LeadStatus.NEW, db_index=True,
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices,
        default=Priority.NORMAL, db_index=True,
    )
    score = models.PositiveSmallIntegerField(
        default=0, db_index=True,
        help_text="Rule-based 0-100. 70+ is treated as a hot lead.",
    )
    source = models.CharField(
        max_length=20, choices=LeadSource.choices,
        default=LeadSource.OTHER, db_index=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="owned_leads",
    )

    # --- attribution ----------------------------------------------------
    referrer = models.CharField(max_length=300, blank=True)
    landing_page = models.CharField(max_length=300, blank=True)
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)

    # --- bookkeeping ----------------------------------------------------
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "lead"
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["-score"]),
        ]

    def __str__(self):
        return f"{self.name} — {self.service or self.service_raw or 'General'}"

    def get_absolute_url(self):
        return reverse("admin:crm_lead_change", args=[self.pk])

    @property
    def is_hot(self):
        return self.score >= 70

    @property
    def is_open(self):
        return self.status not in (LeadStatus.WON, LeadStatus.LOST)

    @property
    def open_tasks(self):
        return self.tasks.filter(done=False)

    def match_duplicate(self):
        """Find an existing lead from the same person.

        Email is the strong signal; phone is used only when supplied, since
        shared/family numbers legitimately generate repeat enquiries.
        """
        qs = Lead.objects.filter(Q(email__iexact=self.email))
        if self.phone:
            qs = qs | Lead.objects.filter(phone__iexact=self.phone)
        return qs.exclude(pk=self.pk).order_by("-created_at")


class LeadActivity(models.Model):
    """Append-only timeline. Never updated, never deleted."""

    class Kind(models.TextChoices):
        CREATED = "created", "Created"
        STATUS_CHANGED = "status_changed", "Status changed"
        ASSIGNED = "assigned", "Owner assigned"
        NOTED = "noted", "Note added"
        EMAILED = "emailed", "Email sent"
        TASK_DONE = "task_done", "Task completed"
        MERGED = "merged", "Merged"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="activities")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    summary = models.CharField(max_length=300)
    detail = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="crm_activities",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "lead activities"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.summary}"


class Task(models.Model):
    """A follow-up owed on a lead."""

    class Meta:
        ordering = ["done", "due_at", "pk"]
        indexes = [models.Index(fields=["done", "due_at"])]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    due_at = models.DateTimeField(null=True, blank=True)
    done = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="crm_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.lead.name})"

    @property
    def is_overdue(self):
        return bool(self.due_at and not self.done and self.due_at < timezone.now())


class Contact(models.Model):
    """A converted lead or a manually added contact.

    Created from a Lead via the admin "Convert to contact" action, which is
    also what stamps the lead as WON.
    """

    name = models.CharField(max_length=160)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=40, blank=True)
    city_or_zip = models.CharField("City / ZIP", max_length=120, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="crm_contacts",
    )
    source_lead = models.OneToOneField(
        Lead, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="converted_contact",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
