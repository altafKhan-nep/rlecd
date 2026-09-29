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


class AuditLog(models.Model):
    """Who changed what, and when, across the CRM.

    `LeadActivity` is the *business* timeline of a lead -- what happened to it,
    written by the services that act on it. This table is the *accounting* of
    the CRM itself: every create, update and delete of a CRM row, with the
    actor and the request it came from. The two are deliberately separate. A
    lead's activity trail is meaningless if you cannot also answer "who moved
    this lead to Lost last Tuesday", and the answer to that question is not
    something the business timeline should be trusted to carry.

    Wired by signals rather than by hand, so a new code path cannot forget to
    log. Note the limit of that guarantee: `QuerySet.update()` and
    `bulk_create()` emit no save signals, so a bulk update is not recorded.
    See the "Known gap" note in `crm/audit.py`.

    `object_id` is a CharField rather than a FK because the row may be gone by
    the time anyone reads this, and a FK would either cascade away the evidence
    or refuse to store it.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        DELETE = "delete", "Deleted"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_entries",
    )
    action = models.CharField(max_length=10, choices=Action.choices, db_index=True)
    # "crm.lead", "crm.task", ... -- the model label, not the class, so a
    # rename does not orphan history.
    model = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=300, blank=True)
    # Field-level diff for updates: {"status": {"from": "new", "to": "won"}}.
    # Bounded to a handful of fields per model by the signal, so a large text
    # field cannot turn one edit into a megabyte of audit rows.
    changes = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log entries"
        indexes = [
            models.Index(fields=["model", "object_id", "-created_at"]),
        ]

    def __str__(self):
        actor = self.actor.get_username() if self.actor else "system"
        return f"{actor} {self.get_action_display().lower()} {self.model} #{self.object_id}"


class TeamMember(models.Model):
    """Someone who can own leads, and the work they cover.

    Round-robin assignment is by service and territory: a lead for a given
    service in a given area goes to the member covering it who has been
    assigned the fewest leads. Nobody has to watch a queue for the load to stay
    even, and a lead that arrives at 2pm still gets an owner.

    `assignment_count` is the round-robin cursor. It is incremented atomically
    when a lead is assigned, and is what makes the rotation fair without
    needing a timestamp comparison or a per-lead query.
    """

    name = models.CharField(max_length=160)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="team_membership",
        help_text="The staff account that owns the lead. A member without one "
                  "cannot be assigned leads and is skipped by round-robin.",
    )
    service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="team_members",
        help_text="Service this member covers. Blank covers every service.",
    )
    territory = models.CharField(
        max_length=120, blank=True,
        help_text="City or ZIP this member covers, matched against "
                  "Lead.city_or_zip. Blank covers everywhere.",
    )
    is_active = models.BooleanField(
        default=True, db_index=True,
        help_text="Untick to stop assigning new leads. Existing ones are kept.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    assignment_count = models.PositiveIntegerField(
        default=0,
        help_text="Running total of leads assigned. The round-robin cursor.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "team member"
        verbose_name_plural = "team members"

    def __str__(self):
        return self.name
