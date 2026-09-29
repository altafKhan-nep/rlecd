"""Inline SVG icons for the admin shell.

Kept in Python rather than an icon font or sprite sheet so the admin needs no
extra network request and every glyph inherits `currentColor` from CSS, which
is what lets one rule recolour the whole set.

Names follow the Material Symbols outline set the reference design uses, so
the markup reads the same way the icon looks. Every entry is drawn on a 24x24
grid with a 2px round-joined stroke to match the rest of the shell.
"""
from django.utils.safestring import mark_safe

# `stroke="currentColor"` is repeated as a presentation attribute purely as a
# fallback. Any `stroke` rule in admin.css overrides it, so styled icons are
# unaffected, but an icon dropped into a context with no matching rule still
# inherits the surrounding colour instead of rendering as an invisible shape.
_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
    '{cls}>{body}</svg>'
)

PATHS = {
    # --- navigation ---------------------------------------------------
    "grid": (
        '<rect x="3" y="3" width="7" height="9" rx="1.5"/>'
        '<rect x="14" y="3" width="7" height="5" rx="1.5"/>'
        '<rect x="14" y="12" width="7" height="9" rx="1.5"/>'
        '<rect x="3" y="16" width="7" height="5" rx="1.5"/>'
    ),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
        '<circle cx="9" cy="7" r="4"/>'
        '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
    ),
    "user": (
        '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>'
        '<circle cx="12" cy="7" r="4"/>'
    ),
    # A head with a cog, to set "Users" apart from the plain person used for
    # Contacts. Without the gear the two nav rows are near-identical glyphs.
    "user-cog": (
        '<circle cx="10" cy="7.5" r="3.5"/>'
        '<path d="M3 21v-1.5A5.5 5.5 0 0 1 8.5 14h3"/>'
        '<circle cx="18" cy="18" r="3"/>'
        '<path d="M18 13.8v1.4M18 20.8v1.4M15.2 18h-1.4M22.2 18h-1.4'
        'M16.1 16.1l-1-1M20.9 20.9l-1-1M19.9 16.1l1-1M15.1 20.9l1-1"/>'
    ),
    # Two heads, to mark "Groups" as a collection rather than a single record.
    "users-round": (
        '<circle cx="9" cy="8" r="3.5"/>'
        '<path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
        '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/>'
        '<path d="M17.5 14.2A6.5 6.5 0 0 1 21.5 20"/>'
    ),
    # Theme toggle. Both are always in the DOM and CSS shows one, so these are
    # the "switch to the other theme" glyphs rather than "current theme": a user
    # in dark mode is offered the sun.
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
        'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
    ),
    "moon": (
        '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'
    ),
    # Three upright columns, for the pipeline board. Distinct from "layers"
    # (stacked) and "panel-left" (a collapsed sidebar): this one has to read as
    # a board of stages at a glance.
    "columns": (
        '<rect x="3" y="4" width="5" height="16" rx="1.2"/>'
        '<rect x="9.5" y="4" width="5" height="11" rx="1.2"/>'
        '<rect x="16" y="4" width="5" height="7" rx="1.2"/>'
    ),
    "pencil": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<path d="M10 11v6M14 11v6"/>'
    ),
    "check-square": (
        '<path d="M9 11l3 3L22 4"/>'
        '<path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'
    ),
    "layers": (
        '<rect x="3" y="4" width="18" height="4" rx="1"/>'
        '<rect x="3" y="10" width="18" height="4" rx="1"/>'
        '<rect x="3" y="16" width="18" height="4" rx="1"/>'
    ),
    "tag": (
        '<path d="M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8z"/>'
    ),
    "store": (
        '<path d="M3 9l1.5-5h15L21 9"/>'
        '<path d="M3 9v10a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9"/>'
        '<path d="M3 9h18"/>'
        '<path d="M9 20v-6h6v6"/>'
    ),
    "image": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<circle cx="8.5" cy="8.5" r="1.5"/>'
        '<path d="M21 15l-5-5L5 21"/>'
    ),
    "map-pin": (
        '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z"/>'
        '<circle cx="12" cy="10" r="3"/>'
    ),
    "help-circle": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/>'
        '<path d="M12 17h.01"/>'
    ),
    "quote": (
        '<path d="M7 7h4v4a4 4 0 0 1-4 4"/>'
        '<path d="M15 7h4v4a4 4 0 0 1-4 4"/>'
    ),
    "briefcase": (
        '<rect x="2" y="7" width="20" height="14" rx="2"/>'
        '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>'
    ),
    "shield": (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
    ),
    "cart": (
        '<circle cx="9" cy="20" r="1.5"/>'
        '<circle cx="18" cy="20" r="1.5"/>'
        '<path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.6L21 7H6"/>'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'
    ),
    "bar-chart": (
        '<path d="M12 20V10"/>'
        '<path d="M18 20V4"/>'
        '<path d="M6 20v-4"/>'
    ),

    # --- actions -------------------------------------------------------
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": (
        '<circle cx="11" cy="11" r="8"/>'
        '<path d="M21 21l-4.35-4.35"/>'
    ),
    "bell": (
        '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>'
        '<path d="M13.73 21a2 2 0 0 1-3.46 0"/>'
    ),
    "edit": (
        '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
        '<path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>'
    ),
    "eye": (
        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "chevron-up": '<path d="M18 15l-6-6-6 6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-left": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-right": '<path d="M9 18l6-6-6-6"/>',
    "chevrons-left": '<path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/>',
    "chevrons-right": '<path d="M13 17l5-5-5-5M6 17l5-5-5-5"/>',
    "external-link": (
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
        '<path d="M15 3h6v6"/>'
        '<path d="M10 14L21 3"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'
    ),
    "menu": '<path d="M3 6h18M3 12h18M3 18h18"/>',
    "menu-open": (
        '<path d="M3 6h18M3 12h9M3 18h18"/>'
        '<path d="M21 12l-4-4v8z" fill="currentColor" stroke="none"/>'
    ),
    "panel-left": (
        '<rect x="3" y="3" width="18" height="18" rx="2"/>'
        '<path d="M9 3v18"/>'
    ),
    "filter": '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
    "star": (
        '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.3-6.2 3.3L7 14.2l-5-4.9 6.9-1z"/>'
    ),
    "type": (
        '<path d="M4 7V4h16v3"/>'
        '<path d="M9 20h6"/>'
        '<path d="M12 4v16"/>'
    ),
    "hash": (
        '<path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18"/>'
    ),
    "percent": (
        '<path d="M19 5L5 19"/>'
        '<circle cx="6.5" cy="6.5" r="2.5"/>'
        '<circle cx="17.5" cy="17.5" r="2.5"/>'
    ),
    "key": (
        '<circle cx="7.5" cy="15.5" r="4.5"/>'
        '<path d="M10.7 12.3L21 2M18 5l3 3M15 8l3 3"/>'
    ),
}


def icon(name, extra_class=""):
    """Return inline SVG markup for `name`.

    Unknown names fall back to a neutral dot rather than raising: a missing
    glyph should degrade to something visibly wrong in a template, not take
    down a page an admin is trying to work in.
    """
    body = PATHS.get(name) or '<circle cx="12" cy="12" r="3"/>'
    cls = f' class="{extra_class}"' if extra_class else ""
    return mark_safe(_SVG.format(cls=cls, body=body))
