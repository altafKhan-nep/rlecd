"""Custom admin site: a real dashboard instead of Django's model index.

`admin.site` is swapped for this class in main/apps.py, so every `admin.site`
reference, decorator and template tag in the project keeps working unchanged.
Only the landing page and global context differ.
"""
from urllib.parse import quote

from django.apps import apps
from django.contrib.admin import AdminSite
from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

MONTH_LABELS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# How many calendar months the lead chart plots, current month included.
TREND_MONTHS = 6

# The sidebar, as data: (group label or None for the top group,
# [(item label, icon name, admin url name, tab_counts key or None), ...]).
#
# Declared once at module level because two places need it. `_nav` renders it,
# and `_nav_labels` reuses the same labels as the human-readable model names in
# search results -- so the sidebar says "FAQs" and the search result says "FAQs"
# rather than Django's raw verbose_name_plural of "faqs".
#
# A `None` count key means the row shows no badge.
NAV_GROUPS = (
    (None, (
        ("Overview", "grid", "admin:index", None),
    )),
    ("Content", (
        ("Pages", "store", "admin:content_page_changelist", "pages"),
        ("Sections", "layers", "admin:content_section_changelist", None),
        # Sits next to Sections rather than under Media library: these rows are
        # the site's 93 page images, and an editor looking for "the bathroom
        # photo" starts from the section it sits in.
        ("Page images", "grid", "admin:content_sectionimage_changelist", None),
        ("Services", "tag", "admin:crm_service_changelist", None),
        ("Outbox", "mail", "admin:crm_emailoutbox_changelist", "outbox"),
        ("Media library", "image", "admin:content_mediaitem_changelist", "media"),
        ("Projects", "briefcase", "admin:content_project_changelist", None),
        ("Service areas", "map-pin", "admin:content_servicearea_changelist", None),
        ("Testimonials", "quote", "admin:content_testimonial_changelist", None),
        ("FAQs", "help-circle", "admin:content_faq_changelist", None),
        ("Trust badges", "shield", "admin:content_trustbadge_changelist", None),
        ("Navigation", "menu", "admin:content_navigation_changelist", None),
        ("Menu items", "list", "admin:content_menuitem_changelist", None),
        ("Revisions", "history", "admin:content_contentrevision_changelist", None),
        ("Site settings", "settings", "admin:content_sitesetting_changelist", None),
    )),
    ("CRM", (
        ("Leads", "users", "admin:crm_lead_changelist", "new_leads"),
        ("Pipeline board", "columns", "admin:pipeline_board", None),
        ("Tasks", "check-square", "admin:crm_task_changelist", "open_tasks"),
        ("Contacts", "user", "admin:crm_contact_changelist", None),
        ("Activity log", "bar-chart", "admin:crm_leadactivity_changelist", None),
        # Round-robin has to be maintainable from the sidebar, not from a
        # guessed URL. A member that goes inactive silently stops receiving
        # leads otherwise.
        ("Team", "users-round", "admin:crm_teammember_changelist", None),
    )),
    # auth.User and auth.Group are auto-registered by django.contrib.auth.
    # Without these entries the only route to them is a hand-typed URL.
    ("Administration", (
        ("Users", "user-cog", "admin:auth_user_changelist", None),
        ("Groups", "users-round", "admin:auth_group_changelist", None),
    )),
)


def _nav_labels():
    """Map admin url name -> human label, e.g. 'crm_lead_changelist' -> 'Leads'."""
    return {
        url_name.split(":")[-1]: label
        for _group, rows in NAV_GROUPS
        for label, _icon, url_name, _count in rows
    }


def _ago(moment, *, now=None):
    """Human age of a timestamp: "3h", "2d", "5w".

    Compact on purpose -- a card has room for one short word, and "23 hours"
    would push the owner's name off the card.
    """
    now = now or timezone.now()
    seconds = (now - moment).total_seconds()
    if seconds < 0:
        return "now"
    for limit, divisor, unit in ((60, 1, "m"), (3600, 60, "h"),
                                (86400, 3600, "d"), (604800, 86400, "w")):
        if seconds < limit:
            return f"{int(seconds // divisor)}{unit}"
    return f"{int(seconds // 604800)}w"


