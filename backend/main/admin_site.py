"""Custom admin site: a real dashboard instead of Django's model index.

`admin.site` is swapped for this class in main/apps.py, so every `admin.site`
reference, decorator and template tag in the project keeps working unchanged.
Only the landing page and global context differ.
"""
from django.apps import apps
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _


class StudioAdminSite(AdminSite):
    site_header = _("REAL LIFE EXPERIENCE Studio")
    site_title = "REAL LIFE EXPERIENCE Studio"
    index_title = "Overview"
    enable_nav_sidebar = False

    def each_context(self, request):
        ctx = super().each_context(request)
        ctx["tab_counts"] = self._tab_counts(request)
        return ctx

    def _tab_counts(self, request):
        """Badge counts for the sidebar. Unavailable models degrade to 0.

        Counts are wrapped so a missing table (fresh database before
        migrations) or a restricted user cannot break every admin page.
        """
        counts = {"new_leads": 0, "open_tasks": 0, "pages": 0, "media": 0}
        if not request.user.is_authenticated or not request.user.is_active:
            return counts
        try:
            crm = apps.get_app_config("crm")
            content = apps.get_app_config("content")
            counts["new_leads"] = crm.get_model("Lead").objects.filter(
                status="new"
            ).count()
            counts["open_tasks"] = crm.get_model("Task").objects.filter(
                done=False
            ).count()
            counts["pages"] = content.get_model("Page").objects.count()
            counts["media"] = content.get_model("MediaItem").objects.count()
        except Exception:
            # A missing table or an unmigrated app must not 500 the whole admin.
            pass
        return counts

    def index(self, request, extra_context=None):
        # each_context supplies site_header/site_title/available_apps and the
        # tab_counts consumed by base_site.html. Rendering without it left the
        # brand line empty and every sidebar badge missing.
        context = {
            **self.each_context(request),
            **_self_queries(request),
            "title": self.index_title,
            "subtitle": None,
            **(extra_context or {}),
        }
        return render(request, "admin/dashboard.html", context)


