"""Frontend content models.

Design constraint that shapes everything here: the public site is a
byte-exact mirror of rlecd.com, so the database must be able to reproduce the
existing markup *exactly*, not merely approximately.

The approach is therefore additive rather than reductive. A `Page` records the
per-page shell differences (head, which navbar/footer/script variant it uses),
and its `Section` rows hold the page body split at lossless top-level HTML
boundaries. Rendering is a plain ordered concatenation, which is why an edit
made in the CRM is the only difference between the local page and the live one.
"""
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone
from django.utils.text import slugify


def visible_text(html):
    """The words in a chunk of stored markup, as one clean line.

    Strips <style>, <script> and <svg> first, because a CSS rule or an inline
    icon path is not something anybody wants to read as a page description.
    Then drops the image markers -- they are slots, not content -- and finally
    the remaining tags, collapsing the whitespace the markup left behind.

    One implementation, used by the changelists and the page-content screen, so
    an editor reads the same words a visitor sees without having to read markup
    to find them.
    """
    import re

    text = re.sub(r"<(style|script|svg)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


class PageQuerySet(models.QuerySet):
    def live(self):
        """Pages a visitor may see right now.

        Visibility has two independent parts and both are required. The
        `is_published` flag is the operator's on/off switch and dates back to
        the importer. `status` is the editorial stage. A page that is flagged
        visible but sitting in Draft has not been released, and a page that is
        Published but flagged invisible has been switched off; treating either
        one alone as authoritative is how a draft page ends up indexed.
        """
        now = timezone.now()
        return self.filter(is_published=True).filter(
            models.Q(status=Page.Status.PUBLISHED)
            | models.Q(status=Page.Status.SCHEDULED, scheduled_for__lte=now)
        )


class Page(models.Model):
    """One public URL.

    The shell-variant fields exist because the live site genuinely ships
    different navbars, footers and script blocks on different pages; storing
    them per page is what keeps the mirror faithful instead of "cleaned up".

    Supports draft/publish workflow: Draft, Published, Scheduled, Archived.
    Saving content does NOT automatically publish -- the editor moves the page
    with `publish()` / `unpublish()` / `schedule()`, and the public site
    serves `Page.objects.live()`, so a draft has no URL at all.
    """

    class Meta:
        ordering = ["path"]

    objects = PageQuerySet.as_manager()

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        SCHEDULED = "scheduled", "Scheduled"
        ARCHIVED = "archived", "Archived"

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200, help_text="Internal page name.")
    path = models.CharField(
        max_length=200, unique=True,
        help_text="URL path, e.g. '/' or '/about/'. Must match urls.py.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PUBLISHED,
        blank=True,
    )
    is_published = models.BooleanField(default=True)
    show_in_menu = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    # --- per-page shell, mirrored verbatim from the live build ------------
    head_html = models.TextField(
        blank=True,
        help_text="Contents of the head_meta block, exactly as shipped.",
    )
    main_attrs = models.CharField(
        max_length=200, blank=True,
        help_text='Attributes the live page puts on <main>, e.g. class="contact-page".',
    )
    nav_variant = models.CharField(
        max_length=60, blank=True, help_text="Navbar partial, e.g. partials/_nav_1.html",
    )
    footer_variant = models.CharField(
        max_length=60, blank=True, help_text="Footer partial, e.g. partials/_foot_1.html",
    )
    post_variant = models.CharField(
        max_length=60, blank=True,
        help_text="Post-footer script partial, e.g. partials/_post_1.html",
    )

    # --- SEO overrides (blank = use the head_html as captured) ------------
    seo_title = models.CharField("SEO title", max_length=200, blank=True)
    seo_description = models.TextField(blank=True)
    seo_keywords = models.CharField(max_length=400, blank=True)
    og_image = models.CharField(max_length=300, blank=True)
    noindex = models.BooleanField(default=False)

    # --- workflow fields ---------------------------------------------------
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="published_pages",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="updated_pages",
    )
    scheduled_for = models.DateTimeField(
        null=True, blank=True,
        help_text="When a Scheduled page goes live. Left empty, a scheduled "
                  "page stays offline.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.path})"

    def get_absolute_url(self):
        return reverse("admin:content_page_change", args=[self.pk])

    def save(self, *args, **kwargs):
        """Keep the workflow stage and the visibility flag from disagreeing.

        A page cannot be Published and simultaneously switched off: the flag
        is the thing the importer and the changelist filter on, and a row that
        says otherwise is only ever a bug. An empty stage means nobody chose
        one, which is a page that has not been through the workflow rather
        than a page that is in it.

        Everything else is left to `publish()` / `unpublish()`, which are the
        sanctioned way to move a page between stages, so that `published_at`
        is stamped with a real time and a real author.
        """
        if not self.status:
            self.status = self.Status.PUBLISHED
        if not self.is_published and self.status == self.Status.PUBLISHED:
            self.status = self.Status.DRAFT
        super().save(*args, **kwargs)

    @property
    def is_live(self):
        """Is this page served to the public at this moment?"""
        if not self.is_published:
            return False
        if self.status == self.Status.PUBLISHED:
            return True
        if self.status == self.Status.SCHEDULED:
            return bool(self.scheduled_for
                        and self.scheduled_for <= timezone.now())
        return False

    def publish(self, user=None):
        """Release the page. Idempotent apart from restamping the time."""
        self.status = self.Status.PUBLISHED
        self.is_published = True
        self.scheduled_for = None
        self.published_at = timezone.now()
        if user is not None and getattr(user, "pk", None):
            self.published_by = user
        self.save()
        return self

    def unpublish(self, user=None):
        """Pull the page off the public site without discarding it."""
        self.status = self.Status.DRAFT
        self.is_published = False
        self.scheduled_for = None
        if user is not None and getattr(user, "pk", None):
            self.updated_by = user
        self.save()
        return self

    def schedule(self, when, user=None):
        """Queue the page to go live at `when`."""
        if when is None:
            raise ValidationError({"scheduled_for": "A scheduled page needs a date."})
        self.status = self.Status.SCHEDULED
        self.is_published = True
        self.scheduled_for = when
        if user is not None and getattr(user, "pk", None):
            self.updated_by = user
        self.save()
        return self

    def archive(self, user=None):
        """Retire the page. It keeps its content but leaves the site for good."""
        self.status = self.Status.ARCHIVED
        self.is_published = False
        self.scheduled_for = None
        if user is not None and getattr(user, "pk", None):
            self.updated_by = user
        self.save()
        return self

    def publish_if_due(self, now=None):
        """Promote a scheduled page whose time has arrived.

        Returns True when the page moved. Called by `publish_due` so that
        scheduling survives without a cron job that has to be remembered, and
        safe to call on a published or draft page, which it leaves alone.
        """
        if self.status != self.Status.SCHEDULED or not self.is_published:
            return False
        now = now or timezone.now()
        if not self.scheduled_for or self.scheduled_for > now:
            return False
        self.published_at = self.scheduled_for
        self.status = self.Status.PUBLISHED
        self.scheduled_for = None
        self.save()
        return True

    @property
    def effective_title(self):
        return self.seo_title or self.title

    def visible_sections(self):
        return self.sections.filter(is_visible=True).order_by("position")

    def first_image(self):
        """This page's first image in render order, or None.

        Walks the sections rather than reading `og_image`, because `og_image`
        is the social-preview path and is usually empty on a captured page.
        """
        return SectionImage.objects.filter(
            section__page_id=self.pk, section__is_visible=True,
        ).order_by("section__position", "position", "pk").first()

    def text_summary(self, limit=110):
        """The first line of real prose on the page, for the changelist.

        Derived from the stored markup so it cannot drift from what the page
        says. Skips <style>, <script> and <svg> so a CSS rule or an inline icon
        path never becomes the description, and collapses whitespace so the
        result is one clean line in a table cell.
        """
        collected = []
        for section in self.sections.filter(is_visible=True).order_by("position"):
            collected.append(visible_text(section.content_html))
            # Kept going until there is a sentence's worth. Demanding that a
            # *single* section be long enough would blank the summary of a page
            # whose copy is split into several short sections.
            joined = " ".join(part for part in collected if part)
            if len(joined) >= 20:
                break
        text = " ".join(part for part in collected if part)
        if not text:
            return ""
        if len(text) > limit:
            return text[:limit].rsplit(" ", 1)[0] + "…"
        return text

    def has_content(self):
        """Can this row actually render a page?

        A published row with nothing in it renders an empty <main> with no
        navbar and no footer, because the whole shell -- head, nav, footer and
        post block -- is read off this row. That is worse than a missing page:
        a visitor arrives from a search result or a nav link and has no way
        back. `content.views.render_page` uses this to fall back to the mirror
        template instead, so a half-imported page degrades to the original
        site's content rather than to a blank screen.

        Any one of these is enough to count as real content. Sections are the
        body; the shell variants are the per-page navbar, footer and script
        block, and a page that has one of those but no body is still a page.
        """
        return bool(
            self.sections.exists()
            or self.head_html.strip()
            or self.nav_variant
            or self.footer_variant
            or self.post_variant
        )


class SectionType(models.TextChoices):
    """Editorial label. Purely for the CRM UI — any value renders the same."""
    HERO = "hero", "Hero"
    FEATURES = "features", "Feature grid"
    GALLERY = "gallery", "Gallery"
    STATS = "stats", "Stats / numbers"
    TEXT = "text", "Text block"
    CTA = "cta", "Call to action"
    FORM = "form", "Lead form"
    FAQ = "faq", "FAQ"
    TESTIMONIALS = "testimonials", "Testimonials"
    AREAS = "areas", "Service areas"
    STYLE = "style", "Style block"
    HTML = "html", "Raw HTML"


class ContentRevision(models.Model):
    """A snapshot of a page at a point in time.

    A page's body is its sections, so a snapshot has to carry the sections
    too -- a revision that only stored the page row would restore a title and
    an SEO description while leaving the body exactly as broken as it was,
    which is the moment somebody reaches for "undo" and finds it does nothing.
    `sections_json` holds them; `content_html` is the same content flattened
    for reading and for searching the revision list.
    """

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "content revision"
        verbose_name_plural = "content revisions"

    #: Section fields a snapshot carries. Images are deliberately excluded:
    #: they are rows in their own right, and restoring a section must not
    #: resurrect or discard an editor's photographs.
    SECTION_FIELDS = ("key", "position", "type", "label",
                      "content_html", "content_body", "is_visible")

    page = models.ForeignKey(
        Page, on_delete=models.CASCADE, related_name="revisions")
    title = models.CharField(max_length=200)
    content_html = models.TextField()
    sections_json = models.TextField(
        blank=True,
        help_text="The page's sections at snapshot time, as JSON.",
    )
    head_html = models.TextField(blank=True)
    seo_title = models.CharField(max_length=200, blank=True)
    seo_description = models.TextField(blank=True)
    status = models.CharField(max_length=20, blank=True)
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="content_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    def __str__(self):
        return f"Revision {self.pk} of {self.page.title} ({self.created_at:%Y-%m-%d %H:%M})"

    @classmethod
    def snapshot(cls, page, user=None, note=""):
        """Record the page as it is right now."""
        sections = list(page.sections.all())
        payload = [
            {field: getattr(section, field) for field in cls.SECTION_FIELDS}
            for section in sections
        ]
        return cls.objects.create(
            page=page,
            title=page.title,
            content_html="".join(section.render_source() for section in sections),
            sections_json=json.dumps(payload),
            head_html=page.head_html,
            seo_title=page.seo_title,
            seo_description=page.seo_description,
            status=page.status,
            is_published=page.is_published,
            created_by=user if getattr(user, "pk", None) else None,
            note=note or "auto",
        )

    def section_rows(self):
        """The snapshot's sections, newest first, decoded."""
        try:
            rows = json.loads(self.sections_json or "[]")
        except json.JSONDecodeError:
            return []
        return rows if isinstance(rows, list) else []

    def restore(self):
        """Put this revision back on the page.

        Sections that existed at snapshot time are rewritten in place, matched
        on `key` rather than on primary key: the importer rebuilds section rows
        on re-capture, so an id from weeks ago may name a different slice of
        the page by now. Sections added since the snapshot are left alone
        rather than deleted -- a restore that quietly removed a paragraph an
        editor had added, along with the images attached to it, would be worse
        than the problem the editor was trying to undo.
        """
        page = self.page
        page.title = self.title
        page.head_html = self.head_html
        page.seo_title = self.seo_title
        page.seo_description = self.seo_description
        page.save()

        existing = {section.key: section for section in page.sections.all()}
        for row in self.section_rows():
            key = row.get("key")
            if not key:
                continue
            section = existing.get(key)
            if section is None:
                section = Section(page_id=page.pk, key=key)
            for field in self.SECTION_FIELDS:
                if field in row:
                    setattr(section, field, row[field])
            section.save()
        return page


class Section(models.Model):
    """An ordered, individually editable slice of a page body.

    `content_html` is stored pre-templatised (it may contain `{% static %}`,
    `{% url %}` and `{% csrf_token %}`) and is rendered through the template
    engine with the request context. That is deliberate: it is what lets a
    database-backed page still emit working asset URLs and a valid CSRF token.
    It also means section content is only as trustworthy as the accounts that
    can edit it — see render.render_sections.
    """

    class Meta:
        ordering = ["page", "position"]
        unique_together = [("page", "key")]
        indexes = [models.Index(fields=["page", "position"])]

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="sections")
    key = models.SlugField(
        max_length=120,
        help_text="Stable identifier, used in templates and for reordering.",
    )
    label = models.CharField(
        max_length=200, blank=True,
        help_text="Shown in the CRM list. Auto-derived from the first heading.",
    )
    type = models.CharField(
        max_length=20, choices=SectionType.choices, default=SectionType.HTML,
    )
    position = models.PositiveIntegerField(default=0)
    content_html = models.TextField()
    is_visible = models.BooleanField(
        default=True, help_text="Untick to hide without deleting.",
    )
    content_body = models.TextField(
        blank=True,
        help_text="What this section says. Written in the editor on the form -- "
                  "no HTML needed. Leave it empty to keep the copy that was "
                  "captured from the live site.",
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="Structural chunks (page <style>, nested <main>). Keep these.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.label or f"{self.page.slug} #{self.position}"

    def save(self, *args, **kwargs):
        if not self.key:
            base = slugify(self.label)[:100] or "section"
            key, n = base, 2
            # Scoped by page_id, not self.page: the FK may be set from a form
            # without the related object being loaded, and self.page raises
            # RelatedObjectDoesNotExist in that case.
            siblings = Section.objects.filter(page_id=self.page_id).exclude(pk=self.pk)
            while siblings.filter(key=key).exists():
                key = f"{base}-{n}"
                n += 1
            self.key = key
        if not self.label:
            self.label = Section.derive_label(self.content_html, self.position)
        super().save(*args, **kwargs)

    def plain_text(self, limit=None):
        """What this section actually says, as text.

        The edit screen is a formatting box, not HTML. This is still the other
        half: so a reader can see the section's words without opening it, and
        tell at a glance whether the copy is the copy they meant.
        """
        text = visible_text(self.editable_html() or self.content_html)
        if limit and len(text) > limit:
            return text[:limit].rsplit(" ", 1)[0] + "…"
        return text

    def editable_html(self):
        """The body the editor owns, or "" when the captured markup is in use.

        Deliberately not a fallback chain hidden in the template. A section is
        either *written in the editor* or *inherited from the capture*, and the
        admin says which, so nobody is editing one and looking at the other.
        """
        return (self.content_body or "").strip()

    def is_edited(self):
        """True once someone has written this section in the editor.

        `capture_content` re-imports the captured markup, so this is also how
        the admin can tell an edited section from an untouched one.
        """
        return bool((self.content_body or "").strip())

    def render_source(self):
        """The markup this section contributes to the page.

        Prefers the editor's body, falling back to the captured markup only
        while the body is empty -- which is what keeps all 19 pages rendering
        exactly as they do today while an editor works through them one at a
        time.

        An edited body has the section's images appended to it. The images used
        to live inside the captured markup, so replacing that markup with plain
        text would silently drop every photograph on the section the first time
        somebody fixed a typo in it. They are appended rather than hidden
        because a section that is missing its pictures is a bug nobody would
        notice until a visitor did.
        """
        body = self.editable_html()
        if not body:
            return self.content_html
        images = "".join(image.to_html() for image in self.images.all()
                         if image.image)
        return f"{body}{images}" if images else body

    def first_image(self):
        """This section's first image in render order, or None."""
        return self.images.order_by("position", "pk").first()

    @staticmethod
    def derive_label(html, position):
        import re
        for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>",
                        r"<h3[^>]*>(.*?)</h3>"):
            m = re.search(pattern, html, re.S | re.I)
            if m:
                text = re.sub(r"<[^>]+>", "", m.group(1))
                text = " ".join(text.split())[:80]
                if text:
                    return text
        return f"Section {position + 1}"


