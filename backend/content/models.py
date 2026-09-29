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
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


class Page(models.Model):
    """One public URL.

    The shell-variant fields exist because the live site genuinely ships
    different navbars, footers and script blocks on different pages; storing
    them per page is what keeps the mirror faithful instead of "cleaned up".
    """

    class Meta:
        ordering = ["path"]

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200, help_text="Internal page name.")
    path = models.CharField(
        max_length=200, unique=True,
        help_text="URL path, e.g. '/' or '/about/'. Must match urls.py.",
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.path})"

    def get_absolute_url(self):
        return reverse("admin:content_page_change", args=[self.pk])

    @property
    def effective_title(self):
        return self.seo_title or self.title

    def visible_sections(self):
        return self.sections.filter(is_visible=True).order_by("position")

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

    @staticmethod
    def derive_label(html, position):
        """Best-effort human label so the CRM list is not a wall of 'html'."""
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
    """Singleton row of brand facts, so a phone number is edited in one place."""

    class Meta:
        verbose_name = "Site settings"

    company_name = models.CharField(max_length=200, default="REAL LIFE EXPERIENCE LLC")
    phone = models.CharField(max_length=40, blank=True)
    email = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=240, blank=True)
    service_area_summary = models.CharField(max_length=240, blank=True)
    years_in_business = models.PositiveSmallIntegerField(blank=True, null=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    whatsapp_url = models.URLField(blank=True)
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
