"""Tests for the custom dashboard admin site.

The dashboard replaced Django's model index, so its failure mode is new: any
query that raises now 500s the landing page for every staff user, and the tab
counts run on *every* admin screen, not just the dashboard. These tests pin
both behaviours, plus the media library's file validation.
"""
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from content.models import MediaItem
from main.admin_site import StudioAdminSite

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
        self.assertEqual(len(stats), 5)
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
        self.assertEqual(icon_count, 5)
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

    def test_dashboard_shows_tab_badges_when_work_exists(self):
        from crm.models import Lead, Task

        lead = Lead.objects.create(name="Nia", email="nia@example.com")
        Task.objects.create(lead=lead, title="Ring back")
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "tab-count")

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
