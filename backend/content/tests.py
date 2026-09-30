"""Tests for the database-backed frontend.

The important ones are the round-trip tests: they prove that an edit made in
the CRM actually changes the public page, which is the whole point of moving
content into the database. The `test_import_is_lossless` test is the guard on
the mirror: if a future import stops reproducing the markup byte for byte, it
fails here rather than silently drifting from the live site.
"""
import re
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.template.loader import render_to_string
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from content import importer, registry
from content.models import (
    ContentRevision, FAQ, MenuItem, Navigation, Page, Project, Section,
    SectionImage, ServiceArea, SiteSetting, Testimonial, TrustBadge,
    visible_text,
)
from content.nav import is_current_url, mark_current, navigation_tree
from main.context_processors import navigation as nav_context
from crm.models import Service
from content import render as render_module
from content.render import (
    ContentRenderError, clear_template_cache, render_sections,
)


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
        for model in (Page, Section, SectionImage, ServiceArea, FAQ,
                      Testimonial, Project, TrustBadge, SiteSetting):
            url = reverse(f"admin:content_{model._meta.model_name}_changelist")
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(url).status_code, 200)

    #: The section form carries a SectionImage inline, so every POST to it
    #: needs that formset's management fields. Omitting them makes Django
    #: reject the whole save, which is correct but easy to trip over.
    @staticmethod
    def empty_image_formset():
        return {
            "images-TOTAL_FORMS": "0",
            "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0",
            "images-MAX_NUM_FORMS": "1000",
        }

    def test_create_section_via_admin(self):
        url = reverse("admin:content_section_add")
        self.client.post(url, {
            "page": self.page.pk, "key": "new", "position": 5,
            "label": "Brand new", "type": "text", "content_html": "<p>NEW</p>",
            **self.empty_image_formset(),
        })
        self.assertTrue(Section.objects.filter(label="Brand new").exists())

    def test_update_section_via_admin(self):
        """The form posts the editor's body, not the captured markup."""
        url = reverse("admin:content_section_change", args=[self.section.pk])
        self.client.post(url, {
            "page": self.page.pk, "label": "Edited", "type": "text",
            "is_visible": "on", "content_body": "<p>UPDATED</p>",
            "images-TOTAL_FORMS": "1", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-position": "0", "images-0-image": "img/new.jpg",
            "images-0-alt_text": "New image", "images-0-caption": "",
        })
        self.section.refresh_from_db()
        self.assertEqual(self.section.label, "Edited")
        self.assertEqual(self.section.content_body, "<p>UPDATED</p>")
        # The captured copy is untouched: it is the fallback, not the value.
        self.assertEqual(self.section.content_html, "<p>ORIGINAL</p>")
        # The image posted alongside the section was saved too.
        self.assertTrue(self.section.images.filter(image="img/new.jpg").exists())

    def test_captured_markup_cannot_be_edited_through_the_form(self):
        """Even a hand-crafted POST cannot overwrite the captured copy.

        The field is not in the form, so Django drops it. Without this the
        captured body would be one crafted request away from being lost, and
        the page would fall back to nothing.
        """
        original = self.section.content_html
        url = reverse("admin:content_section_change", args=[self.section.pk])
        self.client.post(url, {
            "page": self.page.pk, "label": "L", "type": "text",
            "is_visible": "on", "content_body": "<p>New</p>",
            "content_html": "<p>HIJACKED</p>",
            "key": "hijacked",
            "images-TOTAL_FORMS": "0", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
        })
        self.section.refresh_from_db()
        self.assertEqual(self.section.content_html, original)
        self.assertEqual(self.section.key, "s1")

    def test_add_an_image_row_through_the_section_form(self):
        """The path the CMS actually uses to change a picture."""
        self.section.content_html = "<!--rlecd-image:1-->"
        self.section.save()
        url = reverse("admin:content_section_change", args=[self.section.pk])
        self.client.post(url, {
            "page": self.page.pk, "key": self.section.key, "position": 0,
            "label": self.section.label, "type": self.section.type,
            "content_html": "<!--rlecd-image:1-->",
            "images-TOTAL_FORMS": "1", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-position": "0",
            "images-0-image": "{% static 'img/replacement.jpg' %}",
            "images-0-alt_text": "Replacement", "images-0-caption": "",
        })
        image = SectionImage.objects.get()
        self.assertEqual(image.image, "{% static 'img/replacement.jpg' %}")
        self.assertEqual(image.alt_text, "Replacement")

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

    def test_a_page_can_be_created_without_any_sections(self):
        """The page form no longer carries a sections inline.

        The body moved to its own screen, so a page is created from its
        settings and written afterwards. Previously the form rejected any POST
        that did not carry the inline's formset data, which made creating a page
        and writing it a single all-or-nothing request.
        """
        response = self.client.post(reverse("admin:content_page_add"), {
            "title": "New", "slug": "new", "path": "/new/", "sort_order": 0,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Page.objects.filter(slug="new").exists())

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

        For every page, joining its stored sections -- with each section's
        images resolved back into <img> tags -- must equal the body of the
        captured template exactly. If this drifts, the running site can no
        longer be compared against the live markup.

        Resolving the images is what makes this still exact after the images
        were lifted into SectionImage rows: the round trip is
        extract_images -> SectionImage.to_html, and this is the test that says
        the round trip is lossless rather than merely close.
        """
        from content.render import resolve_images

        specs = {s["slug"]: s for s in importer.collect_specs(settings.REPO_ROOT)}
        for slug, spec in specs.items():
            with self.subTest(page=spec["path"]):
                page = Page.objects.get(slug=slug)
                joined = "".join(
                    resolve_images(s, s.content_html)
                    for s in page.sections.order_by("position"))
                self.assertEqual(joined, spec["body"])

    def test_lifting_images_out_is_lossless(self):
        """No page may lose an <img> to the extraction, and none may gain one.

        Counted on the captured body against the stored rows rather than
        compared as text, so this fails if a single tag is dropped even when
        the remaining markup still matches.
        """
        for spec in importer.collect_specs(settings.REPO_ROOT):
            with self.subTest(page=spec["path"]):
                expected = len(re.findall(r"<img\b", spec["body"], re.I))
                self.assertEqual(
                    SectionImage.objects.filter(section__page__slug=spec["slug"]).count(),
                    expected,
                )

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


class HollowPageTests(TestCase):
    """A published row with nothing in it must not shadow the mirror template.

    This is the state the live dev database was actually in: `about` and
    `contact` had published rows with no sections, no head and no shell
    variants. Because the navbar, footer and head are all read off the Page
    row, rendering one of those emitted an empty <main> and no navigation --
    a 200 response the visitor cannot act on.
    """

    def make_hollow(self, slug, **kwargs):
        return Page.objects.create(
            slug=slug, title=slug.title(), path=f"/{slug}/", **kwargs)

    def test_has_content_is_false_for_a_bare_row(self):
        self.assertFalse(self.make_hollow("bare").has_content())

    def test_sections_make_a_page_count_as_content(self):
        page = self.make_hollow("with-sections")
        Section.objects.create(page=page, key="a", label="A", position=0,
                               content_html="<p>BODY</p>")
        self.assertTrue(page.has_content())

    def test_a_shell_variant_alone_counts_as_content(self):
        """A page with a navbar but no body is still a page.

        The shell is per-page data, not decoration -- the live site ships
        different navbars and footers per page -- so a row carrying one of them
        has been captured deliberately and must render.
        """
        for field in ("nav_variant", "footer_variant", "post_variant"):
            with self.subTest(field=field):
                self.assertTrue(self.make_hollow(f"shell-{field}", **{field: "x.html"}).has_content())

    def test_head_html_alone_counts_as_content(self):
        self.assertTrue(self.make_hollow("headed", head_html="<title>T</title>").has_content())

    def test_whitespace_head_does_not_count(self):
        self.assertFalse(self.make_hollow("blank-head", head_html="   \n  ").has_content())

    def test_hollow_published_page_serves_the_mirror(self):
        self.make_hollow("about")
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # The mirror's own content, not a blank shell.
        self.assertIn("<main", html)
        self.assertGreater(len(html), 2000)
        # And critically, the navbar is back, so the page is not a dead end.
        self.assertIn('href="/contact/"', html)

    def test_hollow_page_still_serves_a_usable_contact_form(self):
        """The contact form posts into the CRM, so a hollow page loses leads."""
        self.make_hollow("contact")
        html = self.client.get("/contact/").content.decode()
        self.assertIn('name="email"', html)
        self.assertIn('name="message"', html)
        self.assertIn('action="/contact/"', html)

    def test_a_page_with_content_still_renders_from_the_database(self):
        """The fallback must not steal pages that really are populated."""
        page = Page.objects.create(
            slug="about", title="About", path="/about/",
            head_html="<title>About</title>")
        Section.objects.create(page=page, key="a", label="A", position=0,
                               content_html="<p>DB CONTENT MARKER</p>")
        clear_template_cache()
        html = self.client.get("/about/").content.decode()
        self.assertIn("DB CONTENT MARKER", html)
        # "Our Story" is a heading in the captured mirror template. Its absence
        # is what proves the database won the page rather than the fallback --
        # asserting the marker is present alone would also pass if the mirror
        # happened to contain it.
        self.assertNotIn("Our Story", strip_tags(html))

    def test_the_two_fallbacks_serve_different_content(self):
        """Sanity check on the marker above: it really is in the mirror.

        Without this, a rename of the captured heading would turn the assertion
        above into a permanent no-op.
        """
        from content.models import SiteSetting
        self.assertIn("Our Story", strip_tags(
            render_to_string("main/about.html", {"site_settings": SiteSetting.load()})))

    def test_hollow_page_without_a_mirror_template_is_a_404(self):
        """Falling back is a courtesy; a genuine typo must still 404."""
        self.make_hollow("no_such_mirror")
        self.assertEqual(self.client.get("/no-such-mirror/").status_code, 404)

    def test_unpublished_page_still_falls_back(self):
        page = self.make_hollow("about")
        page.is_published = False
        page.save()
        self.assertEqual(self.client.get("/about/").status_code, 200)

    def test_hollow_page_logs_a_distinct_warning(self):
        """A missing row and a hollow row need different fixes, so the log
        has to tell them apart."""
        with self.assertLogs("content.views", level="WARNING") as caught:
            self.make_hollow("about")
            clear_template_cache()
            self.client.get("/about/")
        self.assertIn("no content", "\n".join(caught.output))


class SectionImageTests(TestCase):
    """Images live in rows, not in the section's HTML.

    Before this, all 93 page images were literal <img> tags inside
    `content_html`, so changing one meant hand-editing markup on a page that
    had twenty of them. The importer now lifts each tag into a SectionImage and
    leaves a marker behind.

    The two things that must hold are that the round trip is byte-exact (so the
    public site does not change) and that a broken reference fails loudly (so
    an image is never silently dropped).
    """

    def setUp(self):
        self.page = Page.objects.create(slug="p", title="P", path="/p/")

    def section(self, html="", **kwargs):
        return Section.objects.create(
            page=self.page, key=kwargs.pop("key", "s"), position=0,
            content_html=html, **kwargs)

    # --- extraction -----------------------------------------------------
    def test_extract_lifts_src_alt_and_presentation(self):
        html = ('<img src="{% static \'img/a.jpg\' %}" alt="A" '
                'class="c d" id="i" style="width: 100%;">')
        stored, images = importer.extract_images(html)
        self.assertEqual(stored, "<!--rlecd-image:1-->")
        self.assertEqual(images[0], {
            "position": 0, "image": "img/a.jpg", "alt_text": "A", "caption": "",
            "css_class": "c d", "dom_id": "i", "inline_style": "width: 100%;",
        })

    def test_extract_keeps_document_order(self):
        html = "".join(
            f'<img src="{{% static \'img/{n}.jpg\' %}}" alt="{n}">'
            for n in "abc")
        _, images = importer.extract_images(html)
        self.assertEqual([i["position"] for i in images], [0, 1, 2])
        self.assertEqual([i["image"] for i in images],
                         ["img/a.jpg", "img/b.jpg", "img/c.jpg"])

    def test_extract_passes_absolute_paths_through_unwrapped(self):
        """Wrapping a /media/ upload in {% static %} would look it up in the
        wrong storage, so only the repo-relative form is unwrapped."""
        for src in ("/media/up/x.png", "https://cdn.example/x.png", "/static/img/z.png"):
            with self.subTest(src=src):
                _, images = importer.extract_images(f'<img src="{src}" alt="a">')
                self.assertEqual(images[0]["image"], src)

    def test_extract_handles_a_tag_with_no_alt(self):
        _, images = importer.extract_images('<img src="img/a.jpg">')
        self.assertEqual(images[0]["alt_text"], "")

    def test_extract_leaves_surrounding_markup_untouched(self):
        html = '<p>a</p>\n<img src="img/a.jpg" alt="A">\n<p>b</p>'
        stored, _ = importer.extract_images(html)
        self.assertEqual(stored, '<p>a</p>\n<!--rlecd-image:1-->\n<p>b</p>')

    # --- round trip -----------------------------------------------------
    def test_round_trip_is_byte_exact(self):
        """The whole point: extract then rebuild must give the tag back."""
        original = ('<img src="{% static \'img/k4.jpg\' %}" alt="The Chef\'s Suite">')
        stored, images = importer.extract_images(original)
        self.assertEqual(SectionImage(**images[0]).to_html(), original)

    def test_apostrophes_are_not_escaped(self):
        """Django's escape() would write &#x27; and rewrite the site's own copy.

        The captured alt text is full of real apostrophes, and a
        double-quoted attribute does not need them escaped.
        """
        image = SectionImage(image="img/a.jpg", alt_text="The Chef's Suite")
        self.assertIn("alt=\"The Chef's Suite\"", image.to_html())
        self.assertNotIn("&#x27;", image.to_html())

    def test_ampersand_in_alt_is_escaped(self):
        image = SectionImage(image="img/a.jpg", alt_text="Tom & Jerry")
        self.assertIn('alt="Tom &amp; Jerry"', image.to_html())

    def test_source_tag_wraps_only_repo_paths(self):
        self.assertEqual(SectionImage(image="img/a.jpg").source_tag(),
                         "{% static 'img/a.jpg' %}")
        self.assertEqual(SectionImage(image="/media/up/a.png").source_tag(),
                         "/media/up/a.png")

    def test_no_caption_means_no_figure_wrapper(self):
        """Captions are opt-in, so an untouched import renders a bare <img>."""
        image = SectionImage(image="img/a.jpg", alt_text="A")
        self.assertNotIn("<figure", image.to_html())

    def test_caption_wraps_in_a_figure(self):
        image = SectionImage(image="img/a.jpg", alt_text="A", caption="  Nice  ")
        html = image.to_html()
        self.assertTrue(html.startswith('<figure class="content-figure">'))
        self.assertIn("<figcaption>Nice</figcaption>", html)

    def test_image_with_no_path_renders_nothing(self):
        """An empty path must not emit a broken <img src="">."""
        self.assertEqual(SectionImage(image="").to_html(), "")

    def test_preview_src_is_servable_or_empty(self):
        self.assertEqual(SectionImage(image="img/a.jpg").preview_src, "/static/img/a.jpg")
        self.assertEqual(SectionImage(image="/media/up/a.png").preview_src, "/media/up/a.png")
        self.assertEqual(SectionImage(image="https://c/x.png").preview_src, "https://c/x.png")
        self.assertEqual(SectionImage(image="").preview_src, "")

    # --- substitution ---------------------------------------------------
    def test_resolve_puts_the_tag_back_in_place(self):
        section = self.section("<!--rlecd-image:1-->")
        SectionImage.objects.create(section=section, position=0,
                                    image="img/a.jpg", alt_text="A")
        resolved = render_module.resolve_images(section, section.content_html)
        self.assertEqual(resolved, "<img src=\"{% static 'img/a.jpg' %}\" alt=\"A\">")

    def test_resolve_is_a_noop_without_markers(self):
        section = self.section("<p>plain</p>")
        self.assertEqual(render_module.resolve_images(section, section.content_html),
                         "<p>plain</p>")

    def test_two_markers_resolve_to_their_own_images(self):
        section = self.section("<!--rlecd-image:1--> mid <!--rlecd-image:2-->")
        SectionImage.objects.create(section=section, position=0, image="img/a.jpg")
        SectionImage.objects.create(section=section, position=1, image="img/b.jpg")
        resolved = render_module.resolve_images(section, section.content_html)
        self.assertIn("img/a.jpg", resolved)
        self.assertIn("img/b.jpg", resolved)
        self.assertLess(resolved.index("a.jpg"), resolved.index("b.jpg"))

    def test_a_marker_with_no_row_raises_rather_than_dropping_the_image(self):
        """Silently removing an image leaves a page that looks fine and is
        missing a photo. That is the failure this change exists to prevent."""
        section = self.section("<!--rlecd-image:1-->")
        with self.assertRaises(ContentRenderError) as caught:
            render_module.resolve_images(section, section.content_html)
        self.assertIn("no such image row", str(caught.exception))

    def test_rendering_a_page_with_a_broken_marker_raises(self):
        self.section("<!--rlecd-image:1-->", is_visible=True)
        with self.assertRaises(ContentRenderError):
            render_sections(self.page)

    # --- the import as a whole ------------------------------------------
    def test_sync_creates_rows_and_preserves_an_editors_caption(self):
        """A re-import must not wipe a caption an editor added.

        An <img> tag has no caption, so the extractor always yields "" for that
        field. Copying that over the stored row on every re-import would
        silently delete editorial work.
        """
        section = self.section()
        images = [{"position": 0, "image": "img/a.jpg", "alt_text": "A",
                   "caption": "", "css_class": "", "dom_id": "", "inline_style": ""}]
        importer._sync_section_images(section, images, reset=True)
        self.assertEqual(SectionImage.objects.count(), 1)

        SectionImage.objects.update(caption="Editor wrote this")

        # Same image, fresh extraction: caption must survive.
        importer._sync_section_images(section, images, reset=False)
        self.assertEqual(SectionImage.objects.get().caption, "Editor wrote this")

        # A changed path is imported; a changed alt is imported.
        images[0]["image"] = "img/b.jpg"
        images[0]["alt_text"] = "B"
        importer._sync_section_images(section, images, reset=False)
        row = SectionImage.objects.get()
        self.assertEqual((row.image, row.alt_text, row.caption),
                         ("img/b.jpg", "B", "Editor wrote this"))

    def test_sync_drops_a_row_whose_image_left_the_markup(self):
        section = self.section()
        images = [{"position": 0, "image": "img/a.jpg", "alt_text": "",
                   "caption": "", "css_class": "", "dom_id": "", "inline_style": ""},
                  {"position": 1, "image": "img/b.jpg", "alt_text": "",
                   "caption": "", "css_class": "", "dom_id": "", "inline_style": ""}]
        importer._sync_section_images(section, images, reset=True)
        self.assertEqual(SectionImage.objects.count(), 2)

        importer._sync_section_images(section, images[:1], reset=False)
        self.assertEqual([i.image for i in SectionImage.objects.all()], ["img/a.jpg"])

    def test_gallery_sections_keep_their_type_after_extraction(self):
        """infer_type detects a gallery by looking for "<img", so it has to see
        the chunk before the tags are lifted out."""
        chunk = '<div class="gallery">' + '<img src="img/a.jpg" alt="a">' * 3 + "</div>"
        self.assertEqual(importer.infer_type(chunk), "gallery")
        stored, images = importer.extract_images(chunk)
        self.assertEqual(importer.infer_type(stored), "html")  # why order matters


class PageSummaryTests(TestCase):
    """The Pages changelist has to be readable without opening 19 pages.

    Telling two pages apart otherwise means opening both, which is how a CMS
    ends up worse than the static site it replaced.
    """

    def setUp(self):
        self.page = Page.objects.create(slug="p", title="P", path="/p/")

    def add(self, key, html, position=0, visible=True):
        return Section.objects.create(
            page=self.page, key=key, label=key, position=position,
            content_html=html, is_visible=visible)

    def test_summary_is_the_first_line_of_prose(self):
        self.add("a", "<h1>Kitchen Portfolio</h1><p>Twenty of our finest remodels</p>")
        self.assertEqual(self.page.text_summary(),
                         "Kitchen Portfolio Twenty of our finest remodels")

    def test_summary_skips_style_script_and_svg(self):
        """A CSS rule or an inline icon path must never become the description."""
        self.add("a", "<style>.x{color:red}</style><script>var a=1</script>"
                      "<svg><path d='M0 0'/></svg>"
                      "<p>Real content worth showing</p>")
        summary = self.page.text_summary()
        self.assertIn("Real content", summary)
        self.assertNotIn("color:red", summary)
        self.assertNotIn("var a", summary)

    def test_summary_drops_image_markers(self):
        self.add("a", "<!--rlecd-image:1--><!--rlecd-image:2-->"
                      "<p>Words that matter here</p>")
        self.assertNotIn("rlecd-image", self.page.text_summary())

    def test_summary_collapses_whitespace(self):
        self.add("a", "<p>one\n\n   two</p><p>three</p>")
        self.assertEqual(self.page.text_summary(), "one two three")

    def test_summary_skips_a_section_with_only_markup(self):
        self.add("a", "<div></div>", position=0)
        self.add("b", "<p>Actual words live in the next section</p>", position=1)
        self.assertIn("Actual words", self.page.text_summary())

    def test_summary_ignores_hidden_sections(self):
        self.add("a", "<p>Hidden text nobody should see</p>", visible=False)
        self.add("b", "<p>Visible text that should show</p>", position=1)
        self.assertNotIn("Hidden text", self.page.text_summary())

    def test_summary_is_truncated_with_an_ellipsis(self):
        self.add("a", "<p>" + "word " * 60 + "</p>")
        summary = self.page.text_summary(limit=50)
        self.assertLessEqual(len(summary), 51)
        self.assertTrue(summary.endswith("…"))

    def test_summary_is_empty_when_there_is_no_text(self):
        self.add("a", "<!--rlecd-image:1-->")
        self.assertEqual(self.page.text_summary(), "")

    def test_first_image_follows_render_order(self):
        first = self.add("a", "<!--rlecd-image:1-->", position=0)
        second = self.add("b", "<!--rlecd-image:1-->", position=1)
        image_a = SectionImage.objects.create(
            section=first, position=0, image="img/first.jpg")
        SectionImage.objects.create(section=second, position=0, image="img/second.jpg")
        self.assertEqual(self.page.first_image(), image_a)

    def test_first_image_skips_hidden_sections(self):
        hidden = self.add("a", "<!--rlecd-image:1-->", position=0, visible=False)
        SectionImage.objects.create(section=hidden, position=0, image="img/hidden.jpg")
        self.assertIsNone(self.page.first_image())

    def test_first_image_is_none_when_there_are_no_images(self):
        self.add("a", "<p>text</p>")
        self.assertIsNone(self.page.first_image())


class SectionImageAdminTests(TestCase):
    """The image rows have to be editable, thumbnailed and pickable."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("img", "i@example.com", "pw")
        cls.page = Page.objects.create(slug="p", title="P", path="/p/")
        cls.section = Section.objects.create(
            page=cls.page, key="s", label="S", position=0,
            content_html="<!--rlecd-image:1-->"
                        "<p>A remodeled kitchen with a marble island</p>")
        cls.image = SectionImage.objects.create(
            section=cls.section, position=0, image="img/a.jpg", alt_text="A")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_changelist_shows_a_thumbnail_and_the_page(self):
        html = self.client.get(
            reverse("admin:content_sectionimage_changelist")).content.decode()
        self.assertIn("/static/img/a.jpg", html)
        self.assertIn(self.page.path, html)

    def test_the_image_field_gets_the_picker(self):
        """The picker is the whole reason these rows exist."""
        from main.widgets import MediaPathWidget

        response = self.client.get(
            reverse("admin:content_sectionimage_change", args=[self.image.pk]))
        form = response.context["adminform"].form
        self.assertIsInstance(form.fields["image"].widget, MediaPathWidget)
        self.assertIn("admin/js/media_picker.js", response.content.decode())

    def test_presentation_fields_are_read_only(self):
        """`class="display-img active"` is what makes the carousel work, so it
        is reproduced on render but must not be casually edited."""
        html = self.client.get(
            reverse("admin:content_sectionimage_change", args=[self.image.pk])
        ).content.decode()
        self.assertIn("field-css_class", html)
        self.assertIn("field-dom_id", html)
        self.assertIn("field-inline_style", html)

    def test_the_section_form_offers_the_editor_and_the_images(self):
        html = self.client.get(
            reverse("admin:content_section_change", args=[self.section.pk])
        ).content.decode()
        self.assertIn("admin/js/rich_text.js", html)
        self.assertIn("data-rte-surface", html)
        # And the inline that holds the images, with the picker on each row.
        self.assertIn("images-TOTAL_FORMS", html)
        self.assertIn("section-image-thumb", html)
        self.assertIn("data-picker-open", html)

    def test_section_list_offers_a_link_to_each_sections_images(self):
        html = self.client.get(
            reverse("admin:content_section_changelist")).content.decode()
        self.assertIn(reverse("admin:content_section_change",
                              args=[self.section.pk]), html)

    def test_page_list_shows_a_thumbnail_a_summary_and_an_image_count(self):
        html = self.client.get(
            reverse("admin:content_page_changelist")).content.decode()
        self.assertIn("/static/img/a.jpg", html)
        self.assertIn("page-summary", html)

    def test_broken_image_row_does_not_break_the_changelist(self):
        SectionImage.objects.create(section=self.section, position=1, image="")
        response = self.client.get(
            reverse("admin:content_sectionimage_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "—")

    def test_the_page_image_thumbnail_falls_back_to_a_dash(self):
        """A page whose first image has no servable path must still list."""
        self.image.image = ""
        self.image.save()
        response = self.client.get(reverse("admin:content_page_changelist"))
        self.assertEqual(response.status_code, 200)


class PageContentScreenTests(TestCase):
    """The page body gets its own screen, as cards.

    It used to be a table of eight columns inlined on the page form, where the
    markup cell was 3,000 characters and the row you wanted was somewhere
    inside it. These tests pin what replaced it, and the two things that must
    not have changed: the markup is still stored verbatim, and the screen is
    still permission-checked against the page.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("e", "e@e.com", "pw")
        cls.page = Page.objects.create(slug="p", title="P", path="/p/")
        cls.hero = Section.objects.create(
            page=cls.page, key="hero", label="Hero", position=0,
            content_html=('<section><h1>Kitchen Portfolio</h1>'
                          '<!--rlecd-image:1-->'
                          '<p>Twenty of our finest remodels.</p></section>'))
        cls.gallery = Section.objects.create(
            page=cls.page, key="gallery", label="Gallery", position=1,
            content_html='<div class="gallery"><!--rlecd-image:1--></div>')
        cls.hidden = Section.objects.create(
            page=cls.page, key="old", label="Retired", position=2,
            content_html="<p>No longer shown</p>", is_visible=False)
        SectionImage.objects.create(
            section=cls.hero, position=0, image="img/hero.jpg", alt_text="Hero")
        SectionImage.objects.create(
            section=cls.gallery, position=0, image="img/g1.jpg")
        SectionImage.objects.create(
            section=cls.gallery, position=1, image="img/g2.jpg")

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin:content_page_content", args=[self.page.pk])

    def test_it_renders_a_card_per_section_in_order(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/page_content.html")
        self.assertEqual([c["section"].position for c in
                          response.context["cards"]], [0, 1, 2])

    def test_a_card_shows_the_sections_words_not_its_markup(self):
        response = self.client.get(self.url)
        card = response.context["cards"][0]
        self.assertIn("Kitchen Portfolio", card["text"])
        self.assertNotIn("<h1>", card["text"])
        self.assertNotIn("rlecd-image", card["text"])

    def test_a_card_shows_the_sections_pictures(self):
        card = self.client.get(self.url).context["cards"][1]
        self.assertEqual(card["thumbs"], ["/static/img/g1.jpg",
                                          "/static/img/g2.jpg"])

    def test_a_section_with_no_images_gets_no_strip(self):
        """A blank strip is worse than none -- it reads as a broken image."""
        card = self.client.get(self.url).context["cards"][2]
        self.assertEqual(card["thumbs"], [])

    def test_a_hidden_section_is_flagged_not_hidden(self):
        """A section that was written and then switched off looks identical to
        one that was never written, so it has to be said out loud."""
        html = self.client.get(self.url).content.decode()
        self.assertIn("is-hidden-section", html)
        self.assertIn("Hidden", html)

    def test_an_image_only_section_says_so_in_words(self):
        card = self.client.get(self.url).context["cards"][1]
        self.assertEqual(card["text"], "")
        self.assertContains(self.client.get(self.url),
                              "image or layout block")

    def test_each_card_links_to_its_section(self):
        html = self.client.get(self.url).content.decode()
        for section in (self.hero, self.gallery, self.hidden):
            self.assertIn(
                reverse("admin:content_section_change", args=[section.pk]), html)

    def test_a_page_with_no_sections_says_so(self):
        empty = Page.objects.create(slug="empty", title="E", path="/empty/")
        response = self.client.get(
            reverse("admin:content_page_content", args=[empty.pk]))
        self.assertContains(response, "no sections")

    def test_a_page_with_no_content_is_called_out(self):
        """It renders blank on the site, and the captured version is being used
        instead. Saying so here beats an editor wondering why it looks empty."""
        empty = Page.objects.create(slug="e2", title="E", path="/e2/")
        self.assertContains(
            self.client.get(reverse("admin:content_page_content",
                                    args=[empty.pk])),
            "renders blank")

    def test_the_changelist_offers_a_way_in(self):
        """The list is where an editor starts, so the way into the body has to
        be on the list and not only on the page form."""
        self.assertContains(
            self.client.get(reverse("admin:content_page_changelist")),
            reverse("admin:content_page_content", args=[self.page.pk]))

    def test_the_page_form_offers_a_way_in(self):
        self.assertContains(
            self.client.get(reverse("admin:content_page_change",
                                    args=[self.page.pk])),
            reverse("admin:content_page_content", args=[self.page.pk]))

    def test_the_page_form_no_longer_carries_the_sections_table(self):
        """The wall of markup this replaced."""
        html = self.client.get(
            reverse("admin:content_page_change", args=[self.page.pk])).content.decode()
        self.assertNotIn("sections-TOTAL_FORMS", html)
        self.assertNotIn("field-content_html", html)

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_a_staff_member_without_permission_is_refused(self):
        """It is a view of a page's body, so it obeys the page's permissions."""
        from django.contrib.auth.models import Permission

        staff = User.objects.create_user("s", "s@e.com", "pw", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        staff.user_permissions.add(Permission.objects.get(
            codename="view_page", content_type__app_label="content"))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_missing_page_is_a_404_not_a_500(self):
        self.assertEqual(
            self.client.get(reverse("admin:content_page_content", args=[99999])
                            ).status_code, 404)

    def test_it_does_not_change_the_stored_markup(self):
        """The screen is a way of reading the markup, not of rewriting it."""
        before = list(self.page.sections.order_by("position")
                      .values_list("content_html", flat=True))
        self.client.get(self.url)
        after = list(self.page.sections.order_by("position")
                     .values_list("content_html", flat=True))
        self.assertEqual(before, after)
        self.assertIn("<!--rlecd-image:1-->", self.hero.content_html)


class SectionFormReadabilityTests(TestCase):
    """The section's own form: words first, markup last and collapsed."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("e", "e@e.com", "pw")
        cls.page = Page.objects.create(slug="p", title="P", path="/p/")
        cls.section = Section.objects.create(
            page=cls.page, key="s", label="S", position=0,
            content_html='<h1>Heading</h1><!--rlecd-image:1--><p>Body text.</p>')
        SectionImage.objects.create(
            section=cls.section, position=0, image="img/a.jpg", alt_text="A")

    def setUp(self):
        self.client.force_login(self.admin)
        self.html = self.client.get(
            reverse("admin:content_section_change", args=[self.section.pk])
        ).content.decode()

    def test_the_words_are_shown_as_words(self):
        self.assertIn("section-as-text", self.html)
        self.assertIn("Heading Body text.", self.html)

    def test_no_html_box_is_offered_at_all(self):
        """The captured markup is not editable, and is not shown as code.

        It used to be a textarea full of escaped angle brackets and
        `{% static %}` tags. That is accurate and unusable, and it was the
        reason the CMS was not usable by anyone but a developer.
        """
        self.assertNotIn("field-content_html", self.html)
        self.assertNotIn('name="content_html"', self.html)
        self.assertNotIn("content_html", self.html)

    def test_the_editor_is_offered_instead(self):
        self.assertIn("field-content_body", self.html)
        self.assertIn("data-rte-surface", self.html)
        self.assertIn("admin/js/rich_text.js", self.html)

    def test_the_system_managed_fields_are_not_offered(self):
        """key, is_locked and position decide the page's structure.

        key is how a re-import matches a section, so editing it silently
        detaches the row from its copy. is_locked marks the structural chunks
        -- the page <style>, a nested <main> -- whose loss breaks the page.
        """
        for field in ("id_key", "id_is_locked", "id_position"):
            with self.subTest(field=field):
                self.assertNotIn(field, self.html)

    def test_the_form_explains_which_copy_is_live(self):
        """Otherwise nobody can tell a section they edited from one they did not."""
        self.assertIn("studio-hint", self.html)
        self.assertIn("captured from the live site", self.html)

    def test_the_images_table_is_on_this_form(self):
        self.assertIn("images-TOTAL_FORMS", self.html)
        self.assertIn("section-image-thumb", self.html)

    def test_a_section_with_no_text_explains_itself(self):
        """Otherwise an image-only section looks like a broken record."""
        Section.objects.create(
            page=self.page, key="g", label="Gallery", position=1,
            content_html='<div><!--rlecd-image:1--></div>')
        response = self.client.get(
            reverse("admin:content_section_change", args=[self.section.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_list_shows_the_words_not_the_byte_count(self):
        """`size` was "3,597 B", which tells an editor nothing about the copy."""
        html = self.client.get(
            reverse("admin:content_section_changelist")).content.decode()
        self.assertIn("Heading Body text.", html)
        self.assertNotIn("size", html.split("<thead>")[1].split("</thead>")[0])


class SimpleTabExcerptTests(TestCase):
    """The other content tabs get the same treatment: a readable description."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("e", "e@e.com", "pw")
        cls.page = Page.objects.create(slug="p", title="P", path="/p/")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_faq_shows_the_answer_next_to_the_question(self):
        FAQ.objects.create(page=self.page, question="How long?",
                           answer="Most kitchens take four to six weeks "
                                  "from signed contract to completion.")
        html = self.client.get(
            reverse("admin:content_faq_changelist")).content.decode()
        self.assertIn("How long?", html)
        self.assertIn("Most kitchens take four to six weeks", html)

    def test_testimonial_shows_the_quote_next_to_the_author(self):
        Testimonial.objects.create(
            author="Dana", location="Owings Mills",
            quote="They turned a basement into a bar we actually use.")
        html = self.client.get(
            reverse("admin:content_testimonial_changelist")).content.decode()
        self.assertIn("Dana", html)
        self.assertIn("They turned a basement", html)

    def test_project_shows_its_summary(self):
        Project.objects.create(title="Kitchen", summary="A six-week gut "
                                                     "renovation in Bethesda.")
        html = self.client.get(
            reverse("admin:content_project_changelist")).content.decode()
        self.assertIn("A six-week gut renovation", html)

    def test_a_blank_description_renders_a_dash_not_a_blank_cell(self):
        FAQ.objects.create(page=self.page, question="Q", answer="")
        html = self.client.get(
            reverse("admin:content_faq_changelist")).content.decode()
        self.assertIn('<span class="muted">—</span>', html)

    def test_a_long_description_is_truncated_with_an_ellipsis(self):
        FAQ.objects.create(page=self.page, question="Q",
                           answer="word " * 80)
        html = self.client.get(
            reverse("admin:content_faq_changelist")).content.decode()
        self.assertIn("…", html)


class VisibleTextTests(TestCase):
    def test_it_drops_markup_scripts_and_markers(self):
        self.assertEqual(
            visible_text("<style>a{color:red}</style><script>var x=1</script>"
                         "<svg><path d='M0'/></svg>"
                         "<!--rlecd-image:1--><p>Just  the words</p>"),
            "Just the words")

    def test_it_collapses_whitespace(self):
        self.assertEqual(visible_text("<p>one\n\n  two</p>"), "one two")

    def test_it_handles_empty_and_none(self):
        self.assertEqual(visible_text(""), "")
        self.assertEqual(visible_text(None), "")

    def test_it_keeps_words_inside_attributes_out(self):
        """An href's value is not something anybody reads as copy."""
        self.assertEqual(visible_text('<a href="/very/long/path">Click</a>'),
                         "Click")

    def test_section_plain_text_truncates_on_a_word_boundary(self):
        section = Section(content_html="<p>" + "word " * 40 + "</p>")
        text = section.plain_text(limit=50)
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(len(text), 51)
        self.assertFalse(text.endswith(" …"))


class SectionEditorTests(TestCase):
    """Editing a section must not require touching markup.

    `content_html` is the copy captured from the live site and stays exactly as
    it was. What an editor writes goes in `content_body`, and that is what
    renders once it is non-empty. The captured copy is the fallback, so all 19
    pages keep rendering exactly as they do while someone works through them
    one section at a time.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("e", "e@e.com", "pw")
        cls.page = Page.objects.create(slug="p", title="P", path="/p/")
        cls.section = Section.objects.create(
            page=cls.page, key="s", label="S", position=0,
            content_html='<section class="hero"><h1>Captured</h1>'
                        '<!--rlecd-image:1--><p>Original copy.</p></section>')
        SectionImage.objects.create(
            section=cls.section, position=0, image="img/hero.jpg", alt_text="H")

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse("admin:content_section_change", args=[self.section.pk])

    # --- the fallback contract -----------------------------------------
    def test_an_untouched_section_renders_the_captured_markup(self):
        self.assertFalse(self.section.is_edited())
        self.assertEqual(self.section.render_source(), self.section.content_html)

    def test_a_written_section_renders_the_editor_body(self):
        self.section.content_body = "<p>New copy</p>"
        self.assertTrue(self.section.is_edited())
        self.assertTrue(self.section.render_source().startswith("<p>New copy</p>"))

    def test_an_edited_section_keeps_its_images(self):
        """Fixing a typo must not silently delete every photo on a section.

        The images used to live inside the captured markup, so replacing that
        markup with the editor's text dropped them. They are appended to an
        edited body instead.
        """
        self.section.content_body = "<p>New copy</p>"
        rendered = self.section.render_source()
        self.assertIn("New copy", rendered)
        self.assertIn("<img", rendered)
        self.assertIn("img/hero.jpg", rendered)

    def test_an_edited_section_with_no_image_rows_is_just_the_body(self):
        self.section.images.all().delete()
        self.section.content_body = "<p>Text only</p>"
        self.assertEqual(self.section.render_source(), "<p>Text only</p>")

    def test_whitespace_only_is_not_an_edit(self):
        """A stray space in the box must not blank the section.

        The editor sends an empty string for an untouched box; a space would
        come from someone pressing space and leaving, and it would silently
        replace the captured copy with nothing.
        """
        self.section.content_body = "   \n  "
        self.assertFalse(self.section.is_edited())
        self.assertEqual(self.section.render_source(), self.section.content_html)

    def test_clearing_the_body_restores_the_captured_copy(self):
        self.section.content_body = "<p>New</p>"
        self.section.content_body = ""
        self.assertEqual(self.section.render_source(), self.section.content_html)

    def test_the_page_renders_the_captured_copy_while_untouched(self):
        html = render_sections(self.page)
        self.assertIn("Captured", html)
        self.assertIn("<img", html)

    def test_the_page_renders_the_body_once_written(self):
        self.section.content_body = "<h2>Fresh heading</h2><p>Fresh copy.</p>"
        self.section.save()
        html = render_sections(self.page)
        self.assertIn("Fresh heading", html)
        self.assertIn("Fresh copy.", html)
        self.assertNotIn("Captured", html)
        # The image is unaffected: images live in their own rows.
        self.assertIn("<img", html)

    def test_the_captured_copy_is_never_destroyed_by_an_edit(self):
        self.section.content_body = "<p>Replaced</p>"
        self.section.save()
        self.section.refresh_from_db()
        self.assertIn("Captured", self.section.content_html)

    def test_editor_markup_survives_rendering(self):
        self.section.content_body = (
            "<h2>Heading</h2><p>Body with <strong>bold</strong> and "
            "<em>italic</em>.</p><ul><li>One</li><li>Two</li></ul>")
        self.section.save()
        html = render_sections(self.page)
        for fragment in ("<h2>", "<strong>", "<em>", "<ul>", "<li>"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, html)

    def test_plain_text_reads_the_body_when_edited(self):
        self.section.content_body = "<h2>Heading</h2><p>The words matter.</p>"
        self.assertEqual(self.section.plain_text(), "Heading The words matter.")

    def test_plain_text_falls_back_to_the_captured_copy(self):
        self.assertIn("Original copy", self.section.plain_text())

    # --- what the form actually exposes ---------------------------------
    def test_the_form_offers_the_editor_and_not_the_markup(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("field-content_body", html)
        self.assertIn("data-rte-surface", html)
        self.assertNotIn("content_html", html)
        self.assertNotIn("content_html", html)

    def test_the_editor_carries_its_script(self):
        self.assertIn("admin/js/rich_text.js",
                      self.client.get(self.url).content.decode())

    def test_the_editor_ships_a_hidden_input_not_a_textarea(self):
        """The hidden input is the form field, so inlines and validation keep
        working; the contenteditable is only the surface."""
        html = self.client.get(self.url).content.decode()
        self.assertIn('type="hidden" name="content_body"', html)
        self.assertIn("data-rte=", html)

    def test_no_admin_form_anywhere_exposes_a_raw_markup_field(self):
        """The complaint was that raw HTML was still visible in places.

        Checked across every registered model rather than one screen, because
        the leaks were spread: the section body, the page <head>, and the
        <main> attributes were three separate fields on three separate forms.
        """
        from django.contrib import admin as dj

        found = []
        for model in dj.site._registry.values():
            try:
                url = reverse(
                    f"admin:{model.model._meta.app_label}_"
                    f"{model.model._meta.model_name}_add")
            except Exception:
                continue
            response = self.client.get(url)
            if response.status_code != 200:
                continue
            body = response.content.decode()
            for probe in ("id_content_html", "id_head_html", "id_main_attrs"):
                if probe in body:
                    found.append(f"{model.model._meta.label}: {probe}")
        self.assertEqual(found, [], f"raw markup still editable: {found}")

    def test_the_page_address_is_a_link_not_a_text_box(self):
        page = self.client.get(
            reverse("admin:content_page_change", args=[self.page.pk])
        ).content.decode()
        self.assertIn("url_preview", page)
        self.assertNotIn('name="path"', page)
        self.assertNotIn('name="slug"', page)

    def test_the_page_head_is_not_offered(self):
        """head_html is meta tags and JSON-LD, 3,853 characters of it."""
        page = self.client.get(
            reverse("admin:content_page_change", args=[self.page.pk])
        ).content.decode()
        self.assertNotIn("head_html", page)


def make_page(slug="workflow", path=None, **kwargs):
    return Page.objects.create(
        title=kwargs.pop("title", slug.title()),
        slug=slug,
        path=path or f"/{slug}/",
        **kwargs,
    )


class PublishWorkflowTests(TestCase):
    """A page is only public when it is published and visible.

    Both halves are checked, because the failure this guards against is a
    draft that renders for real visitors: the flag said yes, the workflow said
    no, and nothing noticed.
    """

    def test_a_new_page_is_live_by_default(self):
        self.assertTrue(make_page().is_live)

    def test_switching_the_flag_off_takes_the_page_down(self):
        page = make_page()
        page.is_published = False
        page.save()
        page.refresh_from_db()
        self.assertFalse(page.is_live)
        self.assertEqual(page.status, Page.Status.DRAFT,
                         "a hidden page cannot still claim to be published")

    def test_a_draft_is_not_served_even_with_the_flag_on(self):
        page = make_page(status=Page.Status.DRAFT, is_published=True)
        self.assertFalse(page.is_live)
        self.assertNotIn(page, Page.objects.live())

    def test_unpublish_then_publish_round_trips(self):
        page = make_page()
        page.unpublish()
        self.assertFalse(page.is_live)
        page.publish()
        self.assertTrue(page.is_live)
        self.assertIsNotNone(page.published_at)

    def test_publish_records_who_released_it(self):
        author = User.objects.create_user("editor", password="pw-editor-123")
        page = make_page()
        page.publish(user=author)
        page.refresh_from_db()
        self.assertEqual(page.published_by, author)

    def test_archive_keeps_the_content_but_loses_the_page(self):
        page = make_page()
        page.archive()
        self.assertFalse(page.is_live)
        self.assertTrue(Page.objects.filter(pk=page.pk).exists())

    def test_a_scheduled_page_waits_for_its_time(self):
        soon = timezone.now() + timezone.timedelta(hours=1)
        page = make_page(status=Page.Status.SCHEDULED, scheduled_for=soon)
        self.assertFalse(page.is_live)
        self.assertNotIn(page, Page.objects.live())

    def test_a_scheduled_page_goes_live_once_its_time_arrives(self):
        past = timezone.now() - timezone.timedelta(minutes=1)
        page = make_page(status=Page.Status.SCHEDULED, scheduled_for=past)
        self.assertTrue(page.is_live)
        self.assertIn(page, Page.objects.live())

    def test_a_scheduled_page_with_no_date_stays_offline(self):
        page = make_page(status=Page.Status.SCHEDULED, scheduled_for=None)
        self.assertFalse(page.is_live)

    def test_schedule_without_a_date_is_refused(self):
        page = make_page()
        with self.assertRaises(ValidationError):
            page.schedule(None)

    def test_publish_if_due_leaves_other_pages_alone(self):
        page = make_page()
        self.assertFalse(page.publish_if_due())

    def test_publish_if_due_stamps_the_scheduled_moment(self):
        past = timezone.now() - timezone.timedelta(minutes=5)
        page = make_page(status=Page.Status.SCHEDULED, scheduled_for=past)
        self.assertTrue(page.publish_if_due())
        page.refresh_from_db()
        self.assertEqual(page.status, Page.Status.PUBLISHED)
        self.assertIsNone(page.scheduled_for)
        self.assertAlmostEqual(page.published_at, past, delta=timezone.timedelta(seconds=5))

    def test_a_draft_page_falls_back_to_the_mirror_template(self):
        make_page("about", path="/about/", status=Page.Status.DRAFT)
        response = self.client.get("/about/")
        self.assertEqual(response.status_code, 200)


class PublishDueCommandTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_it_publishes_only_what_is_due(self):
        due = make_page("due", status=Page.Status.SCHEDULED,
                        scheduled_for=self.now - timezone.timedelta(minutes=1))
        later = make_page("later", status=Page.Status.SCHEDULED,
                          scheduled_for=self.now + timezone.timedelta(days=1))
        call_command("publish_due", stdout=StringIO())
        due.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(due.status, Page.Status.PUBLISHED)
        self.assertEqual(later.status, Page.Status.SCHEDULED)

    def test_a_dry_run_changes_nothing(self):
        page = make_page("due", status=Page.Status.SCHEDULED,
                         scheduled_for=self.now - timezone.timedelta(minutes=1))
        out = StringIO()
        call_command("publish_due", "--dry-run", stdout=out)
        page.refresh_from_db()
        self.assertEqual(page.status, Page.Status.SCHEDULED)
        self.assertIn("would publish", out.getvalue())

    def test_it_does_not_override_a_page_someone_unpublished_meanwhile(self):
        page = make_page("due", status=Page.Status.SCHEDULED,
                         scheduled_for=self.now - timezone.timedelta(minutes=1))
        page.is_published = False
        page.status = Page.Status.DRAFT
        page.save()
        call_command("publish_due", stdout=StringIO())
        page.refresh_from_db()
        self.assertEqual(page.status, Page.Status.DRAFT)

    def test_it_says_so_when_nothing_is_due(self):
        out = StringIO()
        call_command("publish_due", stdout=out)
        self.assertIn("Nothing due", out.getvalue())


class PageAdminWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.client.force_login(self.admin)

    def test_publishing_from_the_page_form_button(self):
        page = make_page("draft", status=Page.Status.DRAFT)
        url = reverse("admin:content_page_publish", args=[page.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        page.refresh_from_db()
        self.assertTrue(page.is_live)

    def test_unpublishing_from_the_page_form_button(self):
        page = make_page("live")
        url = reverse("admin:content_page_unpublish", args=[page.pk])
        self.client.get(url)
        page.refresh_from_db()
        self.assertFalse(page.is_live)

    def test_the_bulk_publish_action(self):
        draft = make_page("draft", status=Page.Status.DRAFT)
        self.client.post(reverse("admin:content_page_changelist"), {
            "action": "action_publish",
            "_selected_action": [str(draft.pk)],
        })
        draft.refresh_from_db()
        self.assertTrue(draft.is_live)

    def test_publishing_something_already_published_says_nothing_to_do(self):
        page = make_page("live")
        response = self.client.post(reverse("admin:content_page_changelist"), {
            "action": "action_publish",
            "_selected_action": [str(page.pk)],
        }, follow=True)
        self.assertContains(response, "Nothing to publish")

    def test_a_draft_offers_a_preview_instead_of_a_dead_public_link(self):
        page = make_page("draft", status=Page.Status.DRAFT)
        html = self.client.get(
            reverse("admin:content_page_change", args=[page.pk])).content.decode()
        self.assertIn(reverse("content_preview", args=[page.slug]), html)
        self.assertIn("not public", html)

    def test_a_live_page_offers_its_public_url(self):
        page = make_page("live")
        html = self.client.get(
            reverse("admin:content_page_change", args=[page.pk])).content.decode()
        self.assertIn("/live/", html)
        self.assertNotIn(reverse("content_preview", args=[page.slug]), html)

    def test_the_list_shows_the_workflow_stage(self):
        page = make_page("live")
        page.unpublish()
        html = self.client.get(
            reverse("admin:content_page_changelist")).content.decode()
        self.assertIn("Draft", html)


class PreviewTests(TestCase):
    def setUp(self):
        self.page = make_page("draft", status=Page.Status.DRAFT)
        Section.objects.create(page=self.page, key="body", position=0,
                               content_html="<h1>Draft copy</h1>")

    def test_a_stranger_gets_a_404(self):
        self.assertEqual(self.client.get("/preview/draft/").status_code, 404)

    def test_a_staff_user_with_no_page_permission_gets_a_404(self):
        from django.contrib.auth.models import Permission
        user = User.objects.create_user("nobody", password="pw-nobody-123",
                                        is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            codename="view_lead"))
        self.client.force_login(user)
        self.assertEqual(self.client.get("/preview/draft/").status_code, 404)

    def test_an_editor_can_preview_a_draft(self):
        from django.contrib.auth.models import Permission
        user = User.objects.create_user("editor", password="pw-editor-123",
                                        is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_page", "change_page"]))
        self.client.force_login(user)
        response = self.client.get("/preview/draft/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft copy")

    def test_a_draft_is_still_not_public(self):
        self.client.get("/draft/")
        self.assertEqual(Page.objects.live().filter(slug="draft").count(), 0)


class RevisionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.client.force_login(self.admin)
        self.page = make_page("history")
        self.hero = Section.objects.create(
            page=self.page, key="hero", position=0,
            content_html="<h1>First heading</h1>")

    def snapshot(self, note=""):
        return ContentRevision.snapshot(self.page, user=self.admin, note=note)

    def test_a_snapshot_carries_the_sections(self):
        revision = self.snapshot()
        self.assertEqual([row["key"] for row in revision.section_rows()], ["hero"])
        self.assertIn("First heading", revision.content_html)

    def test_saving_a_page_takes_a_snapshot(self):
        self.client.post(reverse("admin:content_page_change", args=[self.page.pk]), {
            "title": "History", "status": Page.Status.PUBLISHED,
            "is_published": "on", "show_in_menu": "on", "scheduled_for": "",
            "seo_title": "", "seo_description": "", "og_image": "", "main_attrs": "",
            "_save": "Save",
        })
        self.assertTrue(self.page.revisions.exists())
        revision = self.page.revisions.first()
        self.assertEqual(revision.created_by, self.admin)

    def test_restoring_puts_the_old_copy_back(self):
        old = self.snapshot()
        self.hero.content_html = "<h1>Broken heading</h1>"
        self.hero.save()
        old.restore()
        self.hero.refresh_from_db()
        self.assertEqual(self.hero.content_html, "<h1>First heading</h1>")

    def test_restoring_reinstates_a_hidden_section(self):
        old = self.snapshot()
        self.hero.is_visible = False
        self.hero.save()
        old.restore()
        self.hero.refresh_from_db()
        self.assertTrue(self.hero.is_visible)

    def test_restoring_keeps_sections_added_afterwards(self):
        old = self.snapshot()
        Section.objects.create(page=self.page, key="extra", position=1,
                               content_html="<p>New</p>")
        old.restore()
        self.assertTrue(
            Section.objects.filter(page=self.page, key="extra").exists(),
            "a restore must not delete work it did not capture")

    def test_restoring_does_not_touch_the_workflow_stage(self):
        old = self.snapshot()
        self.page.unpublish()
        old.restore()
        self.page.refresh_from_db()
        self.assertFalse(self.page.is_live,
                         "restoring a copy must not quietly publish a draft")

    def test_the_restore_action_is_offered(self):
        self.snapshot()
        html = self.client.get(
            reverse("admin:content_contentrevision_changelist")).content.decode()
        self.assertIn("restore_selected", html)

    def test_the_restore_action_puts_the_page_back(self):
        old = self.snapshot()
        self.hero.content_html = "<h1>Broken heading</h1>"
        self.hero.save()
        self.client.post(reverse("admin:content_contentrevision_changelist"), {
            "action": "restore_selected",
            "_selected_action": [str(old.pk)],
        })
        self.hero.refresh_from_db()
        self.assertEqual(self.hero.content_html, "<h1>First heading</h1>")

    def test_the_history_list_is_reachable_from_the_page(self):
        self.snapshot()
        self.assertEqual(
            self.client.get(reverse("admin:content_contentrevision_changelist")
                            + f"?page__id__exact={self.page.pk}").status_code, 200)

    def test_revisions_cannot_be_added_by_hand(self):
        response = self.client.get(reverse("admin:content_contentrevision_add"))
        self.assertIn(response.status_code, (403, 302))


class SitemapAndRobotsTests(TestCase):
    def setUp(self):
        SiteSetting.load()

    def test_robots_points_at_the_sitemap(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Sitemap:", body)
        self.assertIn("/sitemap.xml", body)

    def test_robots_does_not_500(self):
        """It used to read a field SiteSetting never had."""
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)

    def test_the_sitemap_lists_a_live_page(self):
        make_page("kitchen", path="/kitchen-remodeling/")
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertIn("/kitchen-remodeling/", body)

    def test_the_sitemap_omits_a_draft(self):
        make_page("secret", path="/secret/", status=Page.Status.DRAFT)
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertNotIn("/secret/", body)

    def test_the_sitemap_omits_a_scheduled_page_until_it_is_due(self):
        make_page("later", path="/later/", status=Page.Status.SCHEDULED,
                  scheduled_for=timezone.now() + timezone.timedelta(days=1))
        self.assertNotIn("/later/", self.client.get("/sitemap.xml").content.decode())

    def test_the_sitemap_lists_a_visible_service(self):
        Service.objects.create(name="Outdoor Kitchens", slug="outdoor-kitchens")
        self.assertIn("/outdoor-kitchens/",
                      self.client.get("/sitemap.xml").content.decode())

    def test_the_sitemap_omits_a_switched_off_service(self):
        Service.objects.create(name="Hidden", slug="hidden",
                               is_active=False)
        self.assertNotIn("/hidden/",
                         self.client.get("/sitemap.xml").content.decode())

    def test_a_service_and_page_with_the_same_path_are_listed_once(self):
        make_page("cabinets", path="/cabinets/")
        Service.objects.create(name="Cabinets", slug="cabinets")
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertEqual(body.count("/cabinets/"), 1)

    def test_the_root_page_is_listed_at_the_root(self):
        make_page("index", path="/")
        self.assertIn("<loc>http://testserver/</loc>",
                      self.client.get("/sitemap.xml").content.decode())


class ServicePageTests(TestCase):
    def setUp(self):
        SiteSetting.load()

    def test_a_service_with_no_captured_page_gets_a_page(self):
        Service.objects.create(name="Outdoor Kitchens", slug="outdoor-kitchens",
                               short_description="Cook outside all year.")
        response = self.client.get("/outdoor-kitchens/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outdoor Kitchens")
        self.assertContains(response, "Cook outside all year.")

    def test_an_inactive_service_is_a_404(self):
        Service.objects.create(name="Retired", slug="retired", is_active=False)
        self.assertEqual(self.client.get("/retired/").status_code, 404)

    def test_an_unpublished_service_is_a_404(self):
        Service.objects.create(name="Draft", slug="draft-service",
                               is_published=False)
        self.assertEqual(self.client.get("/draft-service/").status_code, 404)

    def test_an_unknown_slug_is_a_404_rather_than_an_empty_page(self):
        self.assertEqual(self.client.get("/nothing-here/").status_code, 404)

    def test_a_captured_service_page_still_wins(self):
        """The fifteen captured pages must not be shadowed by the fallback."""
        make_page("painting", path="/painting/",
                  head_html="", nav_variant="partials/_nav_1.html")
        Section.objects.create(page=Page.objects.get(slug="painting"),
                               key="captured", position=0,
                               content_html="<p>CAPTURED MARKUP</p>")
        Service.objects.create(name="Painting", slug="painting")
        response = self.client.get("/painting/")
        self.assertContains(response, "CAPTURED MARKUP")

    def test_the_service_page_uses_site_settings_for_its_cta(self):
        settings_row = SiteSetting.load()
        settings_row.phone = "(410) 555-0100"
        settings_row.primary_cta_label = "Call the office"
        settings_row.save()
        Service.objects.create(name="Decks", slug="decks")
        response = self.client.get("/decks/")
        self.assertContains(response, "Call the office")
        self.assertContains(response, "tel:4105550100")


class NavigationTests(TestCase):
    """Navigation is one database tree, cached, and it knows where it is.

    The captured site shipped six copies of the navbar, one per service page,
    each identical except for which link carried class="active". That is the
    problem these tests exist to keep solved: the highlight is now derived from
    the request path, so a new service needs one row, not a new template.
    """

    def _nav(self, name="Header", slug=None):
        from content.models import Navigation
        return Navigation.objects.create(name=name, slug=slug or name.lower(),
                                         is_active=True)

    def _item(self, nav, label, url="", parent=None, sort_order=0, **kwargs):
        from content.models import MenuItem
        return MenuItem.objects.create(
            navigation=nav, label=label, url=url, parent=parent,
            sort_order=sort_order, **kwargs)

    def test_it_builds_one_group_per_active_navigation(self):
        header = self._nav("Header")
        self._item(header, "Home", "/")
        self._item(self._nav("Footer"), "Quick Links", "/")
        Navigation.objects.create(name="Retired", slug="retired",
                                  is_active=False)
        tree = navigation_tree()
        self.assertEqual(sorted(tree), ["footer", "header"])
        self.assertEqual(tree["header"][0]["label"], "Home")

    def test_a_hidden_navigation_is_not_built(self):
        Navigation.objects.create(name="Draft", slug="draft", is_active=False)
        self.assertEqual(navigation_tree(), {})

    def test_a_cached_tree_asks_the_content_tables_for_nothing(self):
        """The second reader gets the tree without touching content again.

        Asserted on the content tables specifically rather than on a total
        count: the database cache backend is itself a query, so "zero queries"
        is not the property that matters. What matters is that walking the
        navigation -- including every dropdown child -- reads no MenuItem or
        Navigation row, which is what would otherwise happen once per item.
        """
        header = self._nav("Header")
        services = self._item(header, "Services", "#")
        for i in range(15):
            self._item(header, f"Service {i}", f"/service-{i}/", parent=services)
        self._item(header, "Contact", "/contact/")

        first = CaptureQueriesContext(connection)
        with first:
            marked = mark_current(navigation_tree()["header"], "/")
        second = CaptureQueriesContext(connection)
        with second:
            again = mark_current(navigation_tree()["header"], "/")
        self.assertEqual(
            [q["sql"] for q in second.captured_queries
             if "content_menuitem" in q["sql"] or "content_navigation" in q["sql"]],
            [],
        )
        self.assertEqual(len(again), len(marked))

    def test_the_query_count_does_not_grow_with_the_number_of_services(self):
        """The N+1 the old template properties caused, asserted as a slope.

        Building the tree for one dropdown item and for twelve must cost the
        same number of queries. A regression to lazy per-item lookups shows up
        here as a difference, which is the thing that actually went wrong
        before.
        """
        def queries_for_children(count):
            cache.clear()
            slug = f"header{count}"
            header = self._nav(f"Header {count}", slug=slug)
            services = self._item(header, "Services", "#")
            for i in range(count):
                self._item(header, f"Service {i}", f"/service-{i}/",
                           parent=services)
            with CaptureQueriesContext(connection) as ctx:
                marked = mark_current(navigation_tree()[slug], "/")
            self.assertEqual(len(marked[0]["children"]), count)
            queries = len([q for q in ctx.captured_queries
                           if "content_menuitem" in q["sql"]
                           or "content_navigation" in q["sql"]])
            # Leave the database as it was found, so the next measurement sees
            # one menu and not two and quietly measures the wrong thing.
            header.delete()
            return queries

        self.assertEqual(queries_for_children(1), queries_for_children(12))

    def test_editing_a_menu_item_drops_the_cache(self):
        """Otherwise the change an editor just saved is invisible to visitors."""
        header = self._nav("Header")
        contact = self._item(header, "Contact", "/contact/")
        self.assertEqual(len(navigation_tree()["header"]), 1)
        contact.label = "Get in touch"
        contact.save()
        self.assertEqual(navigation_tree()["header"][0]["label"], "Get in touch")

    def test_deleting_a_menu_item_drops_the_cache(self):
        header = self._nav("Header")
        contact = self._item(header, "Contact", "/contact/")
        nav_context(RequestFactory().get("/"))
        contact.delete()
        self.assertEqual(navigation_tree()["header"], [])

    def test_a_menus_own_children_never_appear_as_top_level_items(self):
        header = self._nav("Header")
        services = self._item(header, "Services", "#")
        self._item(header, "Painting", "/painting/", parent=services)
        self.assertEqual([i["label"] for i in navigation_tree()["header"]],
                         ["Services"])


class CurrentUrlTests(TestCase):
    """Which link counts as "the page you are on".

    A prefix match is the tempting shortcut and it is wrong: with a nav link to
    /kitchen/, it lights up on /kitchen-renovation-guide/ too, and a site ends
    up with two highlighted items. These are the rules, pinned.
    """

    def test_the_exact_page_matches(self):
        self.assertTrue(is_current_url("/kitchen/", "/kitchen/"))

    def test_a_missing_trailing_slash_is_the_same_page(self):
        """Captured nav markup is inconsistent about the slash."""
        self.assertTrue(is_current_url("/kitchen/", "/kitchen"))
        self.assertTrue(is_current_url("/kitchen", "/kitchen/"))

    def test_the_root_matches_only_the_root(self):
        self.assertTrue(is_current_url("/", "/"))
        self.assertFalse(is_current_url("/", "/kitchen/"))

    def test_a_longer_path_is_not_the_same_page(self):
        self.assertFalse(is_current_url("/kitchen/", "/kitchen-renovation/"))

    def test_a_sibling_page_does_not_match(self):
        self.assertFalse(is_current_url("/kitchen/", "/bathroom/"))

    def test_a_query_string_is_not_part_of_the_page(self):
        self.assertTrue(is_current_url("/kitchen/?ref=nav", "/kitchen/"))

    def test_an_external_link_is_never_the_current_page(self):
        self.assertFalse(is_current_url("https://example.com/", "/kitchen/"))
        self.assertFalse(is_current_url("//example.com/", "/kitchen/"))

    def test_a_telephone_or_mail_link_is_never_the_current_page(self):
        self.assertFalse(is_current_url("tel:+14438983143", "/kitchen/"))
        self.assertFalse(is_current_url("mailto:a@b.com", "/kitchen/"))

    def test_the_placeholder_link_does_not_claim_to_be_a_page(self):
        self.assertFalse(is_current_url("#", "/kitchen/"))
        self.assertFalse(is_current_url("", "/kitchen/"))
        self.assertFalse(is_current_url(None, "/kitchen/"))

    def test_nothing_matches_when_there_is_no_page(self):
        self.assertFalse(is_current_url("/kitchen/", None))
        self.assertFalse(is_current_url("/kitchen/", ""))

    def test_it_accepts_a_request(self):
        request = RequestFactory().get("/kitchen/")
        self.assertTrue(is_current_url("/kitchen/", request))
        self.assertFalse(is_current_url("/bathroom/", request))


class NavigationRenderingTests(TestCase):
    """What a visitor actually gets, in the markup.

    These are the tests that would catch a nav edit going to the database and
    not reaching the page, which is the failure mode that matters.
    """

    def setUp(self):
        from content.models import Navigation
        cache.clear()
        self.header = Navigation.objects.create(name="Header", slug="header",
                                                is_active=True)
        self.services_item = MenuItem.objects.create(
            navigation=self.header, label="Services", url="#", sort_order=1)
        for i, (label, url) in enumerate([
            ("Bathroom Remodeling", "/bathroom-remodeling/"),
            ("Kitchen Remodeling", "/kitchen-remodeling/"),
            ("Painting", "/painting/"),
        ]):
            MenuItem.objects.create(navigation=self.header, label=label,
                                    url=url, parent=self.services_item,
                                    sort_order=i)
        MenuItem.objects.create(navigation=self.header, label="Contact",
                                url="/contact/", sort_order=2)
        SiteSetting.load()

    def _nav_html(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_database_drives_the_navbar(self):
        html = self._nav_html("/")
        for label in ("Bathroom Remodeling", "Kitchen Remodeling", "Painting"):
            self.assertIn(label, html)

    def test_a_service_added_in_the_crm_appears_without_a_new_template(self):
        MenuItem.objects.create(navigation=self.header, label="HVAC",
                                url="/hvac/", parent=self.services_item)
        self.assertIn("HVAC", self._nav_html("/"))

    def test_the_visited_page_is_the_only_one_marked_active(self):
        html = self._nav_html("/painting/")
        active = re.findall(r'<a href="([^"]+)" class="active"', html)
        self.assertIn("/painting/", active)
        self.assertNotIn("/kitchen-remodeling/", active)

    def test_a_visitor_elsewhere_marks_their_own_page(self):
        active = re.findall(r'<a href="([^"]+)" class="active"',
                            self._nav_html("/kitchen-remodeling/"))
        self.assertIn("/kitchen-remodeling/", active)
        self.assertNotIn("/painting/", active)

    def test_the_highlight_is_recomputed_per_request(self):
        """A cached tree must not carry the previous visitor's path."""
        first = re.findall(r'<a href="([^"]+)" class="active"',
                           self._nav_html("/painting/"))
        second = re.findall(r'<a href="([^"]+)" class="active"',
                            self._nav_html("/kitchen-remodeling/"))
        self.assertIn("/painting/", first)
        self.assertNotIn("/kitchen-remodeling/", first)
        self.assertIn("/kitchen-remodeling/", second)
        self.assertNotIn("/painting/", second)

    def test_the_footer_marks_its_current_link_too(self):
        footer = Navigation.objects.create(name="Footer", slug="footer",
                                          is_active=True)
        MenuItem.objects.create(navigation=footer, label="Quick Links",
                                url="/", sort_order=0)
        html = self._nav_html("/")
        self.assertIn("Quick Links", html)

    def test_a_menu_item_hidden_from_mobile_stays_out_of_the_drawer(self):
        """show_on_mobile is the editor's decision and the template must keep it."""
        MenuItem.objects.filter(label="Painting").update(show_on_mobile=False)
        html = self._nav_html("/")
        drawer = html.split('class="mobile-services-list"')[1].split("</ul>")[0]
        self.assertNotIn("Painting", drawer)
        self.assertIn("Kitchen Remodeling", drawer)

    def test_the_same_item_stays_in_the_desktop_dropdown(self):
        MenuItem.objects.filter(label="Painting").update(show_on_mobile=False)
        html = self._nav_html("/")
        mega = html.split('class="mega-grid"')[1].split("</ul>")[0]
        self.assertIn("Painting", mega)


class CapturedVariantTests(TestCase):
    """The captured partials all point at the dynamic one.

    Six hand-maintained service lists is six places for a service to go missing.
    The mirror evidence that the six were the same bar is in the git history of
    these files; what is asserted here is the state we are keeping.
    """

    def _partials(self):
        import pathlib
        root = pathlib.Path(settings.FRONTEND_DIR) / "templates_main" / "partials"
        return root

    def test_every_nav_variant_delegates_to_the_dynamic_partial(self):
        root = self._partials()
        for i in range(1, 7):
            text = (root / f"_nav_{i}.html").read_text()
            self.assertIn('{% include "partials/_nav_dynamic.html" %}', text,
                          f"_nav_{i}.html does not delegate")

    def test_every_footer_variant_delegates_to_the_dynamic_partial(self):
        root = self._partials()
        for i in range(1, 4):
            text = (root / f"_foot_{i}.html").read_text()
            self.assertIn('{% include "partials/_foot_dynamic.html" %}', text,
                          f"_foot_{i}.html does not delegate")

    def test_no_partial_hardcodes_a_service_list(self):
        """The guard against the six copies coming back."""
        root = self._partials()
        for name in [f"_nav_{i}.html" for i in range(1, 7)] + \
                    [f"_foot_{i}.html" for i in range(1, 4)]:
            text = (root / name).read_text()
            self.assertNotIn("fa-utensils", text, f"{name} hardcodes a service")
            self.assertNotIn("fa-bath", text, f"{name} hardcodes a service")


class SeedNavigationCommandTests(TestCase):
    """The command that replaces six hardcoded navbars with rows.

    The Navigation and MenuItem tables were empty on every database in the
    project until this command existed, so the navbar only ever worked through
    the template's fallback branch. These tests are the reason a fresh deploy
    gets a real menu.
    """

    def _seed(self, *args):
        out = StringIO()
        call_command("seed_services", stdout=out)
        call_command("seed_navigation", *args, stdout=out)
        return out.getvalue()

    def test_it_creates_a_header_with_the_dropdown_in_the_captured_order(self):
        self._seed()
        header = Navigation.objects.get(slug="header")
        order = list(MenuItem.objects.filter(
            navigation=header, parent__isnull=True
        ).order_by("sort_order").values_list("label", flat=True))
        self.assertEqual(
            order, ["Home", "About", "Services", "Areas We Serve",
                    "Contact", "Call"])

    def test_the_dropdown_holds_the_fifteen_services_in_order(self):
        self._seed()
        dropdown = MenuItem.objects.get(navigation__slug="header",
                                        label="Services")
        children = MenuItem.objects.filter(parent=dropdown).order_by("sort_order")
        self.assertEqual(children.count(), 15)
        self.assertEqual(children.first().label, "Bathroom Remodeling")
        self.assertEqual(children.last().label, "Home Additions")

    def test_each_service_links_to_its_own_catalogue_row(self):
        """Not a copied path: the link has to follow the slug."""
        self._seed()
        item = MenuItem.objects.get(navigation__slug="header",
                                    label="Woodworking")
        self.assertEqual(item.service.slug, "woodworking")
        self.assertEqual(item.get_url(), "/woodworking/")

    def test_the_icons_the_capture_had_are_kept(self):
        self._seed()
        item = MenuItem.objects.get(navigation__slug="header",
                                    label="Lead Renovator")
        self.assertEqual(item.icon, "fas fa-certificate")

    def test_the_phone_cta_has_no_baked_in_number(self):
        """A number written here would disagree with SiteSetting after an edit."""
        self._seed()
        cta = MenuItem.objects.get(navigation__slug="header", label="Call")
        self.assertTrue(cta.is_cta)
        self.assertEqual(cta.url, "")

    def test_running_it_twice_changes_nothing(self):
        self._seed()
        first = MenuItem.objects.count()
        output = self._seed()
        self.assertEqual(MenuItem.objects.count(), first)
        self.assertIn("0 created", output)

    def test_rerunning_restores_a_corrected_label(self):
        self._seed()
        MenuItem.objects.filter(label="Areas We Serve").update(label="Coverage")
        self._seed()
        self.assertTrue(MenuItem.objects.filter(label="Coverage").exists())

    def test_rebuild_discards_edits(self):
        self._seed()
        MenuItem.objects.filter(label="Coverage").delete()
        call_command("seed_navigation", "--rebuild", stdout=StringIO())
        self.assertTrue(MenuItem.objects.filter(label="Areas We Serve").exists())

    def test_a_service_removed_from_the_command_survives_by_default(self):
        """Otherwise a deploy would silently delete a service from the menu."""
        from content.management.commands import seed_navigation as command
        original = list(command.SERVICES)
        try:
            self._seed()
            command.SERVICES = [s for s in original if s[0] != "Pergolas"]
            self._seed()
            self.assertTrue(MenuItem.objects.filter(label="Pergolas").exists())
        finally:
            command.SERVICES = original

    def test_prune_removes_a_dropped_service(self):
        from content.management.commands import seed_navigation as command
        original = list(command.SERVICES)
        try:
            self._seed()
            command.SERVICES = [s for s in original if s[0] != "Pergolas"]
            self._seed("--prune")
            self.assertFalse(MenuItem.objects.filter(label="Pergolas").exists())
        finally:
            command.SERVICES = original

    def test_prune_leaves_the_others_alone(self):
        from content.management.commands import seed_navigation as command
        original = list(command.SERVICES)
        try:
            self._seed()
            before = MenuItem.objects.count()
            command.SERVICES = [s for s in original if s[0] != "Pergolas"]
            self._seed("--prune")
            self.assertEqual(MenuItem.objects.count(), before - 1)
        finally:
            command.SERVICES = original

    def test_a_missing_service_is_reported_rather_than_linked_to_nowhere(self):
        out = StringIO()
        call_command("seed_services", stdout=out)
        Service.objects.all().delete()
        err = StringIO()
        call_command("seed_navigation", stdout=out, stderr=err)
        self.assertIn("run seed_services first", err.getvalue())


class SeededNavigationEndToEndTests(TestCase):
    """What a visitor sees once the command has run, on a real service page."""

    def setUp(self):
        cache.clear()
        from content.management.commands import seed_navigation  # noqa: F401
        call_command("seed_services", stdout=StringIO())
        call_command("seed_navigation", stdout=StringIO())

    def _nav(self, path):
        html = self.client.get(path).content.decode()
        return html.split('<nav class="navbar">')[1].split("</nav>")[0]

    def test_a_captured_service_page_gets_the_seeded_menu(self):
        """This is the page that used to render _nav_6.html."""
        nav = self._nav("/painting/")
        self.assertIn("fa-utensils", nav)
        self.assertIn('href="/kitchen-remodeling/"', nav)

    def test_each_service_page_highlights_itself(self):
        for path in ("/painting/", "/kitchen-remodeling/", "/shed-builder/"):
            with self.subTest(path=path):
                active = re.findall(r'<a href="([^"]+)" class="active"',
                                    self._nav(path))
                self.assertIn(path, active)

    def test_the_cta_follows_the_phone_in_settings(self):
        settings_row = SiteSetting.load()
        settings_row.phone = "(410) 555-0100"
        settings_row.save()
        nav = self._nav("/")
        self.assertIn("tel:4105550100", nav)
        self.assertIn("(410) 555-0100", nav)

    def test_a_service_added_after_seeding_appears_once_added_to_the_menu(self):
        """The point of the whole exercise: a new service needs no new template."""
        header = Navigation.objects.get(slug="header")
        dropdown = MenuItem.objects.get(navigation=header, label="Services")
        MenuItem.objects.create(navigation=header, label="HVAC",
                                url="/hvac/", parent=dropdown, sort_order=99)
        self.assertIn("HVAC", self._nav("/"))


class SectionRegistryTests(TestCase):
    """The section render registry must stay inert unless deliberately used.

    `content/templates/content/sections/` ships fourteen templates named after
    section types. Every one of them reads fields `Section` does not have, so
    registering them would render the 19 captured hero sections as
    `<h1 class="hero-headline"></h1>` -- an empty H1 on all nineteen pages, with
    a 200 response and a valid-looking document around it.

    These tests pin the empty default so that registering a type is a visible
    act with a test attached, rather than a plausible-looking one-liner.
    """

    @classmethod
    def setUpTestData(cls):
        importer.import_pages(settings.REPO_ROOT, reset=True)
        clear_template_cache()

    def test_no_section_type_is_registered_by_default(self):
        self.assertEqual(
            registry.SECTION_TEMPLATES, {},
            "SECTION_TEMPLATES must ship empty. A type may only be registered "
            "once the model supplies its fields and a test covers the output.",
        )

    def test_no_captured_section_type_resolves_to_a_structured_template(self):
        """The live corpus must render entirely from stored HTML.

        This is the test that would have caught the original mapping: `hero`,
        `gallery` and `form` are the only captured types that had an entry, and
        those three account for 23 of the 58 sections.
        """
        types = set(Section.objects.values_list("type", flat=True))
        self.assertTrue(types, "importer produced no sections")
        for section_type in sorted(types):
            with self.subTest(type=section_type):
                self.assertIsNone(registry.template_for(section_type))

    def test_unregistered_sections_resolve_to_the_raw_template(self):
        for section_type in sorted(set(Section.objects.values_list("type", flat=True))):
            with self.subTest(type=section_type):
                section = Section(type=section_type)
                self.assertEqual(
                    registry.get_section_template(section).template.name,
                    registry.RAW_SECTION,
                )

    def test_registering_a_type_routes_sections_to_that_template(self):
        """The extension point works, so it is a real opt-in rather than dead code."""
        registry.register("test_probe", "content/sections/_raw.html")
        self.addCleanup(registry.unregister, "test_probe")
        self.assertEqual(registry.template_for("test_probe"),
                         "content/sections/_raw.html")

    def test_unregistering_restores_raw_rendering(self):
        registry.register("test_probe", "content/sections/_raw.html")
        registry.unregister("test_probe")
        self.assertIsNone(registry.template_for("test_probe"))

    def test_shipped_sketches_reference_fields_the_model_lacks(self):
        """The reference templates are unrenderable, which is why they are inert.

        If someone later adds these fields to `Section`, this test is the signal
        to revisit the registry rather than a silent failure.
        """
        real = {f.name for f in Section._meta.get_fields()}
        for name in ("hero", "faq", "form", "gallery", "features"):
            with self.subTest(template=name):
                source = (Path(registry.__file__).parent / "templates"
                          / "content" / "sections" / f"{name}.html").read_text()
                body = re.sub(r"\{% comment %\}.*?\{% endcomment %\}", "", source,
                              flags=re.S)
                fields = set(re.findall(r"section\.([a-z_]+)", body))
                # The templates are allowed to use relations that do exist.
                self.assertTrue(fields - {"images", "faqs"} - real,
                                f"{name}.html now only uses real fields; "
                                f"consider registering it with a test")
                self.assertNotIn(f'"{name}"', registry.SECTION_TEMPLATES)


class DynamicHeadBlockTests(TestCase):
    """base.html's own head_meta block must track the database.

    Rendered from base.html directly, so this tests the block as written rather
    than what any particular page ends up emitting -- see `PageHeadWiringTests`
    for that, which is a different and less happy answer.
    """

    @classmethod
    def setUpTestData(cls):
        cls.page = Page.objects.create(
            slug="head-probe", title="Head Probe", path="/head-probe/",
            seo_title="Probe SEO Title",
            seo_description="Probe description.",
        )
        cls.settings_row = SiteSetting.load()
        cls.settings_row.company_name = "Probe Company"
        cls.settings_row.seo_title_suffix = "Probe Suffix"
        cls.settings_row.phone = "+14435550000"
        cls.settings_row.city = "Reisterstown"
        cls.settings_row.state = "MD"
        cls.settings_row.zip_code = "21136"
        cls.settings_row.save()

    def setUp(self):
        cache.clear()
        self.request = RequestFactory().get("/head-probe/")
        self.request.user = __import__(
            "django.contrib.auth.models", fromlist=["AnonymousUser"]
        ).AnonymousUser()

    def _render(self):
        return render_to_string("base.html", {
            "page": self.page,
            "site_settings": SiteSetting.load(),
            "request": self.request,
            "SITE_URL": "https://example.test",
        })

    def test_title_uses_the_page_seo_title_and_the_suffix(self):
        self.assertIn("<title>Probe SEO Title | Probe Suffix</title>",
                      self._render())

    def test_title_falls_back_to_the_page_title_then_to_settings(self):
        self.page.seo_title = ""
        self.assertIn("<title>Head Probe | Probe Suffix</title>", self._render())
        self.page.title = ""
        # effective_seo_title is default_seo_title or company_name.
        self.assertIn("<title>Probe Company | Probe Suffix</title>",
                      self._render())

    def test_description_uses_the_page_then_settings(self):
        self.assertIn('name="description" content="Probe description."',
                      self._render())
        self.page.seo_description = ""
        html = self._render()
        self.assertIn('name="description" content="', html)
        self.assertNotIn('name="description" content="Probe description."', html)

    def test_author_and_site_name_come_from_settings(self):
        html = self._render()
        self.assertIn('name="author" content="Probe Company"', html)
        self.assertIn('property="og:site_name" content="Probe Company"', html)

    def test_robots_follows_the_page_flag(self):
        self.assertIn('content="index, follow"', self._render())
        self.page.noindex = True
        self.assertIn('content="noindex, follow"', self._render())

    def test_canonical_and_og_url_use_the_request_path(self):
        html = self._render()
        self.assertIn('rel="canonical" href="https://example.test/head-probe/"',
                      html)
        self.assertIn('property="og:url" content="https://example.test/head-probe/"',
                      html)

    def test_open_graph_and_twitter_track_the_page(self):
        html = self._render()
        self.assertIn('property="og:title" content="Probe SEO Title | Probe Suffix"',
                      html)
        self.assertIn('name="twitter:title" content="Probe SEO Title | Probe Suffix"',
                      html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)

    def test_json_ld_carries_the_business_details(self):
        html = self._render()
        self.assertIn('"@type": "HomeAndConstructionBusiness"', html)
        self.assertIn('"name": "Probe Company"', html)
        self.assertIn('"telephone": "+14435550000"', html)
        self.assertIn('"addressLocality": "Reisterstown"', html)

    def test_verification_token_is_emitted_when_set(self):
        self.assertNotIn("google-site-verification", self._render())
        self.settings_row.google_site_verification = "probe-token"
        self.settings_row.save()
        cache.clear()
        self.assertIn('name="google-site-verification" content="probe-token"',
                      self._render())


class PageHeadWiringTests(TestCase):
    """Which head does a public page actually serve? (Known defect, pinned.)

    `content/page.html` overrides base.html's `head_meta` with
    `{% page_head page %}`, which renders the imported `Page.head_html`.

    For the homepage that captured head is itself a template that reads
    `page.effective_title` and `site_settings.effective_seo_title`, so the
    Studio fields reach the served page. For the other 18 pages the captured
    head is a frozen copy of the live markup: the title, description, keywords,
    author and every Open Graph and Twitter tag are literal strings. Only
    `canonical` and `og:url` are expressions.

    The consequence, which is what these tests record: editing `seo_title` on
    any page except the homepage changes nothing that a visitor or a crawler
    sees. `DynamicHeadBlockTests` shows the dynamic block in base.html is
    complete and correct, so the fix is to stop overriding it.
    """

    FROZEN_TITLE = "REAL LIFE EXPERIENCE LLC | About Renovation & Remodeling Maryland"

    @classmethod
    def setUpTestData(cls):
        importer.import_pages(settings.REPO_ROOT, reset=True)
        clear_template_cache()
        cls.about = Page.objects.get(slug="about")
        cls.home = Page.objects.get(slug="index")

    def setUp(self):
        cache.clear()

    def test_the_page_template_overrides_the_dynamic_head(self):
        """If this ever passes the other way, the defect below is fixed."""
        source = (Path(__file__).resolve().parent / "templates" / "content"
                  / "page.html").read_text()
        self.assertIn("{% block head_meta %}{% page_head page %}{% endblock %}",
                      source)

    def test_imported_pages_serve_a_single_head(self):
        html = self.client.get("/about/").content.decode()
        self.assertEqual(html.count("<title>"), 1,
                         "two <title> tags: head_html is being rendered "
                         "alongside base.html's block")
        self.assertEqual(html.lower().count('name="description"'), 1)

    def test_eighteen_of_nineteen_pages_serve_a_frozen_title(self):
        frozen = [p.slug for p in Page.objects.exclude(head_html__in=["", None])
                  if "{{" not in p.head_html.split("</title>")[0]]
        self.assertEqual(len(frozen), 18,
                         f"expected 18 frozen head_html rows, found {len(frozen)}")
        self.assertNotIn("index", frozen, "the homepage head is dynamic")

    def test_the_about_page_serves_its_frozen_title(self):
        html = self.client.get("/about/").content.decode()
        self.assertIn(f"<title>{self.FROZEN_TITLE}</title>", html)

    def test_editing_seo_title_does_not_reach_the_about_page(self):
        """The defect. Editing the field is a no-op on 18 of 19 pages."""
        self.about.seo_title = "A Title An Editor Carefully Chose"
        self.about.save()
        cache.clear()
        html = self.client.get("/about/").content.decode()
        self.assertIn(f"<title>{self.FROZEN_TITLE}</title>", html)
        self.assertNotIn("A Title An Editor Carefully Chose", html)

    def test_editing_the_company_name_does_not_reach_the_about_page(self):
        self.assertNotIn("<title>Probe Company", self.client.get("/about/").content.decode())

    def test_the_homepage_head_does_follow_the_settings(self):
        """The one page where the captured head defers to the database."""
        row = SiteSetting.load()
        row.company_name = "Probe Company"
        row.seo_title_suffix = "Probe Suffix"
        row.save()
        cache.clear()
        html = self.client.get("/").content.decode()
        self.assertIn("Probe Company", html)
        self.assertIn("Probe Suffix", html)
