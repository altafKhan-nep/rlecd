"""Small presentation helpers for the custom admin templates.

Kept as filters rather than logic in the view so rules live in one place and
the dashboard, the sidebar and any list view can share them.
"""
import re

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

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


@register.filter
def get_item(sequence, index):
    """`sequence[index]`, or None if the index is out of range.

    Used to pair a changelist row's cells with their column headers, which are
    two parallel lists in the template context. A template cannot index a list,
    so this is the bridge.
    """
    try:
        return sequence[index]
    except (IndexError, TypeError, KeyError):
        return None


@register.filter
def cell_label(cell, header_text):
    """Add `data-label="<header>"` to a changelist cell, keeping its content.

    Below the 900px breakpoint responsive.css turns each <tr> into a stacked
    card and hides the header row, so each cell has to carry its own label. This
    injects the attribute into the <td> or <th> Django produced rather than
    rebuilding the cell, so list_display_links, list_editable widgets and popup
    behaviour are all left exactly as Django rendered them.

    The header text is stripped of its surrounding whitespace and truncated:
    "Position" is useful, a full sentence is not, and this string is repeated
    once per cell on every row.

    The attribute has to go *inside* the opening tag. Appending it after the
    cell -- which is the obvious way to write this -- closes the cell first and
    leaves the attribute sitting in the row as text, and since the filter runs
    once per cell per row a changelist then renders a wall of
    `data-label="Image" data-label="path" ...` in place of its contents.
    """
    if not cell:
        return cell
    label = " ".join(str(header_text or "").split())
    if not label:
        return cell
    if len(label) > 24:
        label = label[:24].rsplit(" ", 1)[0] + "…"
    attribute = ' data-label="%s"' % escape(label)
    # Only the first tag, and only an opening <td>/<th>: the cell is a
    # complete element, so its own attributes are already closed.
    return mark_safe(re.sub(
        r"^\s*<(t[dh])\b",
        lambda m: "<%s%s" % (m.group(1), attribute),
        str(cell), count=1))