def _self_queries(request):
    """Everything the dashboard renders, in one place.

    Kept in a helper rather than inline so it can be unit tested without
    standing up a request.
    """
    from crm.models import Lead, LeadStatus, Task

    now = timezone.now()
    day_ago = now - timezone.timedelta(days=1)
    week_ago = now - timezone.timedelta(days=7)

    open_leads = Lead.objects.exclude(
        status__in=[LeadStatus.WON, LeadStatus.LOST]
    )
    hot = open_leads.filter(score__gte=70)
    new_leads = open_leads.filter(status=LeadStatus.NEW)

    # Pipeline: one row per status. Bar widths are a share of the *open*
    # pipeline (everything not won or lost), so the bars add up to something a
    # reader can interpret. Normalising against the widest bucket instead --
    # which is what this did first -- made a 1/1/1/1 pipeline render four
    # identical full-width bars and convey nothing.
    counts = {
        r["status"]: r["total"]
        for r in Lead.objects.values("status").annotate(total=Count("id"))
    }
    labels = dict(LeadStatus.choices)
    open_total = sum(
        n for status, n in counts.items()
        if status not in (LeadStatus.WON, LeadStatus.LOST)
    )
    rows = []
    for status, label in LeadStatus.choices:
        total = counts.get(status, 0)
        is_open = status not in (LeadStatus.WON, LeadStatus.LOST)
        # Won and lost are terminal, so they are not part of the open funnel
        # and get no bar. Dividing a closed count by the open total would draw
        # a bar for work that is already finished.
        pct = int(round(total * 100 / open_total)) if (open_total and is_open) else 0
        rows.append({
            "status": status,
            "label": label,
            "total": total,
            "pct": pct,
            "is_open": is_open,
        })

    open_tasks = Task.objects.filter(done=False)
    overdue = open_tasks.filter(due_at__lt=now)

    # Inline SVG rather than an icon font: no extra request, and the stroke
    # inherits currentColor so one rule colours every icon.
    ICON = {
        "leads": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
            '<circle cx="9" cy="7" r="4"/>'
            '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/></svg>'
        ),
        "hot": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 2s5 5.5 5 10a5 5 0 0 1-10 0c0-1.6.6-3 1.2-4"/>'
            '<path d="M12 22a7 7 0 0 0 7-7c0-1.3-.2-2.4-.6-3.4"/>'
            '<path d="M8.5 14.5A3.5 3.5 0 0 0 12 18a3.5 3.5 0 0 0 3.5-3.5"/>'
            '</svg>'
        ),
        "today": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="3" y="4" width="18" height="18" rx="2"/>'
            '<path d="M16 2v4M8 2v4M3 10h18"/></svg>'
        ),
        "task": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M9 11l3 3L22 4"/>'
            '<path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'
            '</svg>'
        ),
        "won": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="8" r="6"/>'
            '<path d="M15.5 13.5L17 22l-5-3-5 3 1.5-8.5"/></svg>'
        ),
        "page": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
            '<path d="M14 2v6h6M9 13h6M9 17h6"/></svg>'
        ),
        "media": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="3" y="3" width="18" height="18" rx="2"/>'
            '<circle cx="8.5" cy="8.5" r="1.5"/>'
            '<path d="M21 15l-5-5L5 21"/></svg>'
        ),
        "service": (
            '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8z"/></svg>'
        ),
    }

    # The SVG literals above contain no interpolation, so marking them safe
    # cannot smuggle user data into the page. Without this Django escapes
    # them and each card renders literal "&lt;svg" text.
    ICON = {name: mark_safe(markup) for name, markup in ICON.items()}

    # Six cards: three CRM, three content. The tone class drives the gradient,
    # so colour is assigned here rather than by position in the template.
    stats = [
        {
            "label": "Open leads",
            "value": open_leads.count(),
            "hint": f"{new_leads.count()} awaiting first contact",
            "url": "admin:crm_lead_changelist",
            "query": "",
            "tone": "t-leads",
            "icon": ICON["leads"],
        },
        {
            "label": "Hot leads",
            "value": hot.count(),
            "hint": "Score 70 or above",
            "url": "admin:crm_lead_changelist",
            "query": "?score__gte=70",
            "tone": "t-hot",
            "icon": ICON["hot"],
        },
        {
            "label": "Overdue tasks",
            "value": overdue.count(),
            "hint": f"{open_tasks.count()} open in total",
            "url": "admin:crm_task_changelist",
            "query": "",
            "tone": "t-tasks",
            "icon": ICON["task"],
        },
        {
            "label": "Pages",
            "value": _content_counts()["pages"],
            "hint": f"{_content_counts()['published']} published",
            "url": "admin:content_page_changelist",
            "query": "",
            "tone": "t-pages",
            "icon": ICON["page"],
        },
        {
            "label": "Media items",
            "value": _content_counts()["media"],
            "hint": "In the library",
            "url": "admin:content_mediaitem_changelist",
            "query": "",
            "tone": "t-media",
            "icon": ICON["media"],
        },
        {
            "label": "Services",
            "value": _content_counts()["services"],
            "hint": "Across the site",
            "url": "admin:crm_service_changelist",
            "query": "",
            "tone": "t-today",
            "icon": ICON["service"],
        },
    ]

    recent_leads = Lead.objects.select_related("service", "owner")[:8]

    upcoming = open_tasks.select_related("lead", "assigned_to").order_by(
        "due_at"
    )[:8]

    # Content health: the numbers that tell an owner whether the published
    # site is complete. This is the part the CRM-only dashboard was missing.
    content = _content_counts()
    content_health = [
        {
            "label": "Pages",
            "value": str(content["pages"]),
            "note": f"{content['published']} published",
            "colour": "#2F7BB5",
        },
        {
            "label": "Sections",
            "value": str(content["sections"]),
            "note": "Across all pages",
            "colour": "#0E8F87",
        },
        {
            "label": "Services",
            "value": str(content["services"]),
            "note": "Listed on the site",
            "colour": "#A8620F",
        },
        {
            "label": "Media items",
            "value": str(content["media"]),
            "note": "In the library",
            "colour": "#7C5CBF",
        },
    ]

    # Scale against the busiest service, not the raw count, so the bars are
    # comparable to each other rather than rendering at 1% when every service
    # has one lead.
    service_rows = list(
        apps.get_app_config("crm")
        .get_model("Service")
        .objects.annotate(lead_total=Count("leads"))
        .order_by("-lead_total", "sort_order")[:6]
    )
    peak = max([s.lead_total for s in service_rows], default=0)
    for svc in service_rows:
        svc.pct = int(round(svc.lead_total * 100 / peak)) if peak else 0

    return {
        "stats": stats,
        "pipeline": rows,
        "recent_leads": recent_leads,
        "upcoming_tasks": upcoming,
        "top_services": service_rows,
        "content_health": content_health,
    }


def _content_counts():
    """Content totals for the dashboard cards and the health panel.

    Wrapped because a fresh database before migrations has no tables, and the
    dashboard must still render.
    """
    try:
        content = apps.get_app_config("content")
        crm = apps.get_app_config("crm")
        pages = content.get_model("Page").objects
        return {
            "pages": pages.count(),
            "published": pages.filter(is_published=True).count(),
            "sections": content.get_model("Section").objects.count(),
            "media": content.get_model("MediaItem").objects.count(),
            "services": crm.get_model("Service").objects.count(),
        }
    except Exception:
        return {"pages": 0, "published": 0, "sections": 0, "media": 0, "services": 0}
