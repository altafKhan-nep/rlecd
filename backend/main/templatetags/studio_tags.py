"""Small presentation helpers for the custom admin templates.

Kept as filters rather than logic in the view so rules live in one place and
the dashboard, the sidebar and any list view can share them.
"""
from django import template
from django.utils.html import format_html

from main.icons import icon

register = template.Library()


@register.simple_tag
def studio_icon(name, css_class=""):
    """Inline SVG icon, safe to drop straight into markup."""
    return icon(name, css_class)


@register.simple_tag(takes_context=True)
def nav_item_class(context, url_name):
    """Return 'is-active' when the current URL matches this nav item.

    Keeping the comparison here means the sidebar template does not have to
    repeat `request.resolver_match.url_name ==` for every link, and a renamed
    URL shows up in one place rather than silently losing its highlight.
    """
    match = context.get("request")
    current = getattr(match, "resolver_match", None)
    if current is not None and current.url_name == url_name:
        return " is-active"
    return ""


@register.filter
def status_chip(is_active):
    """'active' / 'inactive' pill class for a boolean flag."""
    return "active" if is_active else "inactive"


@register.filter
def score_tier(score):
    """Map a 0-100 lead score to a colour tier.

    The admin stylesheet defines .s-hi, .s-mid and .s-lo. Without this the chip
    falls back to the untiered base rule and every score looks identical, which
    defeats the point of colouring them.
    """
    try:
        score = int(score)
    except (TypeError, ValueError):
        return "s-lo"
    if score >= 70:
        return "s-hi"
    if score >= 40:
        return "s-mid"
    return "s-lo"


@register.simple_tag
def thumb(url, alt="", css_class="thumb"):
    """A media thumbnail, or an em dash when the row has no image.

    Content models keep their image as a path string (see content.models), and
    a large share of captured rows genuinely have none. Rendering the broken
    <img> the browser would otherwise draw makes a whole column look faulted
    when it is simply empty, so the absence is drawn deliberately.
    """
    if not url:
        return format_html('<span class="thumb thumb-empty" aria-hidden="true">—</span>')
    return format_html(
        '<img src="{}" alt="{}" class="{}" loading="lazy">',
        url, alt, css_class,
    )


@register.filter
def has_key(mapping, key):
    """`{% if d|has_key:"k" %}` -- Django templates cannot index by variable."""
    return bool(mapping) and key in mapping