def _attr(value):
    """Escape a value for a double-quoted HTML attribute.

    Deliberately not `django.utils.html.escape`, which also turns `'` into
    `&#x27;`. The captured alt text contains real apostrophes -- "The Chef's
    Suite" on the kitchen page -- and escaping them rewrites the site's own
    copy for no gain, because a double-quoted attribute does not need it. The
    result is byte-identical to the markup that was captured.
    """
    return (str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


class SectionImage(models.Model):
    """One image inside a section's body, editable without touching HTML.

    The captured pages had their images hard-coded as literal `<img>` tags in
    the section markup, which meant changing one meant hand-editing HTML on a
    page with 20 of them. The importer now lifts each tag out into a row here
    and leaves a `<!--rlecd-image:N-->` marker in its place, so the path, alt
    text and caption are ordinary fields an editor can change.

    The presentation attributes (`css_class`, `dom_id`, `inline_style`) are
    reproduced verbatim on render, not as things to edit. Thirteen of the
    captured images depend on them for layout -- `class="display-img active"`
    drives a carousel -- so dropping them would silently restyle the site.
    They are shown read-only in the admin and kept out of the editable set.

    `image` is a CharField holding a path, exactly like `Page.og_image` and
    `Project.image`, and for the same reason: a captured row may point at a
    file in this repository, while a newly uploaded one points into MEDIA_URL.
    Two spellings, one field, no migration on the existing content.
    """

    section = models.ForeignKey(
        Section, on_delete=models.CASCADE, related_name="images")
    position = models.PositiveIntegerField(default=0)
    image = models.CharField(
        max_length=300, blank=True,
        help_text="Repo path (img/photo.jpg), an uploaded /media/... path, "
                  "or a full URL. Use the picker to choose from the library.",
    )
    alt_text = models.CharField(
        max_length=300, blank=True,
        help_text="Describes the image for screen readers. Required for "
                  "accessibility even though the captured copy left four "
                  "images with none.",
    )
    caption = models.TextField(
        blank=True,
        help_text="Optional. Rendered as a <figcaption>, which wraps the "
                  "image in a <figure>.",
    )

    # Reproduced on render, not intended for editing.
    css_class = models.CharField(max_length=200, blank=True, editable=False)
    dom_id = models.CharField(max_length=100, blank=True, editable=False)
    inline_style = models.CharField(max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["section", "position", "pk"]
        verbose_name = "section image"
        verbose_name_plural = "section images"

    def __str__(self):
        return self.alt_text or self.image or f"Image {self.position + 1}"

    def source_tag(self):
        """The `src` value, as template source rather than a final URL.

        A repo path is emitted as `{% static %}` so the engine resolves it, which
        is what the captured markup did and what makes static-hashed filenames
        keep working. Anything already absolute -- an upload, a CDN URL -- is
        passed through untouched, because wrapping it in `{% static %}` would
        look it up under the wrong storage.
        """
        value = (self.image or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "/")):
            return value
        return "{%% static '%s' %%}" % value

    def to_html(self):
        """Rebuild the `<img>` tag.

        Attribute order is fixed to src, alt, class, id, style, which is the
        order the captured markup used, so re-rendering an untouched import
        reproduces the original bytes exactly.
        """
        src = self.source_tag()
        if not src:
            return ""
        parts = [f'<img src="{src}"']
        if self.alt_text:
            parts.append(f'alt="{_attr(self.alt_text)}"')
        if self.css_class:
            parts.append(f'class="{_attr(self.css_class)}"')
        if self.dom_id:
            parts.append(f'id="{_attr(self.dom_id)}"')
        if self.inline_style:
            parts.append(f'style="{_attr(self.inline_style)}"')
        tag = " ".join(parts) + ">"
        if self.caption.strip():
            return (f'<figure class="content-figure">'
                    f'{tag}<figcaption>{escape(self.caption.strip())}'
                    f'</figcaption></figure>')
        return tag

    @property
    def preview_src(self):
        """A URL the admin can put in an <img>, or '' if there is nothing to show."""
        value = (self.image or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "/media/", "/static/")):
            return value
        if value.startswith("/"):
            return ""
        return f"/static/{value}"


