"""CRM admin.

The goal is that a salesperson works the pipeline from this list view and
never needs the raw database. Three things make that possible: the changelist
answers "what needs me today", every mutation is logged, and the common bulk
moves are one click.
"""
from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from . import services
from .models import (
    Contact,
    Lead,
    LeadActivity,
    LeadStatus,
    Service,
    Task,
    TeamMember,
)
from main.listview import StudioListMixin


@admin.register(Service)
class ServiceAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "is_published", "show_in_navigation",
                    "show_on_homepage", "is_featured", "sort_order", "lead_count")
    list_editable = ("is_active", "is_published", "show_in_navigation",
                     "show_on_homepage", "is_featured", "sort_order")
    search_fields = ("name", "slug", "short_description", "seo_title")
    list_filter = ("is_active", "is_published", "show_in_navigation",
                   "show_on_homepage", "is_featured")

    @admin.display(description="Leads")
    def lead_count(self, obj):
        return obj.leads.count()


class LeadActivityInline(admin.TabularInline):
    model = LeadActivity
    extra = 0
    can_delete = False
    readonly_fields = ("kind", "summary", "detail", "actor", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


class TaskInline(admin.TabularInline):
    model = Task
    extra = 1
    fields = ("title", "due_at", "done", "assigned_to")


@admin.register(Lead)
class LeadAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = (
        "name", "service_label", "status_badge", "priority_badge", "score_chip",
        "owner", "source", "created_at",
    )
    # The score total is the one number worth seeing across a filtered
    # pipeline: it is the only numeric column here, and it is what the
    # dashboard's "hot leads" card is a slice of.
    studio_totals = {"Score": "score"}
    list_filter = ("status", "priority", "source", "service", "owner", "created_at")
    search_fields = ("name", "email", "phone", "message", "service_raw", "city_or_zip")
    date_hierarchy = "created_at"
    list_select_related = ("service", "owner")
    list_per_page = 50
    inlines = [TaskInline, LeadActivityInline]
    readonly_fields = (
        "created_at", "updated_at", "contacted_at",
        "score", "service_raw", "utm_source", "utm_medium",
        "utm_campaign", "referrer", "landing_page",
    )
    actions = (
        "action_mark_contacted", "action_mark_qualified", "action_mark_won",
        "action_mark_lost", "action_assign_to_me", "action_create_followup",
        "action_convert_to_contact", "action_merge_leads",
        "action_assign_round_robin",
    )
    fieldsets = (
        (None, {"fields": ("name", "email", "phone", "city_or_zip")}),
        ("Enquiry", {"fields": ("service", "message")}),
        ("Pipeline", {"fields": ("status", "priority", "score", "owner", "source", "notes")}),
        ("System-calculated (read only)", {
            "classes": ("collapse",),
            "fields": (
                "service_raw", "utm_source", "utm_medium", "utm_campaign",
                "referrer", "landing_page", "created_at", "updated_at",
                "contacted_at",
            ),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("service", "owner")

    # --- dashboard context shown above the changelist --------------------
    def changelist_view(self, request, extra_context=None):
        leads = Lead.objects.all()
        by_status = {
            row["status"]: row["n"]
            for row in leads.values("status").annotate(n=Count("id"))
        }
        month_start = timezone.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        extra_context = dict(extra_context or {})
        extra_context.update({
            "kpi_open": leads.filter(
                status__in=[LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.QUALIFIED]
            ).count(),
            "kpi_hot": leads.filter(
                score__gte=services.HOT_THRESHOLD, status=LeadStatus.NEW
            ).count(),
            "kpi_unassigned": leads.filter(
                owner__isnull=True, status=LeadStatus.NEW
            ).count(),
            "kpi_month": leads.filter(created_at__gte=month_start).count(),
            "kpi_by_status": [
                (dict(LeadStatus.choices)[key], by_status.get(key, 0))
                for key, _ in LeadStatus.choices
            ],
            "kpi_top_services": [
                (row["service__name"], row["n"])
                for row in leads.exclude(service=None)
                .values("service__name").annotate(n=Count("id")).order_by("-n")[:8]
            ],
        })
        return super().changelist_view(request, extra_context)

    # --- list helpers ----------------------------------------------------
    @admin.display(description="Service", ordering="service__name")
    def service_label(self, obj):
        return obj.service.name if obj.service else (obj.service_raw or "—")

    @admin.display(description="Stage", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span class="pill pill-{}">{}</span>', obj.status,
            obj.get_status_display())

    @admin.display(description="Priority", ordering="priority")
    def priority_badge(self, obj):
        return format_html(
            '<span class="pill pill-{}">{}</span>', obj.priority,
            obj.get_priority_display())

    @admin.display(description="Score", ordering="score")
    def score_chip(self, obj):
        if obj.is_hot:
            tone = "s-hi"
        elif obj.score >= 40:
            tone = "s-mid"
        else:
            tone = "s-lo"
        return format_html('<span class="score-chip {}">{}</span>', tone, obj.score)

    # --- bulk actions ----------------------------------------------------
    @admin.action(description="Mark selected as Contacted")
    def action_mark_contacted(self, request, queryset):
        self._bulk_status(request, queryset, LeadStatus.CONTACTED)

    @admin.action(description="Mark selected as Qualified")
    def action_mark_qualified(self, request, queryset):
        self._bulk_status(request, queryset, LeadStatus.QUALIFIED)

    @admin.action(description="Mark selected as Won")
    def action_mark_won(self, request, queryset):
        self._bulk_status(request, queryset, LeadStatus.WON)

    @admin.action(description="Mark selected as Lost")
    def action_mark_lost(self, request, queryset):
        self._bulk_status(request, queryset, LeadStatus.LOST)

    def _bulk_status(self, request, queryset, status):
        changed = 0
        for lead in queryset:
            if lead.status != status:
                services.set_status(lead, status, actor=request.user)
                changed += 1
        label = dict(LeadStatus.choices)[status]
        self.message_user(
            request, f"{changed} lead(s) moved to {label}.", messages.SUCCESS,
        )

    @admin.action(description="Assign selected leads to me")
    def action_assign_to_me(self, request, queryset):
        for lead in queryset:
            services.assign(lead, request.user, actor=request.user)
        self.message_user(
            request, f"{queryset.count()} lead(s) assigned to you.", messages.SUCCESS,
        )

    @admin.action(description="Create a follow-up task (due tomorrow)")
    def action_create_followup(self, request, queryset):
        due = timezone.now() + timedelta(days=1)
        for lead in queryset:
            Task.objects.create(
                lead=lead,
                title=f"Call {lead.name} back",
                due_at=due,
                assigned_to=lead.owner or request.user,
            )
        self.message_user(
            request, f"{queryset.count()} follow-up task(s) created.",
            messages.SUCCESS,
        )

    @admin.action(description="Convert selected leads to contacts")
    def action_convert_to_contact(self, request, queryset):
        for lead in queryset:
            services.convert_to_contact(lead, actor=request.user)
            services.set_status(lead, LeadStatus.WON, actor=request.user)
        self.message_user(
            request, f"{queryset.count()} lead(s) converted.", messages.SUCCESS,
        )

    @admin.action(description="Merge selected leads into the oldest one")
    def action_merge_leads(self, request, queryset):
        """Fold repeats into one lead.

        A repeat enquiry is the likeliest way this business loses someone: two
        rows for the same job, and whichever is not followed up is a lead that
        never got a call. `match_duplicate` finds the earlier row; this acts on
        it.

        The oldest selected row wins, so a merge always converges on one lead
        without the operator having to decide which to keep. Nothing is
        deleted -- see services.merge_leads.
        """
        leads = list(queryset.order_by("created_at", "pk"))
        if len(leads) < 2:
            self.message_user(
                request,
                "Select at least two leads to merge. One lead cannot be "
                "merged into itself.",
                messages.WARNING,
            )
            return
        primary, duplicates = leads[0], leads[1:]
        try:
            services.merge_leads(primary, duplicates, actor=request.user)
        except services.MergeError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        self.message_user(
            request,
            f"Merged {len(duplicates)} lead(s) into #{primary.pk} "
            f"{primary.name}. The merged rows are kept and marked Lost.",
            messages.SUCCESS,
        )

    @admin.action(description="Re-assign selected by round-robin")
    def action_assign_round_robin(self, request, queryset):
        """Re-run assignment on selected leads, ignoring current owners."""
        assigned = 0
        for lead in queryset:
            member = services.assign_round_robin(
                service=lead.service, territory=lead.city_or_zip,
                actor=request.user)
            if member is None:
                continue
            services.assign(lead, member.user, actor=request.user)
            assigned += 1
        skipped = queryset.count() - assigned
        note = f" {skipped} had no eligible team member." if skipped else ""
        self.message_user(
            request, f"{assigned} lead(s) assigned by round-robin.{note}",
            messages.SUCCESS if assigned else messages.WARNING,
        )

    def save_model(self, request, obj, form, change):
        """Keep the activity trail honest when status is changed on the form."""
        previous = Lead.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        if previous and previous.status != obj.status:
            old_label = dict(LeadStatus.choices)[previous.status]
            LeadActivity.objects.create(
                lead=obj, kind=LeadActivity.Kind.STATUS_CHANGED,
                summary=f"{obj.get_status_display()} (was {old_label})",
                actor=request.user,
            )


@admin.register(TeamMember)
class TeamMemberAdmin(StudioListMixin, admin.ModelAdmin):
    """Who can own a lead, and what they cover.

    Assignment is round-robin by service and territory, so the only thing that
    has to be right here is that a member exists, is active, and has a user
    account to own the lead with.
    """

    list_display = ("name", "user", "service", "territory", "is_active",
                    "assignment_count", "open_leads")
    list_editable = ("is_active",)
    list_filter = ("is_active", "service")
    search_fields = ("name", "territory", "user__username")
    ordering = ("sort_order", "name")
    fieldsets = (
        (None, {"fields": ("name", "user", "is_active", "sort_order")}),
        ("Coverage", {
            "description": (
                "Leave the service blank to cover every service, and the "
                "territory blank to cover everywhere. Round-robin prefers the "
                "specialist for the lead's service, then the fewest "
                "assignments."
            ),
            "fields": ("service", "territory", "assignment_count"),
        }),
    )

    @admin.display(description="Open leads")
    def open_leads(self, obj):
        if not obj or not obj.user_id:
            return "—"
        return obj.user.owned_leads.filter(
            status__in=[LeadStatus.NEW, LeadStatus.CONTACTED,
                        LeadStatus.QUALIFIED]).count()


@admin.register(LeadActivity)
class LeadActivityAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("created_at", "lead", "kind", "summary", "actor")
    list_filter = ("kind", "created_at")
    search_fields = ("summary", "detail", "lead__name", "lead__email")
    list_select_related = ("lead", "actor")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Task)
class TaskAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("title", "lead", "due_label", "state", "assigned_to")
    list_filter = ("done", "due_at", "assigned_to")
    search_fields = ("title", "lead__name", "lead__email")
    list_select_related = ("lead", "assigned_to")
    actions = ("action_mark_done",)

    @admin.display(description="Due", ordering="due_at")
    def due_label(self, obj):
        if not obj.due_at:
            return format_html('<span class="muted">—</span>')
        text = timezone.localtime(obj.due_at).strftime("%b %-d, %H:%M")
        if obj.done:
            return format_html('<span class="muted">{}</span>', text)
        if obj.is_overdue:
            return format_html('<span class="pill pill-urgent">{}</span>', text)
        return text

    @admin.display(description="State", ordering="done")
    def state(self, obj):
        if obj.done:
            return format_html('<span class="pill pill-won">Done</span>')
        if obj.is_overdue:
            return format_html('<span class="pill pill-urgent">Overdue</span>')
        return format_html('<span class="pill pill-new">Open</span>')

    @admin.action(description="Mark selected tasks done")
    def action_mark_done(self, request, queryset):
        now = timezone.now()
        for task in queryset.filter(done=False):
            task.done = True
            task.completed_at = now
            task.save(update_fields=["done", "completed_at"])
            LeadActivity.objects.create(
                lead=task.lead, kind=LeadActivity.Kind.TASK_DONE,
                summary=f"Task completed: {task.title}", actor=request.user,
            )
        self.message_user(request, "Tasks marked done.", messages.SUCCESS)


@admin.register(Contact)
class ContactAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("name", "email", "phone", "city_or_zip", "owner", "created_at")
    list_filter = ("owner", "created_at")
    search_fields = ("name", "email", "phone", "city_or_zip")
    list_select_related = ("owner",)
    raw_id_fields = ("owner", "source_lead")


# Branding lives on StudioAdminSite (main/admin_site.py) rather than being
# assigned onto admin.site here. Module-level assignment ran after the custom
# site was installed and silently overwrote its site_header/site_title.
admin.site.index_title = "Pipeline"
