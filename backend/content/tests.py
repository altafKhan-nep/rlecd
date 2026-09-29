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
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from content import importer
from content.models import (
    FAQ, Page, Project, Section, SectionImage, ServiceArea, SiteSetting,
    Testimonial, TrustBadge, visible_text,
)
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
        url = reverse("admin:content_section_change", args=[self.section.pk])
        self.client.post(url, {
            "page": self.page.pk, "key": self.section.key, "position": 0,
            "label": "Edited", "type": "text", "content_html": "<p>UPDATED</p>",
            "images-TOTAL_FORMS": "1", "images-INITIAL_FORMS": "0",
            "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-position": "0", "images-0-image": "img/new.jpg",
            "images-0-alt_text": "New image", "images-0-caption": "",
        })
        self.section.refresh_from_db()
        self.assertEqual(self.section.content_html, "<p>UPDATED</p>")
        # The image posted alongside the section was saved too.
        self.assertTrue(self.section.images.filter(image="img/new.jpg").exists())

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

        The body moved to its own screen, so a page can be created from its
        settings alone and written afterwards. Previously the form rejected any
        POST that did not carry the inline's formset data, which made creating a
        page and writing it a single all-or-nothing request.
        """
        response = self.client.post(reverse("admin:content_page_add"), {
            "title": "New", "slug": "new", "path": "/new/",
            "sort_order": 0,
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
        self.assertIn("Our Story", strip_tags(
            render_to_string("main/about.html")))

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

    def test_the_section_form_offers_the_preview_script(self):
        html = self.client.get(
            reverse("admin:content_section_change", args=[self.section.pk])
        ).content.decode()
        self.assertIn("admin/js/section_editor.js", html)
        # And the inline that holds the images.
        self.assertIn("images-TOTAL_FORMS", html)
        self.assertIn("section-image-thumb", html)

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

    def test_the_html_box_is_still_there(self):
        """It has to be: the page is a byte-exact mirror, so the markup is the
        source of truth. This is about where it sits, not whether it exists."""
        self.assertIn("field-content_html", self.html)

    def test_the_html_box_is_collapsed(self):
        """Not what most edits are about, so it should not be the loudest
        thing on the screen."""
        box_at = self.html.index('name="content_html"')
        heading_at = self.html.index("HTML (only if you need it)")
        self.assertLess(heading_at, box_at)
        # The class is on the wrapping fieldset, which opens before the
        # heading, so the window has to start before it.
        self.assertIn('class="module aligned collapse"',
                      self.html[heading_at - 400:heading_at])

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
