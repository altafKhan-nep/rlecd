"""Tests for the database-backed frontend.

The important ones are the round-trip tests: they prove that an edit made in
the CRM actually changes the public page, which is the whole point of moving
content into the database. The `test_import_is_lossless` test is the guard on
the mirror: if a future import stops reproducing the markup byte for byte, it
fails here rather than silently drifting from the live site.
"""
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from content import importer
from content.models import (
    FAQ, Page, Project, Section, ServiceArea, SiteSetting, Testimonial, TrustBadge,
)
from content.render import ContentRenderError, clear_template_cache


def strip_tags(html):
    return re.sub(r"<[^>]+>", " ", html or "")


class ChunkingTests(TestCase):
    """The splitter must be lossless or the mirror cannot survive import."""

    def test_chunks_rejoin_byte_identically(self):
        for name, (path, _title) in importer.TEMPLATE_SPECS.items():
            source = str(settings.REPO_ROOT / "frontend" / "source_templates" / "main" / f"{name}.html")
            try:
                with open(source, encoding="utf-8") as fh:
                    src = fh.read()
            except FileNotFoundError:
                continue
            body = importer.parse_blocks(src).get("content", "").strip()
            chunks = importer.chunk_lossless(body)
            with self.subTest(page=path):
                self.assertEqual("".join(chunks), body)

    def test_chunker_handles_empty_and_text_only(self):
        self.assertEqual(importer.chunk_lossless(""), [])
        self.assertEqual(importer.chunk_lossless("   \n "), ["   \n "])


class BlockParsingTests(TestCase):
    def test_inherits_base_defaults_when_block_absent(self):
        base = '{% block nav %}{% include "partials/_nav_1.html" %}{% endblock %}'
        page = '{% extends "base.html" %}{% block content %}x{% endblock %}'
        spec = importer.capture_page_spec(
            page, importer.parse_blocks(base), "/x/", "X")
        self.assertEqual(spec["nav_variant"], "partials/_nav_1.html")
        self.assertEqual(spec["body"], "x")

    def test_page_block_overrides_base(self):
        base = '{% block nav %}{% include "partials/_nav_1.html" %}{% endblock %}'
        page = ('{% block nav %}{% include "partials/_nav_4.html" %}{% endblock %}'
                '{% block content %}y{% endblock %}')
        spec = importer.capture_page_spec(
            page, importer.parse_blocks(base), "/x/", "X")
        self.assertEqual(spec["nav_variant"], "partials/_nav_4.html")

    def test_main_attrs_keeps_leading_space(self):
        # base.html renders "<main{% block main_attrs %}>", so the stored value
        # must carry its own leading space or the tag closes up as
        # "<mainclass=...>".
        page = '{% block main_attrs %} class="contact-page"{% endblock %}'
        spec = importer.capture_page_spec(page, {}, "/c/", "C")
        self.assertEqual(spec["main_attrs"], ' class="contact-page"')
        self.assertEqual('<main%s>' % spec["main_attrs"],
                         '<main class="contact-page">')

    def test_empty_main_attrs_renders_bare_main(self):
        spec = importer.capture_page_spec("", {}, "/c/", "C")
        self.assertEqual(spec["main_attrs"], "")
        self.assertEqual("<main%s>" % spec["main_attrs"], "<main>")