class ServiceArea(models.Model):
    """A town/city the business serves."""

    class Meta:
        ordering = ["name"]

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    group = models.CharField(
        max_length=120, blank=True, db_index=True,
        help_text="Optional grouping, e.g. 'Baltimore County'.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    blurb = models.TextField(blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)


class FAQ(models.Model):
    class Meta:
        ordering = ["sort_order", "id"]

    question = models.CharField(max_length=300)
    answer = models.TextField()
    page = models.ForeignKey(
        Page, null=True, blank=True, on_delete=models.CASCADE,
        related_name="faqs", help_text="Leave blank for site-wide.",
    )
    service = models.ForeignKey(
        "crm.Service", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="faqs",
    )
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.question[:80]


class Testimonial(models.Model):
    class Meta:
        ordering = ["sort_order", "id"]

    author = models.CharField(max_length=160)
    location = models.CharField(max_length=160, blank=True)
    quote = models.TextField()
    rating = models.PositiveSmallIntegerField(
        default=5, help_text="1-5. Used for AggregateRating schema.",
    )
    is_published = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.author} — {self.quote[:40]}"


class Project(models.Model):
    """Portfolio entry."""

    class Meta:
        ordering = ["-completed_on", "sort_order"]

    title = models.CharField(max_length=200)
    service = models.ForeignKey(
        "crm.Service", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="projects",
    )
    area = models.ForeignKey(
        ServiceArea, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="projects",
    )
    summary = models.TextField(blank=True)
    image = models.CharField(
        max_length=300, blank=True,
        help_text="Static path or URL, e.g. /static/img/kitchen1.jpg.",
    )
    completed_on = models.DateField(null=True, blank=True)
    budget_range = models.CharField(max_length=80, blank=True)
    is_published = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class TrustBadge(models.Model):
    """Certifications and memberships, e.g. NTCA, BBB, licence numbers."""

    class Meta:
        ordering = ["sort_order", "id"]

    label = models.CharField(max_length=160)
    issuer = models.CharField(max_length=160, blank=True)
    image = models.CharField(max_length=300, blank=True)
    url = models.URLField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    def __str__(self):
        return self.label


