"""CRIM interface for all frontend content.

Sections are edited inline on their page, so the common case — change this
heading, reorder these blocks, hide that one — is a single screen. The raw HTML
box is always available because captured sections are the mirror's own markup.
"""
from django.contrib import admin
from django.http import Http404
from django.db.models import Sum
from django.template.response import TemplateResponse
from django.urls import path
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from content.models import (
    FAQ, MediaItem, Page, Project, Section, SectionImage, ServiceArea,
    SiteSetting,
    Testimonial, TrustBadge,
)
from content.render import clear_template_cache
from main.listview import StudioListMixin
from main.widgets import MediaPickerFieldsMixin


class SectionImageInline(MediaPickerFieldsMixin, admin.TabularInline):
    """One row per image in a section, with a thumbnail and a picker.

    The thumbnail is the point of this inline. Before, an editor changed an
    image by reading a filename out of raw HTML; here they see the picture they
    are about to replace.

    `css_class`, `dom_id` and `inline_style` are shown read-only. Thirteen of the
    captured images depend on them -- `class="display-img active"` is what makes
    the service carousel work -- so they are reproduced on render but must not be
    casually edited. Deleting a row whose marker is still in the content is the
    one dangerous action here, and it is caught loudly at render time rather
    than quietly dropping a photo.
    """

    model = SectionImage
    extra = 1
    ordering = ["position", "pk"]
    fields = ("position", "thumb", "image", "alt_text", "caption",
              "css_class", "dom_id", "inline_style")
    readonly_fields = ("thumb", "css_class", "dom_id", "inline_style")
    classes = ("section-image-inline",)
    media_picker_fields = ("image",)

    @admin.display(description="Preview")
    def thumb(self, obj):
        if not obj or not obj.pk:
            return format_html('<span class="muted">{}</span>', _("No image yet"))
        src = obj.preview_src
        if not src:
            return format_html('<span class="muted">{}</span>', _("No image set"))
        return format_html(
            '<img src="{}" class="section-image-thumb" alt="" loading="lazy">',
            src)


