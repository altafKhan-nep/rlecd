"""Template tags for database-backed pages."""
from django import template
from django.template.loader import get_template
from django.utils.safestring import mark_safe

from content import render

register = template.Library()


@register.simple_tag(takes_context=True)
def page_head(context, page):
    """Render a page's stored per-page <head> block."""
    if not page or not page.head_html:
        return ""
    return mark_safe(render.render_head(page, request=context.get("request"),
                                        extra=context.flatten()))


@register.simple_tag(takes_context=True)
def page_sections(context, page):
    """Render a page's visible sections, in order, as a single HTML string."""
    if not page:
        return ""
    return mark_safe(render.render_sections(page, request=context.get("request"),
                                            extra=context.flatten()))


@register.simple_tag(takes_context=True)
def section_html(context, section):
    """Render a single section. Used by the raw section template."""
    return mark_safe(render.render_section(section, request=context.get("request"),
                                           extra=context.flatten()))


@register.simple_tag
def simple_include(path):
    """`{% include %}` that tolerates an empty path.

    A Page row may legitimately have no post_body or footer variant, and
    `{% include "" %}` raises TemplateDoesNotExist.
    """
    if not path:
        return ""
    return mark_safe(get_template(path).render())


@register.filter
def field_label(value, fallback=""):
    """Human label for a section's editorial type."""
    try:
        return dict(type(value)._field_map)[value].label
    except Exception:
        return value or fallback
