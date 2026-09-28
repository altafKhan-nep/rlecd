"""CRIM interface for all frontend content.

Sections are edited inline on their page, so the common case — change this
heading, reorder these blocks, hide that one — is a single screen. The raw HTML
box is always available because captured sections are the mirror's own markup.
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from content.models import (
    FAQ, Page, Project, Section, ServiceArea, SiteSetting, Testimonial, TrustBadge,
)
from content.render import clear_template_cache


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
class PageAdmin(admin.ModelAdmin):
    list_display = ("path", "title", "is_published", "section_count",
                    "locked_count", "updated_at", "view_link")
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

    @admin.display(description="view")
    def view_link(self, obj):
        if not obj.pk:
            return "-"
        return format_html('<a href="{}" target="_blank">{}</a>',
                           obj.path, obj.path)

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
class SectionAdmin(admin.ModelAdmin):
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
class ServiceAreaAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "is_active", "sort_order", "project_count")
    list_editable = ("is_active", "sort_order")
    list_filter = ("group", "is_active")
    search_fields = ("name", "group", "blurb")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="projects")
    def project_count(self, obj):
        return obj.projects.count()


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ("question", "page", "service", "is_published", "sort_order")
    list_editable = ("is_published", "sort_order")
    list_filter = ("is_published", "page", "service")
    search_fields = ("question", "answer")


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ("author", "location", "rating", "is_featured",
                    "is_published", "sort_order")
    list_editable = ("is_featured", "is_published", "sort_order")
    list_filter = ("is_published", "is_featured", "rating")
    search_fields = ("author", "quote", "location")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "service", "area", "completed_on", "is_published")
    list_editable = ("is_published",)
    list_filter = ("is_published", "service", "area")
    search_fields = ("title", "summary")
    date_hierarchy = "completed_on"


@admin.register(TrustBadge)
class TrustBadgeAdmin(admin.ModelAdmin):
    list_display = ("label", "issuer", "sort_order", "is_published")
    list_editable = ("sort_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("label", "issuer")


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        # Singleton: never let a second row appear.
        obj.pk = 1
        clear_template_cache()
        super().save_model(request, obj, form, change)