@admin.register(SectionImage)
class SectionImageAdmin(MediaPickerFieldsMixin, StudioListMixin,
                       admin.ModelAdmin):
    """Cross-section image browser, for find-and-fix without opening 19 pages."""

    list_display = ("thumb", "alt_text", "page_slug", "position", "caption")
    list_display_links = ("alt_text",)
    list_filter = ("section__page",)
    search_fields = ("alt_text", "image", "caption", "section__label")
    readonly_fields = ("thumb", "page_slug", "css_class", "dom_id",
                       "inline_style")
    media_picker_fields = ("image",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("section__page")

    @admin.display(description="Image", ordering="image")
    def thumb(self, obj):
        if not obj.preview_src:
            return format_html('<span class="muted">—</span>')
        return format_html(
            '<img src="{}" class="section-image-thumb" alt="" loading="lazy">',
            obj.preview_src)

    @admin.display(description="Page", ordering="section__page__path")
    def page_slug(self, obj):
        return obj.section.page.path


@admin.register(Page)
class PageAdmin(MediaPickerFieldsMixin, StudioListMixin,
                admin.ModelAdmin):
    list_display = ("first_image", "path", "title", "summary", "is_published",
                    "content_link", "image_count", "locked_count", "updated_at")
    list_display_links = ("path",)
    media_picker_fields = ("og_image",)
    list_filter = ("is_published", "show_in_menu", "nav_variant", "footer_variant")
    search_fields = ("path", "title", "seo_title", "seo_description")
    ordering = ("path",)
    readonly_fields = ("created_at", "updated_at", "preview_link",
                       "edit_content")
    fieldsets = (
        (None, {"fields": ("title", "slug", "path", "is_published",
                           "show_in_menu", "sort_order")}),
        ("Page content", {
            "fields": ("preview_link", "edit_content"),
            "description": (
                "The page body lives in its own screen, as one card per "
                "section, so this form stays about the page rather than about "
                "its markup."
            ),
        }),
        ("SEO", {
            "classes": ("collapse",),
            "fields": ("seo_title", "seo_description", "seo_keywords",
                       "og_image", "noindex"),
        }),
        ("Shell (mirrored from live)", {
            "classes": ("collapse",),
            "fields": ("head_html", "main_attrs", "nav_variant",
                       "footer_variant", "post_variant"),
            "description": (
                "These mirror the live site. Editing head_html changes every "
                "meta tag and JSON-LD block on the page."
            ),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_urls(self):
        """Add the page-content screen to the page admin's own namespace.

        On PageAdmin rather than the AdminSite so it sits behind the same
        change permission as the page it describes, and so `?page=<pk>` links
        from the section add form resolve.
        """
        return [
            path("<path:object_id>/content/",
                 self.admin_site.admin_view(self.page_content_view),
                 name="content_page_content"),
        ] + super().get_urls()

    def page_content_view(self, request, object_id, extra_context=None):
        """One page's sections as cards: what each says, and what it looks like.

        Replaces the sections table that used to be inlined on the page form.
        The markup is still stored exactly as captured and still has to be --
        the pages are a byte-exact mirror -- so this is a way of *reading* that
        markup rather than a reformatting of it.
        """
        page = self.get_object(request, object_id)
        if page is None or not self.has_view_or_change_permission(request, page):
            raise Http404(
                f"No page with id {object_id!r} or you cannot view it.")

        sections = list(
            page.sections.select_related().prefetch_related("images")
            .order_by("position", "pk")
        )
        cards = []
        for section in sections:
            thumbs = [image.preview_src for image in section.images.all()
                      if image.preview_src]
            cards.append({
                "section": section,
                "text": section.plain_text(limit=220),
                "thumbs": thumbs[:6],
                "thumb_count": len(thumbs),
            })

        context = {
            **self.admin_site.each_context(request),
            "title": _("Page content"),
            "subtitle": page.path,
            "page": page,
            "cards": cards,
            "opts": self.model._meta,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/page_content.html", context)

    @admin.display(description="Content")
    def content_link(self, obj):
        """Open the page's content screen from the list.

        The list is where an editor starts -- "which page was that on?" -- so
        the way into the body has to be here and not only on the page form.
        """
        if not obj or not obj.pk:
            return ""
        count = obj.sections.count()
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:content_page_content", args=[obj.pk]),
            format_html("{} section{}", count, "" if count == 1 else "s"),
        )

    @admin.display(description="Edit content")
    def edit_content(self, obj):
        """The button to the page's content screen.

        A readonly field rather than a plain link in the fieldset description,
        because a link built here carries the page's own permission check and
        cannot drift out of sync with the object's id.
        """
        if not obj or not obj.pk:
            return ""
        return format_html(
            '<a class="button" href="{}">{}</a>',
            reverse("admin:content_page_content", args=[obj.pk]),
            _("Edit page content"),
        )

    @admin.display(description="Image")
    def first_image(self, obj):
        """The page's first image, so the list reads as a set of pictures.

        Sections own their images, so this walks them in render order rather
        than reading a field on the page. `og_image` is a social-preview path,
        not the page's content, and showing the two side by side would be
        actively misleading.
        """
        image = obj.first_image()
        if image is None:
            return format_html('<span class="muted">—</span>')
        return format_html(
            '<img src="{}" class="section-image-thumb" alt="" loading="lazy">',
            image.preview_src)

    @admin.display(description="Summary")
    def summary(self, obj):
        """A readable line of what the page says.

        Without it, telling two pages apart means opening both. That is the
        reason a CMS ends up worse than the static site it replaced.
        """
        text = obj.text_summary()
        if not text:
            return format_html('<span class="muted">{}</span>', _("No text yet"))
        return format_html('<span class="page-summary">{}</span>', text)

    @admin.display(description="Images")
    def image_count(self, obj):
        """Links to this page's sections, where its images are edited."""
        if not obj or not obj.pk:
            return ""
        count = obj.sections.aggregate(total=Sum("images__id"))["total"] or 0
        if not count:
            return format_html('<span class="muted">—</span>')
        return format_html(
            '<a href="{}?page__id__exact={}">{}</a>',
            reverse("admin:content_section_changelist"), obj.pk, count)

    @admin.display(description="sections")
    def section_count(self, obj):
        return obj.sections.count()

    @admin.display(description="locked")
    def locked_count(self, obj):
        return obj.sections.filter(is_locked=True).count()

    def studio_public_url(self, obj):
        """Public URL for the row's "view on site" action, or None.

        A draft has no public page, so offering the link would send the owner to
        a 404 and imply the change is live. Publishing is the flag that decides,
        not the presence of a path.
        """
        if not obj.pk or not obj.is_published:
            return None
        return reverse("index") if obj.path == "/" else obj.path

    def preview_link(self, obj):
        if not obj.pk:
            return "Save the page first."
        url = reverse("index") if obj.path == "/" else obj.path
        return format_html('<a class="button" href="{}" target="_blank">'
                           'Open {} in a new tab</a>', url, obj.path)

    def save_model(self, request, obj, form, change):
        # A compiled template of the old markup may still be cached.
        clear_template_cache()
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        clear_template_cache()
        super().save_related(request, form, formsets, change)

    def delete_model(self, request, obj):
        clear_template_cache()
        super().delete_model(request, obj)


@admin.register(Section)
class SectionAdmin(StudioListMixin, admin.ModelAdmin):
    """Cross-page section browser, for find-and-fix without opening 19 pages."""

    class Media:
        # The live preview of content_html. Separate from the picker's script
        # rather than bundled, because only the section form needs it.
        js = ("admin/js/section_editor.js",)

    list_display = ("page", "position", "label", "type", "excerpt",
                    "is_visible", "is_locked", "image_count")
    list_filter = ("type", "is_visible", "is_locked", "page")
    list_editable = ("is_visible",)
    search_fields = ("label", "content_html", "key")
    ordering = ("page", "position")
    # key is system-managed so re-imports can match sections; page and position
    # stay editable so a section can be added to or moved within a page.
    readonly_fields = ("key", "as_text", "preview")
    fieldsets = (
        (None, {"fields": ("page", "position", "label", "type", "is_visible")}),
        # The words, so an editor can read the section without reading markup.
        ("What this section says", {
            "fields": ("as_text", "preview"),
        }),
        ("Images", {
            # The rows themselves are the inline below; this is just the signpost,
            # because an inline with no heading above it reads as part of the
            # previous fieldset.
            "fields": (),
            "description": "Images for this section are in the table below.",
        }),
        # Markup last and collapsed. It still has to be here and still has to be
        # the source of truth -- the pages are a byte-exact mirror -- but it is
        # not what most edits are about, so it should not be the first thing on
        # the screen or the loudest thing in it.
        ("HTML (only if you need it)", {
            "classes": ("collapse",),
            "fields": ("key", "is_locked", "content_html"),
            "description": (
                "Rendered exactly as stored. Django tags are available: "
                "<code>{% static 'img/x.jpg' %}</code>, "
                "<code>{% url 'contact' %}</code>, "
                "<code>{% csrf_token %}</code>. A live preview of this markup "
                "appears below the editor."
            ),
        }),
    )
    inlines = [SectionImageInline]
    actions = ["make_visible", "make_hidden"]

    @admin.display(description="Text")
    def excerpt(self, obj):
        """The first line of the section's words, for the list."""
        if not obj or not obj.pk:
            return ""
        text = obj.plain_text(limit=90)
        if not text:
            return format_html('<span class="muted">{}</span>',
                               _("Image or layout only"))
        return format_html('<span class="page-summary">{}</span>', text)

    @admin.display(description="Images")
    def image_count(self, obj):
        """A count that links to this section's change form.

        Images hang off a section, and Django does not render an inline inside
        an inline, so changing an image is a second hop. This makes the hop
        findable from the section list instead of something the editor has to
        know to look for.
        """
        if not obj or not obj.pk:
            return ""
        count = obj.images.count()
        return format_html(
            '<a href="{}">{}</a>',
            reverse("admin:content_section_change", args=[obj.pk]),
            format_html("{} image{}", count, "" if count == 1 else "s"),
        )

    @admin.display(description="size")
    def size(self, obj):
        return f"{len(obj.content_html or ''):,} B"

    def preview(self, obj):
        if obj.pk:
            return format_html(
                '<div class="section-preview-note">{}</div>', obj.plain_text(400))
        return "-"

    preview.short_description = "Current text"

    @admin.display(description="This section's text")
    def as_text(self, obj):
        """The section's words, rendered as prose rather than markup.

        The two halves of editing a section are different jobs. Most edits are
        "is this the right sentence, is it showing" -- answered by reading.
        The rest are layout changes -- answered by the HTML box further down.
        This is the reading half, and it is above the fold for that reason.
        """
        if not obj or not obj.pk:
            return ""
        text = obj.plain_text()
        if not text:
            return format_html(
                '<p class="muted">{}</p>',
                _("This section has no text — it is images and layout. "
                  "Edit it in the HTML section below, or add an image above."))
        return format_html('<div class="section-as-text">{}</div>', text)

    @admin.action(description="Show selected sections")
    def make_visible(self, request, queryset):
        n = queryset.update(is_visible=True)
        clear_template_cache()
        self.message_user(request, f"{n} section(s) shown.")

    @admin.action(description="Hide selected sections")
    def make_hidden(self, request, queryset):
        n = queryset.update(is_visible=False)
        clear_template_cache()
        self.message_user(request, f"{n} section(s) hidden.")


@admin.register(ServiceArea)
class ServiceAreaAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("name", "group", "is_active", "sort_order", "project_count")
    list_editable = ("is_active", "sort_order")
    list_filter = ("group", "is_active")
    search_fields = ("name", "group", "blurb")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="projects")
    def project_count(self, obj):
        return obj.projects.count()


@admin.register(FAQ)
class FAQAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("question", "answer_excerpt", "page", "service",
                    "is_published", "sort_order")
    list_editable = ("is_published", "sort_order")
    list_filter = ("is_published", "page", "service")
    search_fields = ("question", "answer")

    @admin.display(description="Answer")
    def answer_excerpt(self, obj):
        """The question is the title, so the answer is the description.

        Truncated rather than shown whole: this column exists so a list of FAQs
        can be scanned, and a column that wraps to four lines stops that.
        """
        if not obj or not obj.pk:
            return ""
        text = " ".join((obj.answer or "").split())
        if not text:
            return format_html('<span class="muted">—</span>')
        if len(text) > 90:
            text = text[:90].rsplit(" ", 1)[0] + "…"
        return format_html('<span class="page-summary">{}</span>', text)


@admin.register(Testimonial)
class TestimonialAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("author", "quote_excerpt", "location", "rating",
                    "is_featured", "is_published", "sort_order")
    list_editable = ("is_featured", "is_published", "sort_order")
    list_filter = ("is_published", "is_featured", "rating")
    search_fields = ("author", "quote", "location")

    @admin.display(description="Quote")
    def quote_excerpt(self, obj):
        if not obj or not obj.pk:
            return ""
        text = " ".join((obj.quote or "").split())
        if len(text) > 100:
            text = text[:100].rsplit(" ", 1)[0] + "…"
        return format_html('<span class="page-summary">{}</span>', text)


@admin.register(Project)
class ProjectAdmin(MediaPickerFieldsMixin, StudioListMixin,
                   admin.ModelAdmin):
    list_display = ("project_thumb", "title", "summary_excerpt", "service",
                    "area", "completed_on", "is_published")
    list_display_links = ("project_thumb", "title")
    media_picker_fields = ("image",)
    list_editable = ("is_published",)
    list_filter = ("is_published", "service", "area")
    search_fields = ("title", "summary", "image")
    date_hierarchy = "completed_on"
    readonly_fields = ("image_reference",)

    @admin.display(description="")
    def project_thumb(self, obj):
        # `image` is a CharField holding a URL or a path into the repo's
        # static files, so a request for a stored upload and a request for a
        # captured static path are different URLs. Only build an <img> when the
        # value actually points at something servable.
        src = self._image_src(obj.image)
        if not src:
            return format_html('<span class="muted">—</span>')
        return format_html(
            '<img src="{}" class="thumb" alt="{}" loading="lazy">',
            src, obj.title or "project")

    @admin.display(description="Summary")
    def summary_excerpt(self, obj):
        """The project's own description, so the list is not just titles."""
        if not obj or not obj.pk:
            return ""
        text = " ".join((obj.summary or "").split())
        if not text:
            return format_html('<span class="muted">—</span>')
        if len(text) > 90:
            text = text[:90].rsplit(" ", 1)[0] + "…"
        return format_html('<span class="page-summary">{}</span>', text)

    @admin.display(description="Stored image reference")
    def image_reference(self, obj):
        if not obj or not obj.pk:
            return ""
        return format_html(
            '<code class="mono" style="background:#f4f4f2;padding:3px 7px;'
            'border-radius:5px;display:inline-block">{}</code>', obj.image or "—")

    @staticmethod
    def _image_src(value):
        """Turn a stored image value into a servable URL, or None.

        Accepts an absolute URL, a root-relative path, or a bare filename that
        resolves under /static/img/ (how the captured rows are stored).
        """
        if not value:
            return None
        value = str(value).strip()
        if value.startswith(("http://", "https://", "/media/")):
            return value
        if value.startswith("/static/"):
            return value
        if value.startswith("/"):
            return None
        return f"/static/img/{value}"


@admin.register(TrustBadge)
class TrustBadgeAdmin(MediaPickerFieldsMixin, StudioListMixin,
                      admin.ModelAdmin):
    list_display = ("badge_thumb", "label", "issuer", "sort_order", "is_published")
    list_display_links = ("badge_thumb", "label")
    media_picker_fields = ("image",)
    list_editable = ("sort_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("label", "issuer", "image")
    readonly_fields = ("image_reference",)

    @admin.display(description="")
    def badge_thumb(self, obj):
        src = ProjectAdmin._image_src(obj.image)
        if not src:
            return format_html('<span class="muted">—</span>')
        return format_html(
            '<img src="{}" class="thumb" alt="{}" loading="lazy">',
            src, obj.label or "badge")

    @admin.display(description="Stored image reference")
    def image_reference(self, obj):
        if not obj or not obj.pk:
            return ""
        return format_html(
            '<code class="mono" style="background:#f4f4f2;padding:3px 7px;'
            'border-radius:5px;display:inline-block">{}</code>', obj.image or "—")


@admin.register(SiteSetting)
class SiteSettingAdmin(StudioListMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        # Singleton: never let a second row appear.
        obj.pk = 1
        clear_template_cache()
        super().save_model(request, obj, form, change)


class MediaItemInline(admin.TabularInline):
    """Quick-add images while editing a page, without leaving the screen."""

    model = MediaItem
    extra = 1
    fields = ("image", "title", "alt_text", "is_published")
    readonly_fields = ("path_hint",)
    verbose_name = "Image"
    verbose_name_plural = "Images"

    def path_hint(self, obj):
        if not obj or not obj.pk:
            return ""
        return obj.public_path

    path_hint.short_description = "Paste this path"


@admin.register(MediaItem)
class MediaItemAdmin(StudioListMixin, admin.ModelAdmin):
    """Image library: upload, preview, publish, retire, delete.

    Uploaded files land in MEDIA_ROOT, which on a free Render service is
    ephemeral. The dashboard shows that warning on the add form so nobody
    assumes an upload is permanent until durable storage is configured.
    """

    list_display = (
        "thumb", "title", "filename", "path_display", "is_published",
        "size_display", "created_at",
    )
    list_display_links = ("thumb", "title")
    list_editable = ("is_published",)
    list_filter = ("is_published", "created_at")
    search_fields = ("title", "alt_text", "image")
    readonly_fields = ("path_display", "size_display", "used_by", "created_at",
                       "updated_at")
    ordering = ("-created_at",)
    actions = ("action_unpublish", "action_publish")
    fieldsets = (
        (None, {"fields": ("image", "title", "alt_text", "is_published")}),
        ("Details", {
            "classes": ("collapse",),
            "fields": ("path_display", "size_display", "used_by",
                       "created_at", "updated_at"),
        }),
    )

    @admin.display(description="Preview")
    def thumb(self, obj):
        if not obj.image:
            return "-"
        return format_html(
            '<img src="{}" class="thumb" alt="{}" loading="lazy">',
            obj.image.url, obj.alt_text or obj.title or "image preview")

    @admin.display(description="Path to paste")
    def path_display(self, obj):
        if not obj or not obj.pk:
            return ""
        return format_html(
            '<code class="mono" style="background:#f4f4f2;padding:3px 7px;'
            'border-radius:5px;display:inline-block">{}</code>', obj.public_path)

    @admin.display(description="Used by")
    def used_by(self, obj):
        """The content rows that currently point at this image.

        The references are paths in text fields, so the database cannot stop a
        deletion that breaks a live page. Listing them is what makes "retire
        this" and "delete this" distinguishable at the moment of the click.
        """
        if not obj or not obj.pk:
            return ""
        rows = obj.references()
        if not rows:
            return format_html('<span class="muted">{}</span>',
                               _("Not referenced yet."))
        items = []
        for row in rows:
            text = format_html("{}: {}", row["model"], row["label"])
            items.append(
                format_html('<li><a href="{}">{}</a></li>', row["url"], text)
                if row["url"] else format_html("<li>{}</li>", text)
            )
        # Each item is already escaped by format_html, so joining them is the
        # one place that must not escape a second time.
        return format_html('<ul class="used-by-list">{}</ul>',
                           mark_safe("".join(items)))

    @admin.display(description="Size", ordering="image")
    def size_display(self, obj):
        if not obj or not obj.pk:
            return ""
        size = obj.size_display
        if not size:
            return "-"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @admin.action(description="Unpublish selected images")
    def action_unpublish(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f"{updated} image(s) unpublished.")

    @admin.action(description="Publish selected images")
    def action_publish(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(request, f"{updated} image(s) published.")

    def add_view(self, request, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["storage_is_ephemeral"] = self._storage_is_ephemeral()
        return super().add_view(request, form_url, extra_context)

    def _storage_is_ephemeral(self):
        """True when MEDIA_ROOT is on a platform filesystem that is wiped.

        Render's free tier (and most container hosts) keep the container
        filesystem only for the life of the instance. Detected by flag rather
        than by platform sniffing so the warning can be forced in tests.
        """
        import os
        return bool(
            os.environ.get("RENDER")
            or os.environ.get("DYNO")
            or os.environ.get("STUDIO_EPHEMERAL_MEDIA")
        )