@override_settings(ROOT_URLCONF="content.urls_test")
class PageRenderTests(TestCase):
    """The pages must render from the database, not from hard-coded templates."""

    @classmethod
    def setUpTestData(cls):
        cls.page = Page.objects.create(
            slug="test", title="Test", path="/test/",
            nav_variant="partials/_nav_1.html",
            footer_variant="partials/_foot_1.html",
            post_variant="partials/_post_1.html",
            head_html="<title>{% if request %}{{ request.path }}{% endif %}</title>",
        )
        cls.a = Section.objects.create(
            page=cls.page, key="a", label="Alpha", position=0,
            content_html="<p>ALPHA {{ SITE_URL }}</p>")
        cls.b = Section.objects.create(
            page=cls.page, key="b", label="Beta", position=1,
            content_html="<p>BETA</p>")

    def test_sections_render_in_position_order(self):
        html = self.client.get("/test/").content.decode()
        self.assertLess(html.index("ALPHA"), html.index("BETA"))

    def test_static_tag_resolves_in_stored_section(self):
        Section.objects.filter(pk=self.a.pk).update(
            content_html="<img src=\"{% static 'img/rlecd_maryland_logo.png' %}\">")
        clear_template_cache()
        html = self.client.get("/test/").content.decode()
        self.assertIn("/static/img/rlecd_maryland_logo.png", html)

    def test_hiding_a_section_removes_it_from_output(self):
        self.assertIn("BETA", self.client.get("/test/").content.decode())
        self.b.is_visible = False
        self.b.save()
        clear_template_cache()
        self.assertNotIn("BETA", self.client.get("/test/").content.decode())

    def test_deleting_a_section_removes_it_from_output(self):
        self.b.delete()
        clear_template_cache()
        html = self.client.get("/test/").content.decode()
        self.assertIn("ALPHA", html)
        self.assertNotIn("BETA", html)

    def test_editing_a_section_updates_the_page(self):
        self.a.content_html = "<p>REPLACED COPY</p>"
        self.a.save()
        clear_template_cache()
        html = self.client.get("/test/").content.decode()
        self.assertIn("REPLACED COPY", html)
        self.assertNotIn("ALPHA", html)

    def test_broken_section_raises_instead_of_blank_page(self):
        self.a.content_html = "{% if %}"
        self.a.save()
        clear_template_cache()
        with self.assertRaises(ContentRenderError):
            self.client.get("/test/")

    def test_unpublished_page_falls_back_to_legacy_template(self):
        # 404 would be worse than serving the static mirror.
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)

    def test_main_attrs_render_unescaped(self):
        self.page.main_attrs = ' class="x-page"'
        self.page.save()
        clear_template_cache()
        html = self.client.get("/test/").content.decode()
        self.assertIn('<main class="x-page">', html)
        self.assertNotIn("&quot;x-page&quot;", html)


