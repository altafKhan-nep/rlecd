"""CRIM interface for all frontend content.

Sections are edited inline on their page, so the common case — change this
heading, reorder these blocks, hide that one — is a single screen. The raw HTML
box is always available because captured sections are the mirror's own markup.
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from content.models import (
    FAQ, MediaItem, Page, Project, Section, ServiceArea, SiteSetting,
    Testimonial, TrustBadge,
)
from content.render import clear_template_cache
from main.listview import StudioListMixin


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0
    ordering = ["position"]
    readonly_fields = ("preview",)
    fieldsets = (
        (None, {
            "fields": ("position", "label", "type", "is_visible", "preview"),
        }),
        ("HTML", {
            "fields": ("content_html",),
            "description": (
                "Rendered exactly as stored. Django tags are available: "
                "<code>{% static 'img/x.jpg' %}</code>, "
                "<code>{% url 'contact' %}</code>, "
                "<code>{% csrf_token %}</code>."
            ),
        }),
        ("Structure", {
            "classes": ("collapse",),
            "fields": ("is_locked",),
        }),
    )

    def preview(self, obj):
        if not obj.pk:
            return "-"
        return format_html(
            '<div style="max-height:11em;overflow:auto;border:1px solid #ccc;'
            'padding:6px;font-size:11px;background:#fafafa">{}</div>',
            obj.content_html[:600])

    preview.short_description = "Preview"


@admin.register(Page)
class PageAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("path", "title", "is_published", "section_count",
                    "locked_count", "updated_at")
    list_display_links = ("path",)
    list_filter = ("is_published", "show_in_menu", "nav_variant", "footer_variant")
    search_fields = ("path", "title", "seo_title", "seo_description")
    ordering = ("path",)
    readonly_fields = ("created_at", "updated_at", "preview_link")
    inlines = [SectionInline]
    fieldsets = (
        (None, {"fields": ("title", "slug", "path", "is_published",
                           "show_in_menu", "sort_order")}),
        ("Page content", {
            "fields": ("preview_link",),
            "description": "Sections are listed below, in render order.",
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

    list_display = ("page", "position", "label", "type", "is_visible",
                    "is_locked", "size")
    list_filter = ("type", "is_visible", "is_locked", "page")
    list_editable = ("is_visible",)
    search_fields = ("label", "content_html", "key")
    ordering = ("page", "position")
    # key is system-managed so re-imports can match sections; page and position
    # stay editable so a section can be added to or moved within a page.
    readonly_fields = ("key", "preview")
    fields = ("page", "key", "position", "label", "type", "is_visible",
              "is_locked", "content_html", "preview")
    actions = ["make_visible", "make_hidden"]

    @admin.display(description="size")
    def size(self, obj):
        return f"{len(obj.content_html or ''):,} B"

    def preview(self, obj):
        if not obj.pk:
            return "-"
        return format_html(
            '<pre style="max-height:16em;overflow:auto;border:1px solid #ccc;'
            'padding:6px;font-size:11px;background:#fafafa">{}</pre>',
            obj.content_html[:2000])

    preview.short_description = "Preview"

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
    list_display = ("question", "page", "service", "is_published", "sort_order")
    list_editable = ("is_published", "sort_order")
    list_filter = ("is_published", "page", "service")
    search_fields = ("question", "answer")


@admin.register(Testimonial)
class TestimonialAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("author", "location", "rating", "is_featured",
                    "is_published", "sort_order")
    list_editable = ("is_featured", "is_published", "sort_order")
    list_filter = ("is_published", "is_featured", "rating")
    search_fields = ("author", "quote", "location")


@admin.register(Project)
class ProjectAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("project_thumb", "title", "service", "area", "completed_on",
                    "is_published")
    list_display_links = ("project_thumb", "title")
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
class TrustBadgeAdmin(StudioListMixin, admin.ModelAdmin):
    list_display = ("badge_thumb", "label", "issuer", "sort_order", "is_published")
    list_display_links = ("badge_thumb", "label")
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
    readonly_fields = ("path_display", "size_display", "created_at", "updated_at")
    ordering = ("-created_at",)
    actions = ("action_unpublish", "action_publish")
    fieldsets = (
        (None, {"fields": ("image", "title", "alt_text", "is_published")}),
        ("Details", {
            "classes": ("collapse",),
            "fields": ("path_display", "size_display", "created_at", "updated_at"),
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
