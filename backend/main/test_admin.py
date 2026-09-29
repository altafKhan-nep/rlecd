"""Tests for the custom dashboard admin site.

The dashboard replaced Django's model index, so its failure mode is new: any
query that raises now 500s the landing page for every staff user, and the
sidebar counts run on *every* admin screen, not just the dashboard. These tests
pin both behaviours, plus the media library's file validation.
"""
import re
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from content.models import MediaItem, Page, Project, TrustBadge
from crm.models import Lead, LeadStatus, Service
from main.admin_site import (
    GAP_OK,
    MONTH_LABELS,
    StudioAdminSite,
    _month_floor,
    _shift_month,
)
from main.widgets import MediaPathWidget

MEDIA = tempfile.mkdtemp(prefix="studio-admin-tests-")


def png_bytes():
    # Smallest valid 1x1 PNG. Written as bytes rather than base64 so the test
    # does not depend on Pillow being importable at module scope.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@override_settings(MEDIA_ROOT=MEDIA)
class StudioAdminTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(self.admin)

    # --- the shell is the default site ---------------------------------
    def test_default_admin_site_is_the_studio_one(self):
        from django.contrib import admin as django_admin

        # admin.site is a lazy proxy; unwrap to reach the configured instance.
        site = django_admin.site._wrapped
        self.assertIsInstance(site, StudioAdminSite)
        self.assertEqual(site.__class__.__name__, "StudioAdminSite")
        self.assertEqual(site.site_title, "REAL LIFE EXPERIENCE Studio")
        self.assertEqual(site.site_header, "REAL LIFE EXPERIENCE Studio")

    def test_dashboard_replaces_model_index(self):
        """admin:index must render the dashboard, not Django's app list."""
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/dashboard.html")
        self.assertNotContains(response, "Available apps", status_code=200)

    def test_dashboard_renders_with_zero_data(self):
        """An empty database is a normal first-run state, not an error."""
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"][0]["value"], 0)
        self.assertEqual(len(response.context["pipeline"]), 6)
        self.assertTrue(all(r["total"] == 0 and r["pct"] == 0
                            for r in response.context["pipeline"]))
        self.assertContains(response, "No leads yet")

    def test_dashboard_counts_reflect_data(self):
        from crm.models import Lead, LeadStatus, Task
        from crm.services import set_status

        service = self._service("Kitchens")
        lead = Lead.objects.create(
            name="Dana Ortiz", email="dana@example.com", service=service)
        won = Lead.objects.create(
            name="Sam Reed", email="sam@example.com", service=service)
        set_status(won, LeadStatus.WON, actor=self.admin)
        Task.objects.create(lead=lead, title="Call back")

        response = self.client.get(reverse("admin:index"))
        labels = {s["label"]: s["value"] for s in response.context["stats"]}
        self.assertEqual(labels["Open leads"], 1)
        self.assertEqual(labels["Overdue tasks"], 0)
        self.assertContains(response, "Dana Ortiz")

    def test_every_stat_card_has_an_icon_and_query_key(self):
        """The template renders s.icon and s.query unconditionally, so a stat
        missing either key raises NoReverseMatch/VariableDoesNotExist on the
        dashboard rather than failing quietly."""
        response = self.client.get(reverse("admin:index"))
        stats = response.context["stats"]
        self.assertEqual(len(stats), 6)
        for stat in stats:
            with self.subTest(label=stat["label"]):
                self.assertIn("icon", stat)
                self.assertIn("<svg", stat["icon"])
                self.assertIn("query", stat)
                self.assertTrue(stat["url"].startswith("admin:"))

    def test_stat_cards_render_icon_and_inner_wrapper(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["stats"][0]["label"], "Open leads")
        self.assertContains(response, 'class="stat-icon"')
        self.assertContains(response, 'class="stat-inner"')

    def test_score_chips_carry_a_colour_tier(self):
        """The stylesheet only colours .s-hi/.s-mid/.s-lo, so a chip without a
        tier class falls back to the untiered rule and every score looks the
        same. Cover the three boundaries explicitly."""
        from crm.models import Lead

        Lead.objects.all().delete()
        for i, score in enumerate((95, 70, 69, 40, 39, 0)):
            Lead.objects.create(name=f"L{i}", email=f"l{i}@example.com",
                                score=score)
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn("score-chip s-hi", html)
        self.assertIn("score-chip s-mid", html)
        self.assertIn("score-chip s-lo", html)
        # No untiered chip anywhere.
        self.assertNotIn('class="score-chip"', html)

    def test_score_tier_filter_boundaries(self):
        from main.templatetags.studio_tags import score_tier

        self.assertEqual(score_tier(100), "s-hi")
        self.assertEqual(score_tier(70), "s-hi")
        self.assertEqual(score_tier(69), "s-mid")
        self.assertEqual(score_tier(40), "s-mid")
        self.assertEqual(score_tier(39), "s-lo")
        self.assertEqual(score_tier(0), "s-lo")
        self.assertEqual(score_tier(None), "s-lo")
        self.assertEqual(score_tier("nonsense"), "s-lo")

    def test_sidebar_replaces_the_tab_bar(self):
        """The redesign moved navigation from a horizontal tab bar to a fixed
        left sidebar. The old .brand-nav markup must be gone, and the sidebar
        must carry the grouped sections."""
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        self.assertNotIn("brand-nav", html)
        self.assertNotIn("brand-tab", html)
        self.assertIn('class="studio-sidebar"', html)
        self.assertIn('class="sidebar-nav"', html)
        self.assertIn('class="nav-group"', html)

    def test_sidebar_groups_crm_and_content_separately(self):
        """Related sections sit together: CRM items under one label, content
        items under another, so the owner is not scanning a flat list."""
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        self.assertIn("CRM", html)
        self.assertIn("Content", html)
        # Both groups present, each with its items.
        for item in ("Leads", "Tasks", "Contacts"):
            self.assertIn(item, html)
        for item in ("Pages", "Sections", "Services", "Media", "Site settings"):
            self.assertIn(item, html)

    def test_sidebar_marks_the_active_section(self):
        """Exactly one nav item carries is-active, and it is the one matching
        the current URL."""
        cases = [
            (reverse("admin:index"), "Overview"),
            (reverse("admin:crm_lead_changelist"), "Leads"),
            (reverse("admin:content_page_changelist"), "Pages"),
            (reverse("admin:content_mediaitem_changelist"), "Media"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertEqual(html.count("nav-item is-active"), 1)
                # The active item's label appears in the sidebar.
                self.assertIn(expected, html)

    def test_sidebar_shows_live_counts(self):
        """Badges reflect real data: a new lead shows a count on Leads, and
        the count is absent when there is nothing to report."""
        from crm.models import Lead

        Lead.objects.create(name="Fresh", email="fresh@example.com", status="new")
        html = self.client.get(reverse("admin:crm_lead_changelist")).content.decode()
        self.assertIn("nav-count", html)

    def test_sidebar_has_user_tools_and_view_site(self):
        """The sidebar footer carries the user identity and the links an owner
        needs, so they are reachable without scrolling back to a top bar."""
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        self.assertIn("sidebar-user", html)
        self.assertIn("sidebar-footer", html)
        self.assertIn("View site", html)
        self.assertIn("Log out", html)

    def test_mobile_drawer_toggle_is_present(self):
        """Below 900px the sidebar becomes an overlay drawer, so the toggle
        button and its overlay must exist in the markup, and the script that
        drives them must reference the open state."""
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        self.assertIn('id="sidebar-toggle"', html)
        self.assertIn('id="sidebar-overlay"', html)
        # sidebar-open is applied by JS, so it appears in the script, not the
        # static markup.
        self.assertIn("sidebar-open", html)
        self.assertIn("sidebar-toggle", html)

    def test_content_health_panel_is_present(self):
        """The dashboard reports content gaps, not just CRM. The panel must
        list the four kinds of work that does not reach a visitor."""
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Content health")
        for label in ("Unpublished pages", "Empty pages", "Hidden sections",
                      "Inactive services"):
            self.assertContains(response, label)

    def test_content_health_reports_gaps_from_real_data(self):
        """Health numbers come from the database, not placeholders, and they
        count problems rather than repeating the totals in the stat cards."""
        from content.models import Page, Section
        from crm.models import Service

        published = Page.objects.create(title="Home", path="/", slug="home",
                                        is_published=True)
        Page.objects.create(title="Draft", path="/draft/", slug="draft",
                            is_published=False)
        Service.objects.create(name="Live Service", slug="live-service")
        Service.objects.create(name="Retired Service", slug="retired-service",
                               is_active=False)
        Section.objects.create(page=published, key="hero", type="hero",
                               content_html="<h1>Hi</h1>", position=0)
        Section.objects.create(page=published, key="off", type="hero",
                               content_html="<p>hidden</p>", position=1,
                               is_visible=False)

        response = self.client.get(reverse("admin:index"))
        by_label = {row["label"]: row for row in response.context["content_health"]}
        self.assertEqual(by_label["Unpublished pages"]["value"], 1)
        # The draft has no sections, so it counts as empty as well.
        self.assertEqual(by_label["Empty pages"]["value"], 1)
        self.assertEqual(by_label["Hidden sections"]["value"], 1)
        self.assertEqual(by_label["Inactive services"]["value"], 1)

    def test_content_health_colour_flags_a_gap(self):
        """A row reading zero is the healthy state, so colour has to follow the
        count -- otherwise a clean site still looks like a list of problems."""
        from content.models import Page

        response = self.client.get(reverse("admin:index"))
        clean = {row["label"]: row for row in response.context["content_health"]}
        self.assertTrue(clean)
        for row in clean.values():
            self.assertEqual(row["colour"], GAP_OK, f"{row['label']} should be clean")

        Page.objects.create(title="Draft", path="/draft/", slug="draft",
                            is_published=False)
        response = self.client.get(reverse("admin:index"))
        after = {row["label"]: row for row in response.context["content_health"]}
        self.assertNotEqual(after["Unpublished pages"]["colour"], GAP_OK)
        # A gap in one row must not paint the others.
        self.assertEqual(after["Hidden sections"]["colour"], GAP_OK)

    def test_stat_cards_use_tone_classes_not_nth_child(self):
        """Colour is assigned by a tone class from the view, so reordering the
        cards cannot shuffle the palette. Every card must carry a tone."""
        response = self.client.get(reverse("admin:index"))
        stats = response.context["stats"]
        tones = {s["tone"] for s in stats}
        self.assertEqual(len(tones), 6, "each card should have a distinct tone")
        for tone in tones:
            self.assertTrue(tone.startswith("t-"), f"unexpected tone {tone!r}")

    def test_dashboard_does_not_render_a_stray_default_heading(self):
        """Django's content_title block emits its own <h1> above the content
        area. The dashboard has its own greeting, so the inherited heading must
        be suppressed or the page opens with two competing titles."""
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, 'class="dash-title"')
        self.assertNotContains(response, "<h1>Pipeline</h1>")
        self.assertEqual(response.content.decode().count("<h1"), 1)

    def test_stat_icons_are_not_escaped(self):
        """Regression: the icon SVG is built in Python, so without mark_safe
        Django escapes it and the dashboard shows literal "&lt;svg" text in
        every card. One icon is already raw markup in base_site.html, so the
        count must exceed that baseline."""
        response = self.client.get(reverse("admin:index"))
        html = response.content.decode()
        icon_count = html.count('class="stat-icon"')
        self.assertEqual(icon_count, 6)
        self.assertNotIn("&lt;svg", html)
        self.assertGreaterEqual(html.count("<svg"), icon_count)

    def test_pipeline_stages_have_distinct_colours(self):
        """Each status is its own pill class, so a six-stage funnel is
        distinguishable instead of six shades of grey."""
        from crm.models import Lead

        for status in ("new", "contacted", "qualified", "won", "lost"):
            Lead.objects.create(name=status, email=f"{status}@example.com",
                                status=status)
        response = self.client.get(reverse("admin:index"))
        for cls in ("pill-new", "pill-contacted", "pill-qualified",
                    "pill-won", "pill-lost"):
            self.assertContains(response, cls)

    def test_dashboard_brand_line_is_populated(self):
        """Regression: the dashboard rendered its own context dict and skipped
        each_context, so site_header came through empty and the header read as
        a bare link."""
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "REAL LIFE EXPERIENCE Studio")
        self.assertNotContains(response, "Studio Studio")

    def test_dashboard_shows_sidebar_badges_when_work_exists(self):
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Nia", email="nia@example.com")
        Task.objects.create(lead=lead, title="Ring back")
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "nav-count")

    def test_lead_list_renders_score_chips(self):
        """`score` is a model field, so a same-named display method is silently
        ignored by admin. The chip only appears if the column is wired to a
        distinct name."""
        from crm.models import Lead

        Lead.objects.create(name="Ivo", email="ivo@example.com", score=91)
        response = self.client.get(reverse("admin:crm_lead_changelist"))
        self.assertContains(response, "score-chip")
        self.assertContains(response, "91")

    def test_tab_counts_present_on_every_admin_page(self):
        """Tab badges come from each_context, so they must ride along on
        non-dashboard screens too."""
        response = self.client.get(reverse("admin:crm_lead_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("tab_counts", response.context)
        self.assertEqual(response.context["tab_counts"]["new_leads"], 0)

    def test_tab_counts_reflect_open_work(self):
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Kim", email="kim@example.com")
        Lead.objects.create(name="Lee", email="lee@example.com", status="won")
        Task.objects.create(lead=lead, title="Send quote")

        response = self.client.get(reverse("admin:crm_lead_changelist"))
        counts = response.context["tab_counts"]
        self.assertEqual(counts["new_leads"], 1)
        self.assertEqual(counts["open_tasks"], 1)

    def test_pipeline_bars_are_share_of_open_leads(self):
        """Bars are a share of the open funnel. Normalising to the largest
        bucket once made a 1-per-stage pipeline render four full bars, so this
        pins the share-based scale."""
        from crm.models import Lead, LeadStatus

        for i in range(3):
            Lead.objects.create(name=f"P{i}", email=f"p{i}@example.com")
        Lead.objects.create(name="W", email="w@example.com", status=LeadStatus.WON)

        response = self.client.get(reverse("admin:index"))
        rows = {r["status"]: r for r in response.context["pipeline"]}
        self.assertEqual(rows["new"]["total"], 3)
        self.assertEqual(rows["new"]["pct"], 100)   # sole open stage
        self.assertEqual(rows["won"]["pct"], 0)     # closed, excluded from total
        for row in response.context["pipeline"]:
            self.assertGreaterEqual(row["pct"], 0)
            self.assertLessEqual(row["pct"], 100)

    def test_service_bars_scale_to_the_busiest(self):
        """Widths are a share of the largest count, so equal counts render at
        equal full-width bars rather than a meaningless 1%."""
        from crm.models import Lead, Service

        busy = Service.objects.create(name="Kitchens", slug="kitchens")
        quiet = Service.objects.create(name="Decks", slug="decks")
        for i in range(4):
            Lead.objects.create(name=f"K{i}", email=f"k{i}@example.com", service=busy)
        Lead.objects.create(name="D0", email="d0@example.com", service=quiet)

        response = self.client.get(reverse("admin:index"))
        by_name = {s.name: s for s in response.context["top_services"]}
        self.assertEqual(by_name["Kitchens"].pct, 100)
        self.assertEqual(by_name["Decks"].pct, 25)

    def test_service_bars_all_zero_without_leads(self):
        from crm.models import Service

        Service.objects.create(name="Kitchens", slug="kitchens")
        response = self.client.get(reverse("admin:index"))
        self.assertTrue(all(s.pct == 0 for s in response.context["top_services"]))

    def test_pipeline_marks_closed_stages(self):
        from crm.models import Lead, LeadStatus

        Lead.objects.create(name="A", email="a@example.com")
        Lead.objects.create(name="B", email="b@example.com", status=LeadStatus.WON)
        response = self.client.get(reverse("admin:index"))
        rows = {r["status"]: r["is_open"] for r in response.context["pipeline"]}
        self.assertTrue(rows["new"])
        self.assertFalse(rows["won"])
        self.assertFalse(rows["lost"])

    def test_pipeline_lists_every_status_even_when_empty(self):
        """A stage with no leads must still appear, otherwise the funnel
        silently loses stages as data ages out of them."""
        from crm.models import LeadStatus

        response = self.client.get(reverse("admin:index"))
        listed = {r["status"] for r in response.context["pipeline"]}
        self.assertEqual(listed, {s for s, _ in LeadStatus.choices})

    # --- tab bar targets exist -----------------------------------------
    def test_every_tab_url_resolves(self):
        """A tab pointing at a missing model is a 500 on click. Cheap to
        assert here, expensive to discover in production."""
        for name in (
            "admin:index",
            "admin:crm_lead_changelist",
            "admin:crm_task_changelist",
            "admin:crm_contact_changelist",
            "admin:content_page_changelist",
            "admin:crm_service_changelist",
            "admin:content_mediaitem_changelist",
            "admin:content_sitesetting_changelist",
        ):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_login_page_still_works(self):
        """The branded login overrides base_site; make sure it kept its form
        contract after the shell changed underneath it."""
        out = self.client.post(
            reverse("admin:login"),
            {"username": "owner", "password": "pw", "next": reverse("admin:index")},
        )
        self.assertEqual(out.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    # --- media library ---------------------------------------------------
    def test_media_upload_stores_file_and_path(self):
        item = MediaItem.objects.create(
            image=SimpleUploadedFile("deck.png", png_bytes(), "image/png"),
            title="Deck photo", alt_text="A finished deck")
        self.assertTrue(item.image.name.endswith(".png"))
        self.assertIn("uploads/", item.image.name)
        self.assertTrue((Path(MEDIA) / item.image.name).exists())
        self.assertEqual(item.public_path, item.image.url)
        self.assertEqual(str(item), "Deck photo")

    def test_media_title_falls_back_to_filename(self):
        item = MediaItem.objects.create(
            image=SimpleUploadedFile("kitchen-after.png", png_bytes(), "image/png"))
        self.assertEqual(item.title, "kitchen-after")

    def test_media_rejects_non_image_extension(self):
        item = MediaItem(image="shell.php")
        with self.assertRaises(Exception) as ctx:
            item.clean()
        self.assertIn("Unsupported image type", str(ctx.exception))

    def test_media_accepts_each_allowed_extension(self):
        for ext in MediaItem.ALLOWED_EXTENSIONS:
            with self.subTest(ext=ext):
                item = MediaItem(image=f"photo{ext}")
                item.clean()  # must not raise

    def test_media_changelist_and_add_page_render(self):
        MediaItem.objects.create(
            image=SimpleUploadedFile("a.png", png_bytes(), "image/png"))
        self.assertEqual(
            self.client.get(reverse("admin:content_mediaitem_changelist")).status_code,
            200)
        self.assertEqual(
            self.client.get(reverse("admin:content_mediaitem_add")).status_code, 200)

    def test_media_publish_actions(self):
        a = MediaItem.objects.create(
            image=SimpleUploadedFile("a.png", png_bytes(), "image/png"))
        b = MediaItem.objects.create(
            image=SimpleUploadedFile("b.png", png_bytes(), "image/png"))
        url = reverse("admin:content_mediaitem_changelist")

        self.client.post(url, {"action": "action_unpublish", "_selected_action": [a.pk]})
        a.refresh_from_db()
        self.assertFalse(a.is_published)

        self.client.post(url, {"action": "action_publish", "_selected_action": [a.pk, b.pk]})
        a.refresh_from_db()
        self.assertTrue(a.is_published)

    def test_ephemeral_banner_shown_on_add_form_when_flagged(self):
        """The warning must actually reach the page, not just the context.
        A banner that is computed but never rendered is worse than none."""
        import os

        os.environ["STUDIO_EPHEMERAL_MEDIA"] = "1"
        try:
            response = self.client.get(reverse("admin:content_mediaitem_add"))
        finally:
            os.environ.pop("STUDIO_EPHEMERAL_MEDIA", None)
        self.assertContains(response, "Uploads are not persistent")

    def test_no_ephemeral_banner_on_normal_host(self):
        import os

        os.environ.pop("STUDIO_EPHEMERAL_MEDIA", None)
        os.environ.pop("RENDER", None)
        response = self.client.get(reverse("admin:content_mediaitem_add"))
        self.assertNotContains(response, "Uploads are not persistent")

    @override_settings()
    def test_ephemeral_warning_flagged_on_render(self):
        """Uploads on Render's free tier are lost on redeploy. The add form
        has to say so, otherwise the media library looks durable when it is
        not."""
        import os

        from content.admin import MediaItemAdmin
        ma = MediaItemAdmin(MediaItem, StudioAdminSite(name="admin"))

        os.environ.pop("STUDIO_EPHEMERAL_MEDIA", None)
        os.environ.pop("RENDER", None)
        self.assertFalse(ma._storage_is_ephemeral())

        os.environ["STUDIO_EPHEMERAL_MEDIA"] = "1"
        try:
            self.assertTrue(ma._storage_is_ephemeral())
        finally:
            os.environ.pop("STUDIO_EPHEMERAL_MEDIA", None)

    # --- public site must be untouched ---------------------------------
    def test_public_pages_still_render(self):
        # These are the real routes in main/urls.py. The /about-us/ and
        # /contact-us/ slugs 404 -- the site uses /about/ and /contact/.
        for path in ("/", "/about/", "/contact/", "/kitchen-remodeling/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def _service(self, name):
        from crm.models import Service

        return Service.objects.create(name=name, slug=name.lower())


class DashboardWidgetsTests(TestCase):
    """The trend badges, the lead chart and the date on the dashboard heading.

    Each of these shipped broken once. They are quiet failures -- the dashboard
    still renders, the numbers are just wrong -- so they get pinned here.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(self.admin)
        self.service = Service.objects.create(name="Kitchens", slug="kitchens")

    def _lead_in_month(self, months_back, name="L"):
        """Create a lead dated inside the calendar month `months_back` back."""
        from crm.models import Lead, LeadStatus
        from main.admin_site import _shift_month

        now = timezone.now()
        year, month = _shift_month(now.year, now.month, -months_back)
        return Lead.objects.create(
            name=name,
            email=f"{name}@example.com",
            service=self.service,
            status=LeadStatus.NEW,
            created_at=now.replace(year=year, month=month, day=15),
        )

    def _series(self):
        return self.client.get(reverse("admin:index")).context["monthly_data"]

    def test_chart_window_reaches_back_six_months(self):
        """The window has to open *behind* now.

        `_month_floor` originally added its offset instead of subtracting it,
        so the query filtered from five months in the future and every bar
        rendered at zero while still looking like a working chart.
        """
        now = timezone.now()
        series = self._series()
        self.assertEqual(len(series), 6)
        self.assertLess(
            _month_floor(now, 5), now,
            "chart window must start in the past",
        )
        # The newest bar is the current month.
        self.assertEqual(series[-1]["label"], MONTH_LABELS[now.month - 1])

    def test_chart_labels_track_six_distinct_calendar_months(self):
        """Months are walked with calendar arithmetic, not by subtracting 30
        days. Stepping back 30 days from a 31st lands in the previous month,
        which produced two bars sharing one label and skipped a month.

        The six months are checked as (year, month) pairs, since a window
        spanning a year boundary legitimately repeats a name.
        """
        now = timezone.now()
        keys = [
            _shift_month(now.year, now.month, -offset)
            for offset in range(5, -1, -1)
        ]
        self.assertEqual(len(set(keys)), 6, "chart window repeats a month")
        self.assertEqual(keys, sorted(keys), "chart is not oldest-first")
        self.assertEqual(keys[-1], (now.year, now.month))

        for months_back in range(6):
            self._lead_in_month(months_back, name=f"L{months_back}")
        series = self._series()
        self.assertEqual([row["value"] for row in series], [1, 1, 1, 1, 1, 1])
        self.assertEqual(
            [row["label"] for row in series],
            [MONTH_LABELS[key[1] - 1] for key in keys],
        )

    def test_chart_ignores_leads_from_the_same_month_last_year(self):
        """Rows are matched on year *and* month. Matching the month number
        alone folded a lead from the same month of the previous year into this
        year's bar and roughly doubled the total."""
        now = timezone.now()
        self._lead_in_month(0, name="recent")
        Lead.objects.create(
            name="ancient",
            email="ancient@example.com",
            service=self.service,
            status=LeadStatus.NEW,
            created_at=now.replace(year=now.year - 1, month=now.month, day=15),
        )
        self.assertEqual(sum(row["value"] for row in self._series()), 1)

    def test_chart_bars_are_scaled_to_the_busiest_month(self):
        """Bar heights are percentages of the plot, so they must be scaled.
        Writing the raw lead count into the height let a busy month overflow
        the panel and a quiet month render as an invisible sliver."""
        for _ in range(9):
            self._lead_in_month(2, name=f"busy{_}")
        self._lead_in_month(0, name="quiet")
        series = self._series()
        busiest = max(row["value"] for row in series)
        self.assertEqual(max(row["pct"] for row in series), 100)
        for row in series:
            with self.subTest(label=row["label"]):
                self.assertLessEqual(row["pct"], 100)
                self.assertEqual(row["pct"], round(row["value"] * 100 / busiest))

    def test_rendered_bar_heights_use_the_scaled_value(self):
        """The template must draw the scaled percentage, not the raw count."""
        for _ in range(9):
            self._lead_in_month(2, name=f"busy{_}")
        response = self.client.get(reverse("admin:index"))
        heights = re.findall(r'chart-bar[^"]*"\s*style="height: (\d+)%"',
                             response.content.decode())
        self.assertEqual(
            heights,
            [str(row["pct"]) for row in response.context["monthly_data"]],
        )

    def test_trend_is_omitted_where_no_period_comparison_exists(self):
        """Overdue tasks are a backlog and Service has no created_at, so
        neither can be compared to a previous period. Those cards must render
        no badge rather than a confident-looking "up 0%"."""
        response = self.client.get(reverse("admin:index"))
        stats = {s["label"]: s for s in response.context["stats"]}
        self.assertIsNone(stats["Overdue tasks"]["trend"])
        self.assertIsNone(stats["Services"]["trend"])
        badges = re.findall(r'class="stat-trend (trend-\w+)"',
                            response.content.decode())
        self.assertEqual(len(badges), 4)

    def test_trend_compares_equal_widthed_windows(self):
        """Both sides of the comparison cover the same span. The original
        compared all open leads against only the ones created over a month
        ago, so the badge described neither number on the card."""
        for _ in range(4):
            self._lead_in_month(0, name=f"cur{_}")
        for _ in range(2):
            self._lead_in_month(1, name=f"prev{_}")
        stats = {s["label"]: s for s in
                 self.client.get(reverse("admin:index")).context["stats"]}
        self.assertEqual(stats["Open leads"]["trend"], ("up", 100))

    def test_heading_has_a_date(self):
        """The heading prints `today`. Nothing in the admin context provides
        it, so the view has to -- otherwise the date line renders empty."""
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["today"], timezone.localdate())
        self.assertContains(response, timezone.localdate().strftime("%A"))

    def test_content_health_survives_alongside_the_new_panels(self):
        """Content health is a documented dashboard widget. Adding the lead
        chart, top services and contacts must not have displaced it."""
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Content health")
        self.assertContains(response, "Lead Trend")
        self.assertContains(response, "Top Services")
        self.assertContains(response, "Recent Contacts")


class ShellAndSearchTests(TestCase):
    """Phase 1 of the shell work: the nav, the notification badge, the
    context-aware add buttons, the collapse control and global search.

    Each of these replaced something decorative in the previous template -- a
    hard-coded badge, an input with no action, a repeated active check -- so the
    tests pin the behaviour that made them worth changing.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="shell", email="shell@example.com", password="pw-shell-123"
        )
        self.client.force_login(self.user)

    # ------------------------------------------------------------ navigation
    def test_nav_covers_every_registered_model(self):
        """The sidebar is built from the registry's models, so a model that is
        registered but missing from the nav is a silent gap."""
        from django.contrib import admin
        from django.test import RequestFactory

        # main.admin_config sets default_site, so the live registry hangs off
        # django.contrib.admin.site rather than a module-level instance.
        site = admin.site
        self.assertIsInstance(site, StudioAdminSite)

        # Permission checks need a request, and the URL name in the href is the
        # stable identifier; the label is deliberately not asserted because copy
        # changes must not fail this test.
        request = RequestFactory().get("/admin/")
        request.user = self.user

        html = self.client.get(reverse("admin:index")).content.decode()
        for model, model_admin in site._registry.items():
            if not model_admin.has_view_permission(request):
                continue
            meta = model._meta
            with self.subTest(model=meta.label_lower):
                # The nav item is identified by its href, not its label, so a
                # copy change cannot make this test fail for the wrong reason.
                url = reverse(
                    f"admin:{meta.app_label}_{meta.model_name}_changelist"
                )
                self.assertIn(f'href="{url}"', html)

    def test_active_marking_survives_a_model_without_a_nav_entry(self):
        """Every nav entry that matches the current URL is marked, and a URL
        with no entry (an add or change page) still marks its parent list."""
        html = self.client.get(
            reverse("admin:crm_lead_changelist")
        ).content.decode()
        self.assertEqual(html.count("nav-item is-active"), 1)

    # ------------------------------------------------------- notification bell
    def test_badge_is_absent_when_nothing_needs_attention(self):
        """A badge showing 0 trains the eye to ignore the bell, so at rest the
        button must not be rendered at all."""
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertNotIn("notif-badge", html)

    def test_badge_counts_new_leads_and_overdue_tasks(self):
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Busy", email="busy@example.com",
                                   status="new")
        Task.objects.create(
            lead=lead, title="Overdue", done=False,
            due_at=timezone.now() - timezone.timedelta(days=2),
        )
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["attention_count"], 2)
        self.assertContains(response, "notif-badge")

    def test_badge_ignores_done_and_future_tasks(self):
        """Only genuinely overdue work counts. A completed task or one due next
        week is not something to interrupt the owner about."""
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Calm", email="calm@example.com",
                                   status="contacted")
        Task.objects.create(lead=lead, title="Finished", done=True,
                            due_at=timezone.now() - timezone.timedelta(days=5))
        Task.objects.create(lead=lead, title="Later", done=False,
                            due_at=timezone.now() + timezone.timedelta(days=5))
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["attention_count"], 0)

    def test_bell_deep_links_into_the_queue_it_counts(self):
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Bell", email="bell@example.com",
                                   status="new")
        Task.objects.create(lead=lead, title="Late", done=False,
                            due_at=timezone.now() - timezone.timedelta(days=1))
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(
            response.context["attention_url"],
            reverse("admin:crm_task_changelist") + "?done__exact=0",
        )

    # ----------------------------------------------------------- add buttons
    def test_add_buttons_follow_the_screen_you_are_on(self):
        cases = [
            (reverse("admin:crm_lead_changelist"), "New lead"),
            (reverse("admin:content_page_changelist"), "New page"),
            (reverse("admin:crm_service_changelist"), "New service"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                labels = [link["label"] for link in response.context["add_links"]]
                self.assertIn(expected, labels)

    def test_add_buttons_hide_what_the_user_cannot_create(self):
        """A link the user would get a 403 from is worse than no link, so
        permissions are checked before the button is offered."""
        limited = User.objects.create_user(
            username="viewer", password="pw-viewer-123", is_staff=True
        )
        limited.user_permissions.add(
            Permission.objects.get(codename="view_lead", content_type__app_label="crm")
        )
        self.client.force_login(limited)
        response = self.client.get(reverse("admin:crm_lead_changelist"))
        self.assertEqual(response.context["add_links"], [])

    def test_add_buttons_are_not_repeated_on_the_index(self):
        """The dashboard has its own action areas -- the header buttons and the
        Quick actions panel. Repeating a create pair in the topbar printed the
        same two links twice on the one screen that already has them."""
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.context["add_links"], [])
        # The dashboard's own affordances must survive that.
        html = response.content.decode()
        self.assertContains(response, "Quick actions")
        self.assertIn(reverse("admin:crm_lead_add"), html)
        self.assertIn(reverse("admin:content_page_add"), html)

    # -------------------------------------------------------------- collapse
    def test_collapse_state_is_applied_before_first_paint(self):
        """The collapsed preference is read in <head> so the sidebar does not
        snap from full width to a rail on every reload."""
        html = self.client.get(reverse("admin:index")).content.decode()
        head = html.split("</head>")[0]
        self.assertIn("studio.sidebar.collapsed", head)
        self.assertIn("sidebar-collapsed", head)

    def test_collapse_button_is_a_real_button_with_aria_state(self):
        html = self.client.get(reverse("admin:index")).content.decode()
        self.assertIn('id="sidebar-collapse"', html)
        self.assertIn('aria-expanded="true"', html)
        self.assertIn("aria-controls=\"studio-sidebar\"", html)

    # ---------------------------------------------------------------- search
    def test_search_requires_a_signed_in_staff_user(self):
        """The view lives inside the admin namespace, so the admin's own
        permission wrapper must protect it."""
        self.client.logout()
        response = self.client.get(reverse("admin:studio_search"), {"q": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_search_finds_a_row_and_links_to_its_change_form(self):
        from content.models import Page

        page = Page.objects.create(title="Distinctive Title", path="/x/",
                                   slug="x", is_published=True)
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "Distinctive"})
        self.assertEqual(response.status_code, 200)
        results = [r for g in response.context["search_groups"]
                   for r in g["results"]]
        self.assertEqual(
            [r["url"] for r in results],
            [reverse("admin:content_page_change", args=[page.pk])],
        )
        # PageAdmin's own display method supplies the label, so a result reads
        # the way the changelist column does.
        self.assertIn("Distinctive Title", results[0]["label"])

    def test_search_spans_content_and_crm(self):
        from content.models import Page
        from crm.models import Lead

        Page.objects.create(title="Zephyrine", path="/z/", slug="z")
        Lead.objects.create(name="Zephyrine", email="z@example.com",
                            status="new")
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "Zephyrine"})
        models = {g["label"] for g in response.context["search_groups"]}
        self.assertEqual(models, {"Pages", "Leads"})

    def test_group_labels_reuse_the_sidebar_wording(self):
        """Search results must not introduce a second name for a model. Django
        calls them "faqs" and "lead activities"; the sidebar says "FAQs" and
        "Activity log", and those are the names that should carry through."""
        from content.models import FAQ

        FAQ.objects.create(question="Can you help?", answer="Yes.")
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "Can you"})
        self.assertIn("FAQs", {g["label"] for g in response.context["search_groups"]})

    def test_search_skips_models_with_no_declared_search_fields(self):
        """A model that never declared search_fields is not searched. Including
        it would mean guessing columns and usually matching everything."""
        from content.models import SiteSetting

        SiteSetting.objects.create(company_name="unique_setting_value_xyz")
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "unique_setting"})
        self.assertEqual(response.context["search_groups"], [])

    def test_search_respects_view_permission(self):
        """Search must not leak rows from models the user cannot open."""
        from content.models import Page

        Page.objects.create(title="Restrictedneedle", path="/r/", slug="r")
        limited = User.objects.create_user(
            username="leadonly", password="pw-leadonly-123", is_staff=True
        )
        limited.user_permissions.add(
            Permission.objects.get(codename="view_lead", content_type__app_label="crm")
        )
        self.client.force_login(limited)
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "Restrictedneedle"})
        self.assertEqual(response.context["search_groups"], [])

    def test_empty_search_lists_the_models_it_covers(self):
        response = self.client.get(reverse("admin:studio_search"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["searchable_models"])
        self.assertContains(response, "Type to search")

    def test_search_caps_rows_per_model(self):
        from content.models import Page

        for i in range(8):
            Page.objects.create(title=f"Capped {i}", path=f"/c{i}/",
                                slug=f"c{i}")
        response = self.client.get(reverse("admin:studio_search"),
                                   {"q": "Capped"})
        for group in response.context["search_groups"]:
            with self.subTest(model=group["label"]):
                self.assertLessEqual(len(group["results"]),
                                     StudioAdminSite.SEARCH_ROWS)

    def test_search_survives_a_model_whose_query_is_broken(self):
        """One bad model must not take the whole page down; the fan-out
        degrades to the models that do work."""
        from django.contrib.admin import ModelAdmin

        from content.models import Page

        Page.objects.create(title="Stillfound", path="/s/", slug="s")

        original = ModelAdmin.get_search_results
        calls = {"n": 0}

        def flaky(self, request, queryset, search_term):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("simulated bad search_fields")
            return original(self, request, queryset, search_term)

        with mock.patch.object(ModelAdmin, "get_search_results", flaky):
            response = self.client.get(reverse("admin:studio_search"),
                                       {"q": "Stillfound"})
        self.assertEqual(response.status_code, 200)
        results = [r for g in response.context["search_groups"] for r in g["results"]]
        self.assertTrue(results)


class IconRegistryTests(TestCase):
    """The icon registry is only useful if a missing name is loud.

    `icon()` falls back to a dot for an unknown name, which is a reasonable
    safety net at runtime and a terrible way to ship: a typo in a template
    would render a placeholder dot with nothing in the logs. These tests turn
    that into a failure.
    """

    def _templates(self):
        root = Path(__file__).resolve().parents[2] / "frontend" / "templates" / "admin"
        return sorted(root.glob("*.html"))

    def test_every_icon_name_used_in_a_template_exists(self):
        from main.icons import PATHS

        pattern = re.compile(r"{%\s*studio_icon\s+\"([a-z0-9-]+)\"")
        used = set()
        for template in self._templates():
            used.update(pattern.findall(template.read_text()))

        self.assertTrue(used, "no studio_icon calls found; check the pattern")
        missing = sorted(used - set(PATHS))
        self.assertEqual(missing, [], f"unknown icon names: {missing}")

    def test_icon_names_in_the_nav_declaration_all_exist(self):
        """The nav is data in Python, not markup, so the template scan cannot
        see it. Dotted keys are skipped: they are per-row overrides."""
        from main.admin_site import StudioAdminSite

        from main.icons import PATHS

        site = StudioAdminSite(name="probe")
        nav = site._nav({})
        names = {item["icon"]
                 for section in nav
                 for item in section["items"]}
        missing = sorted(names - set(PATHS))
        self.assertEqual(missing, [], f"unknown icon names: {missing}")

    def test_icon_output_is_well_formed_svg(self):
        """The SVG must parse. A truncated attribute silently breaks the icon
        and still returns HTTP 200, so only a real parse catches it."""
        from xml.etree import ElementTree

        from main.icons import PATHS, icon

        for name in PATHS:
            with self.subTest(icon=name):
                root = ElementTree.fromstring(icon(name))
                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(root.get("aria-hidden"), "true")
                self.assertEqual(root.get("viewBox"), "0 0 24 24")

    def test_icon_carries_an_accessible_name(self):
        """Icons are aria-hidden, so anything wrapping them must carry a label.
        This pins that the attribute is actually emitted."""
        from main.icons import icon

        self.assertIn('aria-hidden="true"', icon("search"))
        self.assertIn('class="custom"', icon("search", "custom"))

    def test_no_icon_is_defined_twice(self):
        """A repeated dict key does not raise, it silently shadows the earlier
        value, so the registry can lose an icon without any error. Parse the
        source, because by the time Python has the dict the duplicate is gone."""
        import ast

        from main import icons

        source = Path(icons.__file__).read_text(encoding="utf-8")
        duplicates = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Dict):
                continue
            keys = [key.value for key in node.keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)]
            duplicates.extend(name for name in set(keys) if keys.count(name) > 1)
        self.assertEqual(duplicates, [], f"icons defined more than once: {duplicates}")


class AdminTemplateHygieneTests(TestCase):
    """No template syntax may reach the browser.

    Django's lexer matches `{# ... #}` without `re.DOTALL`, so a comment that
    spans more than one line is never tokenised and is emitted as literal text
    on the page. The view still returns 200, so this failure survives a
    status-code check and a passing test suite, and has to be pinned directly.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="hygiene", email="hygiene@example.com", password="pw-hygiene-123"
        )
        self.client.force_login(self.user)

    def _templates(self):
        root = Path(__file__).resolve().parents[2] / "frontend" / "templates" / "admin"
        return sorted(root.glob("*.html"))

    def test_no_admin_template_uses_a_multi_line_hash_comment(self):
        """The source-level cause. `{% comment %}` is the multi-line form."""
        offenders = []
        for template in self._templates():
            lines = template.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                if line.count("{#") and line.count("{#") != line.count("#}"):
                    offenders.append(f"{template.name}:{number}")
        self.assertEqual(offenders, [], f"multi-line {{# #}} comments: {offenders}")

    def test_rendered_admin_pages_contain_no_template_syntax(self):
        """The behaviour that matters, on the pages a user actually opens."""
        for url in ("/admin/", "/admin/content/page/", "/admin/crm/lead/",
                    "/admin/crm/contact/", "/admin/content/project/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                for token in ("{#", "{%", "endcomment"):
                    self.assertNotIn(token, html, f"{token!r} leaked from {url}")


class ChangelistFurnitureTests(TestCase):
    """Phase 2: the shared changelist skin.

    The mixin adds a thumbnail column, per-row action icons, a result count with
    removable filter chips, and a totals row. These tests pin the behaviour that
    makes each worth having -- in particular the three places it could lie to
    the reader: an action the user cannot take, a total that does not match the
    rows on screen, and a "view on site" link for something unpublished.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="lists", email="lists@example.com", password="pw-lists-123"
        )
        self.client.force_login(self.user)

    # ------------------------------------------------------- adoption coverage
    def test_every_project_model_admin_is_skinned(self):
        """Adopting the list view must not be per-model optional by accident.
        A ModelAdmin that silently misses the mixin would be the one screen in
        the admin that looks different from all the others."""
        from django.contrib import admin

        from main.listview import StudioListMixin

        for model, model_admin in admin.site._registry.items():
            app_label = model._meta.app_label
            if app_label not in ("content", "crm"):
                continue
            with self.subTest(model=model._meta.label_lower):
                self.assertIsInstance(model_admin, StudioListMixin)
                self.assertEqual(
                    model_admin.change_list_template,
                    "admin/studio_changelist.html",
                )

    def test_actions_column_is_added_exactly_once(self):
        """The mixin appends the column; a ModelAdmin that also lists it must
        not produce a duplicated header."""
        from django.contrib import admin

        from crm.models import Lead

        lead_admin = admin.site._registry[Lead]
        columns = lead_admin.get_list_display(_fake_request(self.user))
        self.assertEqual(columns.count("studio_row_actions"), 1)
        self.assertEqual(columns[-1], "studio_row_actions")

    def test_thumbnail_column_is_not_duplicated(self):
        """ProjectAdmin already lists project_thumb. The mixin must leave an
        admin's own column list alone rather than adding a second copy."""
        from django.contrib import admin

        from content.models import Project

        project_admin = admin.site._registry[Project]
        # The attribute is normally set on the class; set it on this instance and
        # restore it so the change cannot leak into another test.
        original = project_admin.studio_thumb
        project_admin.studio_thumb = "project_thumb"
        try:
            columns = project_admin.get_list_display(_fake_request(self.user))
        finally:
            project_admin.studio_thumb = original
        self.assertEqual(columns.count("project_thumb"), 1)

    # ------------------------------------------------------------ row actions
    def test_row_actions_offer_edit_and_delete(self):
        from crm.models import Lead

        lead = Lead.objects.create(name="Row", email="row@example.com",
                                   status="new")
        html = self.client.get(reverse("admin:crm_lead_changelist")).content.decode()
        self.assertIn(reverse("admin:crm_lead_change", args=[lead.pk]), html)
        self.assertIn(reverse("admin:crm_lead_delete", args=[lead.pk]), html)

    def test_row_actions_are_hidden_from_a_user_who_cannot_use_them(self):
        """A viewer may not add, change or delete. Offering the icons anyway
        produces links that 403 on submit, so the column must be empty."""
        from crm.models import Lead

        lead = Lead.objects.create(name="Locked", email="locked@example.com",
                                   status="new")
        viewer = User.objects.create_user(
            username="rowviewer", password="pw-rowview-123", is_staff=True
        )
        viewer.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="crm", codename__startswith="view_"
        ))
        self.client.force_login(viewer)
        html = self.client.get(reverse("admin:crm_lead_changelist")).content.decode()

        # Scoped to the actions cell on purpose. Django's own row link points at
        # the change URL for a viewer too -- that is the read-only view, and it
        # is correct. What must not appear is an *action* link, so the check
        # reads the cell the mixin is responsible for.
        cells = _row_action_cells(html)
        self.assertEqual(len(cells), 1)
        self.assertIn("is-empty", cells[0])
        self.assertNotIn(reverse("admin:crm_lead_change", args=[lead.pk]), cells[0])
        self.assertNotIn(reverse("admin:crm_lead_delete", args=[lead.pk]), cells[0])
        # Nothing else on the page offers a delete either.
        self.assertNotIn(reverse("admin:crm_lead_delete", args=[lead.pk]), html)

    def test_draft_pages_get_no_view_on_site_action(self):
        """An unpublished page has no public URL, so the link would 404 and
        imply the edit is already live."""
        from content.models import Page

        draft = Page.objects.create(title="Hidden Draft", path="/hidden/",
                                    slug="hidden", is_published=False)
        live = Page.objects.create(title="Visible", path="/visible/",
                                   slug="visible", is_published=True)
        html = self.client.get(reverse("admin:content_page_changelist")).content.decode()
        # Both rows are listed; only the published one offers the public link.
        self.assertIn(reverse("admin:content_page_change", args=[draft.pk]), html)
        self.assertIn('href="/visible/"', html)
        self.assertNotIn('href="/hidden/"', html)
        self.assertIn(reverse("admin:content_page_change", args=[live.pk]), html)

    # --------------------------------------------------------------- counting
    def test_count_says_filtered_of_total(self):
        from content.models import Page

        for i in range(5):
            Page.objects.create(title=f"P{i}", path=f"/p{i}/", slug=f"p{i}",
                                is_published=bool(i % 2))
        response = self.client.get(
            reverse("admin:content_page_changelist") + "?is_published__exact=1"
        )
        self.assertEqual(response.context["studio_shown"], 2)
        self.assertEqual(response.context["studio_all"], 5)

    def test_unfiltered_list_reports_a_single_number(self):
        from content.models import Page

        Page.objects.create(title="One", path="/one/", slug="one")
        response = self.client.get(reverse("admin:content_page_changelist"))
        self.assertEqual(response.context["studio_shown"], 1)
        self.assertEqual(response.context["studio_all"], 1)

    # ---------------------------------------------------------- filter chips
    def test_applied_filter_becomes_a_named_chip(self):
        """A chip is read by a person, so it says "Is published: Yes" rather
        than echoing `is_published__exact=1` back at them."""
        from content.models import Page

        Page.objects.create(title="Pub", path="/pub/", slug="pub",
                            is_published=True)
        response = self.client.get(
            reverse("admin:content_page_changelist") + "?is_published__exact=1"
        )
        chips = response.context["studio_filters"]
        self.assertEqual([chip["label"] for chip in chips],
                         ["Is published: Yes"])

    def test_no_filters_means_no_chips(self):
        response = self.client.get(reverse("admin:content_page_changelist"))
        self.assertEqual(response.context["studio_filters"], [])

    def test_a_chip_drops_only_its_own_filter(self):
        """Two filters applied, one chip clicked: the other must survive."""
        from content.models import Page

        Page.objects.create(title="Both", path="/both/", slug="both",
                            is_published=True, show_in_menu=True)
        Page.objects.create(title="OnlyPublished", path="/op/", slug="op",
                            is_published=True, show_in_menu=False)
        url = (reverse("admin:content_page_changelist")
               + "?is_published__exact=1&show_in_menu__exact=1")
        response = self.client.get(url)
        chips = response.context["studio_filters"]
        self.assertEqual(sorted(chip["label"] for chip in chips),
                         ["Is published: Yes", "Show in menu: Yes"])

        # Each chip's URL drops exactly the parameter it is named for and keeps
        # the other one, so the two chips are genuinely independent.
        for chip in chips:
            dropped = ("is_published__exact"
                       if "published" in chip["label"].lower()
                       else "show_in_menu__exact")
            kept = ("show_in_menu__exact" if dropped == "is_published__exact"
                    else "is_published__exact")
            self.assertNotIn(dropped, chip["remove_url"])
            self.assertIn(kept, chip["remove_url"])

    def test_search_term_is_not_reported_as_a_filter_chip(self):
        """A search box is a different control from a filter; showing it as a
        removable chip next to real filters conflates the two."""
        from content.models import Page

        Page.objects.create(title="Needle", path="/n/", slug="n")
        response = self.client.get(
            reverse("admin:content_page_changelist") + "?q=Needle"
        )
        self.assertEqual(response.context["studio_filters"], [])

    # ----------------------------------------------------------------- totals
    def test_totals_sum_the_filtered_rows_only(self):
        """A totals row that sums the whole table while the view shows a subset
        is the classic way a list misleads."""
        from crm.models import Lead

        for name, score in (("a", 10), ("b", 20), ("c", 30)):
            Lead.objects.create(name=name, email=f"{name}@example.com",
                                status="new", score=score)
        Lead.objects.create(name="won", email="won@example.com",
                            status="won", score=100)

        unfiltered = self.client.get(reverse("admin:crm_lead_changelist"))
        self.assertEqual(unfiltered.context["studio_totals"]["Score"], 160)

        filtered = self.client.get(
            reverse("admin:crm_lead_changelist") + "?status__exact=new"
        )
        self.assertEqual(filtered.context["studio_totals"]["Score"], 60)

    def test_models_without_totals_declare_none(self):
        """The totals strip must be absent, not empty, where nothing is summed."""
        response = self.client.get(reverse("admin:content_page_changelist"))
        self.assertEqual(response.context["studio_totals"], {})
        self.assertNotContains(response, "studio-list-totals")

    def test_lead_changelist_renders_the_totals_strip(self):
        self.client.get(reverse("admin:crm_lead_changelist"))
        self.assertContains(self.client.get(
            reverse("admin:crm_lead_changelist")), "studio-list-totals")


def _row_action_cells(html):
    """The inner HTML of every per-row actions cell on a changelist."""
    return re.findall(
        r'<td class="field-studio_row_actions">(.*?)</td>', html, re.S
    )


def _fake_request(user):
    from django.test import RequestFactory

    request = RequestFactory().get("/admin/")
    request.user = user
    return request


class MediaPickerTests(TestCase):
    """The picker must decorate the path fields, never replace them.

    `Page.og_image`, `Project.image` and `TrustBadge.image` are CharFields
    holding paths, and `content.models.MediaItem` documents why. So the bar for
    this feature is not "can pick an image" but "can pick an image without any
    existing row or hand-typed value becoming invalid".
    """

    def setUp(self):
        self.media_dir = tempfile.mkdtemp(prefix="studio-picker-tests-")
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        self.owner = User.objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(self.owner)
        # Every test that touches image.url needs the field patched to a real
        # directory, since MEDIA_ROOT points nowhere useful under the test
        # runner. Applied here rather than decorated so it cannot be forgotten
        # on one test and half the class runs against a missing path.
        patcher = override_settings(MEDIA_ROOT=self.media_dir)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def make_media(self, name, **kwargs):
        kwargs.setdefault("is_published", True)
        return MediaItem.objects.create(
            title=kwargs.pop("title", name.rsplit(".", 1)[0].replace("-", " ").title()),
            alt_text=kwargs.pop("alt_text", ""),
            image=SimpleUploadedFile(name, png_bytes(), content_type="image/png"),
            **kwargs,
        )

    def make_page(self, slug, og_image=""):
        # `path` is unique and non-blank, and Page.__str__ includes it, so a
        # page built without one is both ambiguous in output and a constraint
        # violation waiting to happen across subTests.
        return Page.objects.create(slug=slug, title=slug.replace("-", " ").title(),
                                   path=f"/{slug}/", og_image=og_image)

    def add_form(self, model, url_name):
        response = self.client.get(reverse(f"admin:content_{url_name}_add"))
        self.assertEqual(response.status_code, 200)
        return response.context["adminform"].form

    def picker_response(self):
        return self.client.get(reverse("admin:media_picker"))

    def picker_json(self):
        response = self.picker_response()
        self.assertEqual(response.status_code, 200)
        return response.json()

    # --- the widget is on the right fields, and only those --------------
    def test_picker_lands_on_exactly_the_three_image_fields(self):
        for model, url_name, field in (("Page", "page", "og_image"),
                                       ("Project", "project", "image"),
                                       ("TrustBadge", "trustbadge", "image")):
            with self.subTest(model=model):
                widget = self.add_form(model, url_name).fields[field].widget
                self.assertIsInstance(widget, MediaPathWidget)

    def test_plain_text_fields_do_not_get_a_picker(self):
        """The mixin names fields, so a CharField override cannot leak.

        `formfield_overrides` is keyed by field type; reaching for it would put
        a picker on every slug, title and SEO field on the form.
        """
        for model, field in (("Page", "title"), ("Page", "slug"),
                             ("Page", "seo_title"), ("Project", "title"),
                             ("TrustBadge", "label")):
            with self.subTest(model=model, field=field):
                widget = self.add_form(model, model.lower()).fields[field].widget
                self.assertNotIsInstance(widget, MediaPathWidget)

    def test_widget_keeps_a_working_text_input(self):
        """The escape hatch has to survive: still text, still bound."""
        widget = self.add_form("Page", "page").fields["og_image"].widget
        rendered = widget.render("og_image", "/static/img/x.png")
        self.assertIn('type="text"', rendered)
        self.assertIn("/static/img/x.png", rendered)
        self.assertIn("vTextField", rendered)
        self.assertIn("data-picker-open", rendered)

    def test_widget_ships_its_own_script(self):
        self.assertIn("admin/js/media_picker.js",
                      [str(path) for path in MediaPathWidget().media._js])

    def test_widget_points_at_the_picker_endpoint(self):
        widget = self.add_form("Page", "page").fields["og_image"].widget
        self.assertIn(reverse("admin:media_picker"),
                      widget.render("og_image", ""))

    # --- the endpoint ---------------------------------------------------
    def test_endpoint_returns_published_media_newest_first(self):
        older = self.make_media("older.png")
        newer = self.make_media("newer.png")
        paths = [item["path"] for item in self.picker_json()["images"]]
        self.assertIn(newer.public_path, paths)
        self.assertIn(older.public_path, paths)
        self.assertLess(paths.index(newer.public_path), paths.index(older.public_path))

    def test_endpoint_hides_unpublished_media(self):
        """Retiring an image must not break a page already storing its path."""
        draft = self.make_media("draft.png", is_published=False)
        self.assertNotIn(draft.public_path,
                         [i["path"] for i in self.picker_json()["images"]])

    def test_endpoint_payload_carries_only_what_the_grid_needs(self):
        item = self.make_media("grid.png")
        entry = self.picker_json()["images"][0]
        self.assertEqual(set(entry), {"id", "title", "path", "alt", "thumb", "size"})
        self.assertEqual(entry["path"], item.public_path)
        self.assertTrue(entry["thumb"].startswith("/media/"))

    def test_endpoint_truncates_rather_than_growing_without_bound(self):
        with mock.patch.object(StudioAdminSite, "MEDIA_PICKER_LIMIT", 2):
            for i in range(3):
                self.make_media(f"cap-{i}.png")
            payload = self.picker_json()
            self.assertEqual(len(payload["images"]), 2)
            self.assertTrue(payload["truncated"])

    def test_endpoint_skips_an_item_whose_file_is_gone(self):
        """A row emptied server-side must not 500 the grid, and must not be
        offered at all -- picking it would write an empty path."""
        MediaItem.objects.create(title="no file", image="")
        self.assertEqual(self.picker_json()["images"], [])

    # --- permissions ---------------------------------------------------
    def test_endpoint_requires_login(self):
        self.client.logout()
        self.assertEqual(self.picker_response().status_code, 302)

    def test_endpoint_denies_staff_without_view_permission(self):
        staff = User.objects.create_user("staffer", "s@example.com", "pw",
                                          is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.picker_response().status_code, 403)

    def test_endpoint_allows_view_only_staff(self):
        """View alone must be enough, or the picker is shut to exactly the
        editors with the most fields to fill in. Writing a path is guarded by
        the change permission on the field's own form, not here."""
        staff = User.objects.create_user("viewer", "v@example.com", "pw", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(
            codename="view_mediaitem", content_type__app_label="content"))
        self.client.force_login(staff)
        self.assertEqual(self.picker_response().status_code, 200)

    # --- reference visibility ------------------------------------------
    def test_references_finds_pages_projects_and_badges(self):
        item = self.make_media("shared.png")
        self.make_page("landing", item.public_path)
        Project.objects.create(title="Kitchen", image=item.public_path)
        TrustBadge.objects.create(label="Warranty", image=item.public_path)
        self.make_page("unrelated")

        found = item.references()
        self.assertEqual(len(found), 3)
        self.assertEqual({row["model"] for row in found},
                         {"Page", "Project", "TrustBadge"})
        for row in found:
            self.assertTrue(row["url"].startswith("/admin/"))

    def test_references_also_match_the_bare_storage_name(self):
        """A hand-typed `uploads/...` is as real as a served URL.

        The field is text, so both spellings occur in the data and the lookup
        has to try both or it will under-report and hide a live reference.
        """
        item = self.make_media("byname.png")
        self.make_page("hand-typed", item.image.name)
        self.assertEqual(len(item.references()), 1)

    def test_references_are_empty_for_an_unused_image(self):
        self.assertEqual(self.make_media("unused.png").references(), [])

    def test_references_tolerate_an_item_with_no_file(self):
        self.assertEqual(MediaItem(title="blank", image="").references(), [])

    def test_change_form_lists_what_uses_the_image(self):
        item = self.make_media("reported.png")
        url = reverse("admin:content_mediaitem_change", args=[item.pk])
        self.assertIn("Not referenced yet", self.client.get(url).content.decode())

        page = self.make_page("uses-it", item.public_path)
        html = self.client.get(url).content.decode()
        self.assertIn("Used by", html)
        self.assertIn(page.title, html)
        # The link is the point: a reference you cannot click is a claim you
        # have to go and verify by hand.
        self.assertIn(
            reverse("admin:content_page_change", args=[page.pk]), html)
        # Real list items, not a run-together paragraph. format_html on a bare
        # join produces a valid-looking <ul> with no <li> in it, which renders
        # as one solid block of text.
        self.assertIn('<ul class="used-by-list"><li>', html)

    # --- the static half -------------------------------------------------
    def test_picker_script_is_served_from_the_project_static_dir(self):
        """The widget's Media declares a path; the file has to be there.

        A missing file is not a render error -- the browser just 404s it
        silently and the field looks like a plain text input -- so it is
        asserted here rather than left to a manual click.
        """
        from django.contrib.staticfiles import finders

        for js in MediaPathWidget().media._js:
            self.assertTrue(finders.find(str(js)), f"{js} is not on the static path")

    def test_picker_styles_are_shipped(self):
        from django.contrib.staticfiles import finders

        self.assertIsNotNone(finders.find("admin/css/admin.css"))
        css = finders.find("admin/css/admin.css")
        with open(css, encoding="utf-8") as handle:
            body = handle.read()
        for selector in (".media-path", ".media-picker-modal", ".media-picker-grid"):
            self.assertIn(selector, body, f"{selector} has no styles")
        self.assertEqual(body.count("{"), body.count("}"), "unbalanced CSS braces")