class AdminCrudTests(TestCase):
    """Full create/read/update/delete through the CRM admin, for content."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            "editor", "e@example.com", "pw")
        cls.page = Page.objects.create(
            slug="p", title="P", path="/p/", nav_variant="partials/_nav_1.html",
            footer_variant="partials/_foot_1.html",
            post_variant="partials/_post_1.html")
        cls.section = Section.objects.create(
            page=cls.page, key="s1", label="S1", position=0,
            content_html="<p>ORIGINAL</p>")

    def setUp(self):
        # client is not available on the class in setUpTestData.
        self.client.force_login(self.admin)

    def test_changelists_all_load(self):
        for model in (Page, Section, ServiceArea, FAQ, Testimonial,
                      Project, TrustBadge, SiteSetting):
            url = reverse(f"admin:content_{model._meta.model_name}_changelist")
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_section_via_admin(self):
        url = reverse("admin:content_section_add")
        self.client.post(url, {
            "page": self.page.pk, "key": "new", "position": 5,
            "label": "Brand new", "type": "text", "content_html": "<p>NEW</p>",
        })
        self.assertTrue(Section.objects.filter(label="Brand new").exists())

    def test_update_section_via_admin(self):
        url = reverse("admin:content_section_change", args=[self.section.pk])
        self.client.post(url, {
            "page": self.page.pk, "key": self.section.key, "position": 0,
            "label": "Edited", "type": "text", "content_html": "<p>UPDATED</p>",
        })
        self.section.refresh_from_db()
        self.assertEqual(self.section.content_html, "<p>UPDATED</p>")

    def test_delete_section_via_admin(self):
        url = reverse("admin:content_section_delete", args=[self.section.pk])
        self.client.post(url, {"post": "yes"})
        self.assertFalse(Section.objects.filter(pk=self.section.pk).exists())

    def test_create_page_and_service_area_via_admin(self):
        # PageAdmin has a SectionInline, so the POST must carry the inline
        # management form data a browser would submit.
        self.client.post(reverse("admin:content_page_add"), {
            "title": "New page", "slug": "new-page", "path": "/new-page/",
            "is_published": "on", "show_in_menu": "on", "sort_order": 0,
            "nav_variant": "partials/_nav_1.html",
            "footer_variant": "partials/_foot_1.html",
            "post_variant": "partials/_post_1.html",
            "head_html": "<title>New</title>", "main_attrs": "",
            "sections-TOTAL_FORMS": "0",
            "sections-INITIAL_FORMS": "0",
            "sections-MIN_NUM_FORMS": "0",
            "sections-MAX_NUM_FORMS": "1000",
        })
        self.assertTrue(Page.objects.filter(slug="new-page").exists())

        self.client.post(reverse("admin:content_servicearea_add"),
                         {"name": "Owings Mills", "is_active": "on",
                          "sort_order": 0, "blurb": "", "group": ""})
        area = ServiceArea.objects.get(name="Owings Mills")
        self.assertEqual(area.slug, "owings-mills")

    def test_page_admin_rejects_post_without_inline_management_data(self):
        """Documents why the formset data above is required."""
        response = self.client.post(reverse("admin:content_page_add"), {
            "title": "No inline", "slug": "no-inline", "path": "/no-inline/",
            "sort_order": 0,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Page.objects.filter(slug="no-inline").exists())

    def test_bulk_show_and_hide_actions(self):
        url = reverse("admin:content_section_changelist")
        self.client.post(url, {"action": "make_hidden",
                               "_selected_action": [str(self.section.pk)]})
        self.section.refresh_from_db()
        self.assertFalse(self.section.is_visible)

        self.client.post(url, {"action": "make_visible",
                               "_selected_action": [str(self.section.pk)]})
        self.section.refresh_from_db()
        self.assertTrue(self.section.is_visible)

    def test_sectionsetting_is_a_singleton(self):
        first = SiteSetting.load()
        second = SiteSetting.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SiteSetting.objects.count(), 1)


class MirrorRegressionTests(TestCase):
    """Guard the public site: every route must still render from the database.

    The test database is populated by running the real importer, so these tests
    exercise the same Page/Section rows the running site serves.
    """

    ROUTES = [
        "/", "/about/", "/contact/", "/areas-we-serve/",
        "/kitchen-remodeling/", "/bathroom-remodeling/", "/basement-finishing/",
        "/home-additions/", "/painting/", "/home-improvement/",
        "/patios-decks/", "/cabinets/", "/woodworking/", "/hardscaping/",
        "/walkway-designs/", "/pergolas/", "/lead-removal/", "/shed-builder/",
        "/lead-renovator/",
    ]

    @classmethod
    def setUpTestData(cls):
        importer.import_pages(settings.REPO_ROOT, reset=True)
        clear_template_cache()

    def test_imported_sections_reproduce_the_captured_body(self):
        """The strongest mirror guarantee available.

        For every page, joining its stored sections must equal the body of the
        captured template exactly. If this drifts, the running site can no longer
        be compared against the live markup.
        """
        specs = {s["slug"]: s for s in importer.collect_specs(settings.REPO_ROOT)}
        for slug, spec in specs.items():
            with self.subTest(page=spec["path"]):
                page = Page.objects.get(slug=slug)
                joined = "".join(
                    s.content_html for s in page.sections.order_by("position"))
                self.assertEqual(joined, spec["body"])

    def test_page_shell_fields_match_the_captured_template(self):
        specs = {s["slug"]: s for s in importer.collect_specs(settings.REPO_ROOT)}
        for slug, spec in specs.items():
            with self.subTest(page=spec["path"]):
                page = Page.objects.get(slug=slug)
                self.assertEqual(page.main_attrs, spec["main_attrs"])
                self.assertEqual(page.nav_variant, spec["nav_variant"])
                self.assertEqual(page.footer_variant, spec["footer_variant"])
                self.assertEqual(page.post_variant, spec["post_variant"])

    def test_every_route_returns_200(self):
        for route in self.ROUTES:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_public_routes_are_database_backed(self):
        """Editing a section must change the public page.

        This is the assertion that would fail if the views silently fell back
        to the hard-coded templates: a DB edit would then have no effect.
        """
        contact = Page.objects.get(path="/contact/")
        section = contact.sections.filter(is_visible=True).first()
        self.assertIsNotNone(section, "no imported section to edit")

        original = section.content_html
        section.content_html = "<p>DB-DRIVEN-MARKER</p>"
        section.save()
        clear_template_cache()
        try:
            self.assertIn("DB-DRIVEN-MARKER",
                          self.client.get("/contact/").content.decode())
        finally:
            section.content_html = original
            section.save()
            clear_template_cache()

    def test_unpublished_page_falls_back_to_legacy_template(self):
        """A missing row degrades to the static mirror, not to a 404."""
        Page.objects.filter(path="/about/").update(is_published=False)
        clear_template_cache()
        self.assertEqual(self.client.get("/about/").status_code, 200)

    def test_homepage_social_widgets_stay_on_homepage_only(self):
        home = self.client.get("/").content.decode()
        contact = self.client.get("/contact/").content.decode()
        self.assertIn("tawk", home.lower())
        self.assertNotIn("tawk", contact.lower())