def _shift_month(year, month, delta):
    """Return (year, month) moved by `delta` months, carrying into the year."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _month_floor(moment, months_back=0):
    """Midnight on the first day of the month `months_back` months before `moment`.

    Takes a count backwards rather than a signed delta: the only caller wants
    the start of a window that ends at `moment`, and a signed parameter made it
    a one-character mistake to reach forward instead and silently match nothing.
    """
    year, month = _shift_month(moment.year, moment.month, -months_back)
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0,
                          second=0, microsecond=0)


def _trend(current, previous):
    """Direction and percentage change of `current` against `previous`.

    A previous period of zero has no meaningful percentage, so a rise from
    nothing is reported as a flat 100% rather than an invented figure.
    """
    if previous == 0:
        return ("up", 100) if current > 0 else ("flat", 0)
    pct = int(round((current - previous) * 100 / previous))
    if pct > 0:
        return ("up", pct)
    if pct < 0:
        return ("down", abs(pct))
    return ("flat", 0)


def _volume_trend(queryset, now, days=30):
    """Period-over-period change for a queryset that has a `created_at`.

    Both sides are scoped to the same width of window so the comparison is
    like with like. Only meaningful for flows; a backlog count such as
    "overdue tasks" has no flow to measure and must pass no trend at all.

    Returns None when the query fails, matching _content_counts: a fresh
    database with no tables should cost the dashboard a trend, not the page.
    """
    current_start = now - timezone.timedelta(days=days)
    previous_start = now - timezone.timedelta(days=days * 2)
    try:
        current = queryset.filter(created_at__gte=current_start).count()
        previous = queryset.filter(
            created_at__gte=previous_start,
            created_at__lt=current_start,
        ).count()
    except Exception:
        return None
    return _trend(current, previous)


class StudioAdminSite(AdminSite):
    site_header = _("REAL LIFE EXPERIENCE Studio")
    site_title = "REAL LIFE EXPERIENCE Studio"
    index_title = "Overview"
    enable_nav_sidebar = False

    def each_context(self, request):
        ctx = super().each_context(request)
        counts = self._tab_counts(request)
        ctx["tab_counts"] = counts
        ctx["nav_sections"] = self._nav(counts)
        # The bell used to render a hard-coded "3". A badge that is always the
        # same number teaches the eye to ignore it, which is the opposite of
        # what a notification count is for.
        ctx["attention_count"] = counts["overdue_tasks"] + counts["new_leads"]
        ctx["attention_url"] = self._attention_url(counts)
        ctx["add_links"] = self._add_links(request)
        return ctx

    def _attention_url(self, counts):
        """Deep link the bell badge into the queue it is counting.

        The badge adds new leads and overdue tasks together, but they are not
        equally urgent, so it opens whichever is the more pressing: an overdue
        task is already past due, whereas a new lead is merely unhandled.
        """
        if counts["overdue_tasks"]:
            return f"{reverse('admin:crm_task_changelist')}?done__exact=0"
        return f"{reverse('admin:crm_lead_changelist')}?status__exact=new"

    def _nav(self, counts):
        """Sidebar structure, derived from the NAV_GROUPS declaration.

        The template used to hard-code seven links, each repeating an active
        check. That made a new model mean editing markup, and a renamed URL
        silently dropped its highlight. The count key is looked up from the
        counts dict; anything not named there shows no badge.
        """
        sections = []
        for label, rows in NAV_GROUPS:
            items = [
                {
                    "label": name,
                    "icon": ico,
                    "url": reverse(url_name),
                    # Bare name, because resolver_match.url_name carries no
                    # namespace prefix. Comparing it against "admin:content_..."
                    # would never match and no item would ever look active.
                    "match_name": url_name.split(":")[-1],
                    "count": counts.get(key) if key else None,
                }
                for name, ico, url_name, key in rows
            ]
            sections.append({"label": label, "items": items})
        return sections

    def _current_model(self, request):
        """Model label for the screen being viewed, e.g. 'content_page'.

        Derived from the resolver's url_name. `match.app_name` is "admin" for
        every admin URL, so it cannot be used to tell models apart.
        """
        match = getattr(request, "resolver_match", None)
        name = getattr(match, "url_name", None) or ""
        for suffix in ("_changelist", "_add", "_change", "_delete", "_history"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return None

    def _add_links(self, request):
        """Primary create buttons for the topbar, scoped to the current section.

        The reference shows two context-sensitive "Add" buttons per screen. The
        pair is picked from the model the admin is actually looking at, so the
        topbar never offers to create something the screen has no context for.
        Each candidate is permission-checked, so a user who cannot add a model
        simply sees one button instead of a link that 403s.

        With no model in view there is nothing to scope to, so the bar stays
        empty. That is the index case, and the dashboard supplies its own two
        action areas -- the header buttons and Quick actions -- so a fallback
        pair here would only print the same links twice.
        """
        current = self._current_model(request)
        if current is None:
            return []
        pairs = {
            "content_page": (("content_page", "New page"),
                             ("content_section", "New section")),
            "content_section": (("content_section", "New section"),
                                ("content_page", "New page")),
            "content_mediaitem": (("content_mediaitem", "Upload media"),
                                  ("content_page", "New page")),
            "content_project": (("content_project", "New project"),
                                ("content_mediaitem", "Upload media")),
            "crm_service": (("crm_service", "New service"),
                            ("content_page", "New page")),
            "crm_lead": (("crm_lead", "New lead"),
                         ("crm_task", "New task")),
            "crm_task": (("crm_task", "New task"),
                         ("crm_lead", "New lead")),
            "crm_contact": (("crm_contact", "New contact"),
                            ("crm_lead", "New lead")),
        }
        chosen = pairs.get(current, ())

        links = []
        for label, text in chosen:
            try:
                model = apps.get_model(*label.split("_", 1))
            except LookupError:
                continue
            model_admin = self._registry.get(model)
            if model_admin is None or not model_admin.has_add_permission(request):
                continue
            # Admin URL names are <app_label>_<model_name>_<action>, so the app
            # label is required here; model_name alone reverses to nothing.
            url_name = (f"admin:{model._meta.app_label}_"
                        f"{model._meta.model_name}_add")
            links.append({"url": reverse(url_name), "label": text})
        return links

    def _tab_counts(self, request):
        """Badge counts for the sidebar. Unavailable models degrade to 0.

        Counts are wrapped so a missing table (fresh database before
        migrations) or a restricted user cannot break every admin page.
        """
        counts = {
            "new_leads": 0, "open_tasks": 0, "overdue_tasks": 0,
            "pages": 0, "media": 0, "outbox": 0,
        }
        if not request.user.is_authenticated or not request.user.is_active:
            return counts
        try:
            crm = apps.get_app_config("crm")
            content = apps.get_app_config("content")
            now = timezone.now()
            leads = crm.get_model("Lead").objects
            tasks = crm.get_model("Task").objects
            counts["new_leads"] = leads.filter(status="new").count()
            counts["open_tasks"] = tasks.filter(done=False).count()
            counts["overdue_tasks"] = tasks.filter(
                done=False, due_at__lt=now
            ).count()
            counts["pages"] = content.get_model("Page").objects.count()
            counts["media"] = content.get_model("MediaItem").objects.count()
            counts["outbox"] = crm.get_model("EmailOutbox").objects.filter(
                status="queued").count()
        except Exception:
            # A missing table or an unmigrated app must not 500 the whole admin.
            pass
        return counts

    def get_urls(self):
        """Add the admin's own utility views to its URL namespace.

        Django's admin has no global search endpoint (a changelist's `?q=` is
        per-model), so the topbar's field needs a real target. Subclassing
        get_urls puts it behind admin: where `admin_view` already applies the
        login check, instead of a bare project URL that would sit outside the
        admin's own security. The media picker's data source is added for the
        same reason.
        """
        extra = [
            path(
                "studio-search/",
                self.admin_view(self.studio_search),
                name="studio_search",
            ),
            path(
                "media-picker/",
                self.admin_view(self.media_picker),
                name="media_picker",
            ),
            path(
                "pipeline/",
                self.admin_view(self.pipeline_board),
                name="pipeline_board",
            ),
        ]
        return extra + super().get_urls()

    #: Cards per column. A board that renders four hundred rows is a list
    #: with extra steps, and the point of a board is the shape of the pipeline
    #: rather than every lead in it.
    BOARD_LIMIT = 40

    def pipeline_board(self, request, extra_context=None):
        """The lead pipeline as one column per stage.

        Read-only. A changelist answers "show me these rows"; this answers
        "what is stuck", which is the question a salesperson actually has and
        which a sorted table makes them answer by eye. Moving a lead stays a
        deliberate, confirmed action on the changelist -- a board that silently
        moved a card on drag would be a worse thing to hand someone than a
        list they have to read.
        """
        from crm import services
        # LeadStatus is a TextChoices in crm.models, not an attribute of the
        # Lead class, so it is imported rather than read off the model.
        from crm.models import LeadStatus as statuses

        lead_model = apps.get_model("crm", "Lead")

        owner_name = (
            lambda user: user.get_username() if user else ""
        )

        columns = []
        total = unassigned = hot = 0
        for key, label in statuses.choices:
            rows = (
                lead_model.objects.filter(status=key)
                .select_related("owner", "service")
                .order_by("-score", "-created_at")[:self.BOARD_LIMIT]
            )
            cards = []
            for lead in rows:
                total += 1
                if not lead.owner_id:
                    unassigned += 1
                is_hot = lead.is_hot
                if is_hot:
                    hot += 1
                cards.append({
                    "url": reverse("admin:crm_lead_change", args=[lead.pk]),
                    "name": lead.name,
                    "service_label": (
                        lead.service.name if lead.service
                        else (lead.service_raw or "—")
                    ),
                    "score": lead.score,
                    "score_band": (
                        "hot" if lead.score >= services.HOT_THRESHOLD
                        else "warm" if lead.score >= 40 else "cold"
                    ),
                    "is_hot": is_hot,
                    "owner": owner_name(lead.owner),
                    "age": _ago(lead.created_at),
                    "created_at_iso": lead.created_at.isoformat(),
                })
            columns.append({"key": key, "label": label, "count": len(cards),
                            "leads": cards})

        context = {
            **self.each_context(request),
            "title": _("Pipeline board"),
            "subtitle": None,
            "board": {
                "columns": columns,
                "total": total,
                "unassigned": unassigned,
                "hot": hot,
            },
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/kanban.html", context)

    #: The picker asks for the whole library at once and filters in the
    #: browser, so the payload is capped rather than left unbounded. Raised if
    #: a library ever grows past it, because a silently truncated picker looks
    #: to an editor like a missing image.
    MEDIA_PICKER_LIMIT = 300

    def media_picker(self, request):
        """JSON for the image picker's grid, newest first.

        Permission is on *viewing* the library, not editing it: choosing an
        image writes a path into a field the caller is already allowed to
        change, and that check belongs to the form being submitted, not to a
        read-only endpoint. Requiring `add`/`change` here would lock the
        picker out for exactly the editors who have the most to fill in.

        Unpublished items are omitted, which is what `is_published` is for on
        this model. Retiring an image this way cannot break a page that
        already stores the path as text -- the field keeps working, the image
        simply stops being offered for new choices.
        """
        media = apps.get_model("content", "MediaItem")
        # The permission check belongs to the model's ModelAdmin, not to the
        # site: AdminSite has no has_view_permission of its own.
        media_admin = self._registry.get(media)
        if media_admin is None or not media_admin.has_view_permission(request, media):
            return JsonResponse({"images": []}, status=403)

        # A row whose file was removed server-side has no path, and offering a
        # cell that writes an empty string would quietly blank the field it was
        # meant to fill. Such rows are skipped rather than rendered broken.
        library = media.objects.filter(is_published=True).exclude(image="")
        items = [
            {
                "id": obj.pk,
                "title": obj.title or obj.filename,
                "path": obj.public_path,
                "alt": obj.alt_text or obj.title or obj.filename,
                # url is safe on a non-empty image; the empty case is already
                # excluded above, so this cannot raise.
                "thumb": obj.image.url,
                "size": obj.size_display,
            }
            for obj in library.order_by("-created_at")[:self.MEDIA_PICKER_LIMIT]
        ]
        return JsonResponse({
            "images": items,
            # Counted from the same queryset, so a library full of file-less
            # rows cannot inflate this into a permanent "+ images" warning.
            "truncated": library.count() > len(items),
        })

    # Rows shown per model, and models shown, keep one keystroke cheap even
    # though the query fans out across every registered model.
    SEARCH_ROWS = 5
    SEARCH_MODELS = 12

    def studio_search(self, request, extra_context=None):
        term = (request.GET.get("q") or "").strip()
        groups = self._search_groups(request, term) if term else []
        context = {
            **self.each_context(request),
            "title": _("Search"),
            "subtitle": None,
            "search_term": term,
            "search_groups": groups,
            "search_total": sum(len(g["results"]) for g in groups),
            "searchable_models": self._searchable(request),
            **(extra_context or {}),
        }
        return render(request, "admin/search.html", context)

    def _model_label(self, meta):
        """Human name for a model, preferring the sidebar's own wording.

        Django's verbose_name_plural is often the wrong shape for a heading:
        FAQ is "faqs", LeadActivity is "lead activities". The nav already says
        "FAQs" and "Activity log", so reusing that keeps one name per model
        across the whole admin. A model with no nav entry falls back to the
        plural with a single leading capital.
        """
        url_name = f"{meta.app_label}_{meta.model_name}_changelist"
        label = _nav_labels().get(url_name)
        if label:
            return label
        plural = meta.verbose_name_plural
        return plural[:1].upper() + plural[1:]

    def _searchable(self, request):
        """Registered models this user may view and that are searchable.

        `search_fields` is the admin's own declaration of what is text-searchable
        for a model, so reusing it keeps global search consistent with each
        changelist's search instead of guessing column names here.
        """
        out = []
        for model, model_admin in self._registry.items():
            if not model_admin.search_fields:
                continue
            try:
                if not model_admin.has_view_or_change_permission(request):
                    continue
            except Exception:
                continue
            meta = model._meta
            url_name = f"{meta.app_label}_{meta.model_name}_changelist"
            out.append({
                "label": self._model_label(meta),
                "changelist_url": reverse(f"admin:{url_name}"),
            })
        out.sort(key=lambda row: row["label"])
        return out[: self.SEARCH_MODELS]
    def _search_groups(self, request, term):
        """Run the term against every searchable model and group the hits.

        Walks the registry directly rather than the `_searchable` list, because
        that one returns display dicts for the template while the query needs the
        ModelAdmin. Both apply the same two gates -- declared search_fields and
        view permission -- so a model cannot appear in one and not the other.
        """
        groups = []
        for model, model_admin in self._registry.items():
            if not model_admin.search_fields:
                continue
            try:
                if not model_admin.has_view_or_change_permission(request):
                    continue
                # get_search_results is the same code path a changelist search
                # uses, so per-model overrides and the search_fields prefixes
                # (=, ^, @) behave identically in both places.
                queryset, _duplicates = model_admin.get_search_results(
                    request, model._default_manager.get_queryset(), term
                )
                hits = list(queryset[: self.SEARCH_ROWS])
            except Exception:
                # One misbehaving model (bad search_fields, unmigrated table)
                # must not take down the whole search page.
                continue
            if not hits:
                continue
            meta = model._meta
            url_name = f"{meta.app_label}_{meta.model_name}_changelist"
            changelist = reverse(f"admin:{url_name}")
            groups.append({
                "label": self._model_label(meta),
                "results": [self._search_row(model_admin, obj) for obj in hits],
                # Precomputed rather than assembled in the template: the admin
                # URL name is <app_label>_<model_name>_<action>, so a bare app
                # label in a template could only ever reverse by luck.
                "changelist_url": f"{changelist}?q={quote(term)}",
            })

        groups.sort(key=lambda row: row["label"])
        return groups[: self.SEARCH_MODELS]

    def _search_row(self, model_admin, obj):
        """Label plus a deep link to the change form for one hit.

        The label falls back through the admin's own display methods so the
        result reads the same way the changelist column does.
        """
        try:
            label = str(model_admin.get_object_name(obj))
        except Exception:
            label = str(obj)
        return {
            "label": label,
            "url": reverse(
                f"admin:{model_admin.model._meta.app_label}_"
                f"{model_admin.model._meta.model_name}_change",
                args=[obj.pk],
            ),
        }

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
    #
    # `trend` is a (direction, percentage) pair measured over the last 30 days
    # against the 30 before, or None where the number has no flow to measure.
    # Overdue tasks are a backlog, not a rate, and Service carries no
    # timestamp at all, so inventing a percentage for those two would put a
    # confident-looking "up 0%" next to a number it does not describe.
    content = _content_counts()
    pages_qs = apps.get_app_config("content").get_model("Page").objects
    media_qs = apps.get_app_config("content").get_model("MediaItem").objects

    stats = [
        {
            "label": "Open leads",
            "value": open_leads.count(),
            "hint": f"{new_leads.count()} awaiting first contact",
            "url": "admin:crm_lead_changelist",
            "query": "",
            "tone": "t-leads",
            "icon": ICON["leads"],
            "trend": _volume_trend(Lead.objects.all(), now),
        },
        {
            "label": "Hot leads",
            "value": hot.count(),
            "hint": "Score 70 or above",
            "url": "admin:crm_lead_changelist",
            "query": "?score__gte=70",
            "tone": "t-hot",
            "icon": ICON["hot"],
            "trend": _volume_trend(hot, now),
        },
        {
            "label": "Overdue tasks",
            "value": overdue.count(),
            "hint": f"{open_tasks.count()} open in total",
            "url": "admin:crm_task_changelist",
            "query": "",
            "tone": "t-tasks",
            "icon": ICON["task"],
            "trend": None,
        },
        {
            "label": "Pages",
            "value": content["pages"],
            "hint": f"{content['published']} published",
            "url": "admin:content_page_changelist",
            "query": "",
            "tone": "t-pages",
            "icon": ICON["page"],
            "trend": _volume_trend(pages_qs, now),
        },
        {
            "label": "Media items",
            "value": content["media"],
            "hint": "In the library",
            "url": "admin:content_mediaitem_changelist",
            "query": "",
            "tone": "t-media",
            "icon": ICON["media"],
            "trend": _volume_trend(media_qs, now),
        },
        {
            "label": "Services",
            "value": content["services"],
            "hint": "Across the site",
            "url": "admin:crm_service_changelist",
            "query": "",
            "tone": "t-today",
            "icon": ICON["service"],
            "trend": None,
        },
    ]

    recent_leads = Lead.objects.select_related("service", "owner")[:8]

    upcoming = open_tasks.select_related("lead", "assigned_to").order_by(
        "due_at"
    )[:8]

    # Content health: what is present but not doing its job. The stat cards
    # above already own the totals, so repeating them here would say the same
    # thing twice. These are the gaps an owner can act on instead.
    content_health = _content_gaps()

    # Leads created per calendar month, oldest first, for the trend chart.
    #
    # Months are walked with calendar arithmetic rather than by stepping back
    # 30 days at a time. Subtracting 30 days from a 31st lands in the previous
    # month, which made two bars share a label and skip one entirely, and
    # matching rows on the month number alone folded leads from the same month
    # of the previous year into this year's bar.
    counts = {
        (row["month"].year, row["month"].month): row["total"]
        for row in Lead.objects
        .filter(created_at__gte=_month_floor(now, TREND_MONTHS - 1))
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Count("id"))
    }
    monthly_data = []
    for offset in range(TREND_MONTHS - 1, -1, -1):
        year, month = _shift_month(now.year, now.month, -offset)
        monthly_data.append({
            "label": MONTH_LABELS[month - 1],
            "value": counts.get((year, month), 0),
        })

    # Bars are drawn as a percentage of the plot height, so they have to be
    # scaled against the busiest month. Writing the raw lead count straight
    # into the height made a real month overflow the panel and a quiet one
    # render as an invisible sliver.
    peak_month = max((row["value"] for row in monthly_data), default=0)
    for row in monthly_data:
        row["pct"] = (
            int(round(row["value"] * 100 / peak_month)) if peak_month else 0
        )

    recent_contacts = (
        apps.get_app_config("crm").get_model("Contact").objects
        .order_by("-created_at")[:5]
    )

    # Scale against the busiest service, not the raw count, so the bars are
    # comparable to each other rather than rendering at 1% when every service
    # has one lead.
    service_rows = list(
        apps.get_app_config("crm")
        .get_model("Service")
        .objects.annotate(lead_total=Count("leads"))
        .order_by("-lead_total", "sort_order")[:6]
    )
    peak_service = max([s.lead_total for s in service_rows], default=0)
    for svc in service_rows:
        svc.pct = (
            int(round(svc.lead_total * 100 / peak_service)) if peak_service else 0
        )

    return {
        "stats": stats,
        "pipeline": rows,
        "recent_leads": recent_leads,
        "upcoming_tasks": upcoming,
        "top_services": service_rows,
        "content_health": content_health,
        "monthly_data": monthly_data,
        "recent_contacts": recent_contacts,
        # The dashboard heading prints the current date. Nothing in the admin
        # context provides it, so it is passed explicitly rather than left to
        # render blank.
        "today": timezone.localdate(),
    }


#: Row colours for the content-gap list: a row reading zero is good news.
GAP_OK = "#1F7A4D"
GAP_WARN = "#A8620F"
GAP_BROKEN = "#B3261E"


def _gap_colour(count, when_bad=GAP_WARN):
    return when_bad if count else GAP_OK


def _content_gaps():
    """Content that exists but is not reaching a visitor.

    The dashboard's stat cards already report how much content there is, so
    this reports how much of it is not working: pages saved but unpublished,
    pages with nothing in them, sections switched off, services taken out of
    the menu. A row reading 0 is the desired state, which is the opposite of
    the totals it replaces -- a growing number here is the thing to fix.

    Wrapped in try/except for the same reason as `_content_counts`: a database
    without the tables yet must still render the dashboard.
    """
    try:
        content = apps.get_app_config("content")
        crm = apps.get_app_config("crm")
        pages = content.get_model("Page").objects
        sections = content.get_model("Section").objects
        services = crm.get_model("Service").objects

        unpublished = pages.filter(is_published=False).count()
        empty = pages.annotate(n=Count("sections")).filter(n=0).count()
        hidden = sections.filter(is_visible=False).count()
        inactive = services.filter(is_active=False).count()

        return [
            {
                "label": "Unpublished pages",
                "value": unpublished,
                "note": "Saved but hidden from visitors",
                "colour": _gap_colour(unpublished),
            },
            {
                "label": "Empty pages",
                "value": empty,
                "note": "No sections, so they render blank",
                # An empty page is published-and-useless, not merely unwritten.
                "colour": _gap_colour(empty, GAP_BROKEN),
            },
            {
                "label": "Hidden sections",
                "value": hidden,
                "note": "Authored but switched off",
                "colour": _gap_colour(hidden),
            },
            {
                "label": "Inactive services",
                "value": inactive,
                "note": "Not offered on the site",
                "colour": _gap_colour(inactive),
            },
        ]
    except Exception:
        return []


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