class SiteSetting(models.Model):
    """Singleton row of brand facts, so a phone number is edited in one place.

    This is the true global source of truth for all business content. Every
    public template reads from this model — navbar, footer, contact sections,
    CTA buttons, SEO, Open Graph, Twitter cards, JSON-LD, schema, copyright,
    and contact links. No business phone number, email, company name, or address
    is hardcoded in public templates.
    """

    class Meta:
        verbose_name = "Site settings"

    company_name = models.CharField(max_length=200, default="REAL LIFE EXPERIENCE LLC")
    legal_company_name = models.CharField(max_length=200, blank=True)
    tagline = models.CharField(max_length=300, blank=True)
    logo = models.CharField(max_length=300, blank=True)
    favicon = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    phone_display = models.CharField(max_length=40, blank=True)
    email = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=240, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=80, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    service_area_summary = models.CharField(max_length=240, blank=True)
    business_hours = models.TextField(blank=True)
    years_in_business = models.PositiveSmallIntegerField(blank=True, null=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    whatsapp_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    financing_url = models.URLField(blank=True)
    bbb_url = models.URLField(blank=True)
    map_url = models.URLField(blank=True)
    map_embed = models.TextField(blank=True)
    default_seo_title = models.CharField(max_length=200, blank=True)
    seo_title_suffix = models.CharField(
        max_length=120, blank=True,
        help_text="Appended to page titles after a pipe, e.g. 'Home "
                  "Renovation & Remodeling'. Leave blank to use each page's "
                  "title on its own.",
    )
    google_site_verification = models.CharField(
        max_length=120, blank=True,
        help_text="The content= value from Google's verification tag.",
    )
    default_seo_description = models.TextField(blank=True)
    default_seo_keywords = models.CharField(max_length=400, blank=True)
    default_og_image = models.CharField(max_length=300, blank=True)
    primary_cta_label = models.CharField(max_length=80, blank=True)
    primary_cta_url = models.CharField(max_length=300, blank=True)
    copyright_text = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def effective_phone_display(self):
        return self.phone_display or self.phone

    @property
    def effective_phone_tel(self):
        if not self.phone:
            return ""
        return self.phone.replace("(", "").replace(")", "").replace(" ", "").replace("-", "")

    @property
    def effective_address(self):
        parts = [self.address, self.city, self.state, self.zip_code]
        return ", ".join(p for p in parts if p)

    @property
    def effective_copyright(self):
        if self.copyright_text:
            return self.copyright_text
        return f"&copy; {self.company_name}. All Rights Reserved."

    @property
    def effective_seo_title(self):
        return self.default_seo_title or self.company_name

    @property
    def verification_token(self):
        """The Google site-verification value, or nothing.

        The token Google issued for this domain is a fact about the site, not
        a phrase about the business, but it belongs with the rest of the head
        configuration rather than inside a template where the next person to
        take the site to a new domain will not find it.
        """
        return self.google_site_verification

    @property
    def effective_seo_description(self):
        return self.default_seo_description or self.tagline

    @property
    def effective_logo(self):
        return self.logo or "/static/img/rlecd_maryland_logo.png"

    @property
    def effective_og_image(self):
        return self.default_og_image or self.effective_logo

    @property
    def effective_cta_label(self):
        return self.primary_cta_label or "Call Now"

    @property
    def effective_cta_url(self):
        if self.primary_cta_url:
            return self.primary_cta_url
        if self.phone:
            return f"tel:{self.effective_phone_tel}"
        return "/contact/"


class Navigation(models.Model):
    """A named navigation group (header, footer, mobile, etc.).

    One database configuration generates both desktop and mobile navigation.
    No separate hardcoded desktop/mobile service lists exist.
    """

    class Meta:
        verbose_name = "navigation"
        verbose_name_plural = "navigation"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    """One item in a navigation group.

    Supports navigation label, destination (URL, service, or page), service
    grouping, order, visibility, desktop/mobile visibility, dropdown/mega-menu,
    icon, featured service flag, and CTA item flag.
    """

    class Meta:
        ordering = ["sort_order", "label"]
        verbose_name = "menu item"
        verbose_name_plural = "menu items"

    navigation = models.ForeignKey(
        Navigation, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE,
        related_name="children")
    label = models.CharField(max_length=120)
    url = models.CharField(max_length=300, blank=True)
    service = models.ForeignKey(
        "crm.Service", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="menu_items")
    page = models.ForeignKey(
        Page, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="menu_items")
    icon = models.CharField(max_length=100, blank=True)
    css_classes = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    show_on_desktop = models.BooleanField(default=True)
    show_on_mobile = models.BooleanField(default=True)
    is_cta = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    open_in_new_tab = models.BooleanField(default=False)

    def __str__(self):
        return self.label

    def get_url(self):
        if self.url:
            return self.url
        if self.service:
            return self.service.get_absolute_url()
        if self.page:
            return self.page.path
        return "#"

    @property
    def has_dropdown(self):
        return self.children.filter(is_visible=True).exists()

    @property
    def dropdown_items(self):
        return self.children.filter(is_visible=True).order_by("sort_order")


class MediaItem(models.Model):
    """An uploaded image, addressable by a stable public path.

    Image-bearing content fields (Page.og_image, Project.image, TrustBadge.image)
    are deliberately CharFields holding a path like "/static/img/x.jpg" rather
    than ImageFields. They were populated by capturing the mirror, where the
    values are already public URLs. Converting them to ImageFields would
    orphan every captured row and point at files that live in the repo, not in
    MEDIA_ROOT.

    This model is the upload side of that arrangement: editors add images here,
    then copy the resulting path into whichever field needs it. Keeping those
    fields as text is what lets one image reference a repo static file, a CDN,
    or an upload made here.
    """

    ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")

    class Meta:
        verbose_name = "Media item"
        verbose_name_plural = "Media library"
        ordering = ("-created_at",)

    image = models.ImageField(upload_to="uploads/%Y/%m")
    title = models.CharField(
        max_length=160, blank=True,
        help_text="Shown in the library. Falls back to the filename.",
    )
    alt_text = models.CharField(
        max_length=200, blank=True,
        help_text=(
            "Describes the image for screen readers and when it fails to "
            "load. Used by pickers when they build an img tag."
        ),
    )
    is_published = models.BooleanField(
        default=True,
        help_text=(
            "Unpublished images stay in the library but are hidden from "
            "pickers. Untick to retire an image without breaking live pages "
            "that already reference it."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title or self.filename

    def clean(self):
        # Checked here rather than in upload_to so a rejected file produces a
        # form validation error naming the field, instead of a ValueError from
        # inside the storage layer after the upload has already been written.
        name = (self.image.name or "").lower()
        if name and not name.endswith(self.ALLOWED_EXTENSIONS):
            raise ValidationError(
                "Unsupported image type. Allowed: %s"
                % ", ".join(self.ALLOWED_EXTENSIONS)
            )

    def save(self, *args, **kwargs):
        if not self.title:
            self.title = (self.image.name or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        super().save(*args, **kwargs)

    @property
    def filename(self):
        return (self.image.name or "").rsplit("/", 1)[-1]

    @property
    def public_path(self):
        """The value to paste into Page.og_image, Project.image, etc."""
        return self.image.url if self.image else ""

    def reference_candidates(self):
        """Every spelling of this image that a path field might hold.

        The picker writes `public_path`, but these fields are text that an
        editor can also fill by hand, so a lookup has to try the forms that
        actually occur in practice -- the served URL, and the bare storage
        name -- instead of only the one this code writes.
        """
        return {value for value in (self.public_path, self.image.name or "") if value}

    def references(self):
        """Rows that point at this image, as dicts for the change form.

        Retiring an image is safe; deleting one that a live page references is
        the destructive mistake this library invites, because the reference is
        a path stored as text and nothing at the database level stops it. The
        change form lists these so the blast radius is visible first.
        """
        from django.apps import apps
        from django.urls import NoReverseMatch, reverse

        targets = (
            ("content", "Page", "og_image"),
            ("content", "Project", "image"),
            ("content", "TrustBadge", "image"),
        )
        candidates = self.reference_candidates()
        if not candidates:
            return []

        found = []
        for app_label, model_name, field in targets:
            try:
                model = apps.get_model(app_label, model_name)
                rows = model.objects.filter(**{f"{field}__in": candidates})
            except Exception:
                # A missing table or an unmigrated app must not take the change
                # form down; an incomplete list is better than a 500.
                continue
            for row in rows:
                try:
                    url = reverse(
                        # Admin URL names are lowercased: the model is `Page`
                        # but the route is `content_page_change`. Using the
                        # class name here raises NoReverseMatch silently and
                        # drops every link.
                        f"admin:{app_label}_{model_name.lower()}_change",
                        args=[row.pk],
                    )
                except NoReverseMatch:
                    url = None
                found.append({"model": model_name, "label": str(row), "url": url})
        found.sort(key=lambda row: (row["model"], row["label"]))
        return found

    @property
    def size_display(self):
        try:
            return self.image.size
        except (OSError, ValueError):
            return 0
