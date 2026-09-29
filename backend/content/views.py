"""Serving database-backed pages."""
import logging

from django.http import Http404
from django.shortcuts import render

from content.models import Page
from content.render import ContentRenderError

logger = logging.getLogger(__name__)

PAGE_TEMPLATE = "content/page.html"

#: slug -> the hard-coded template built by the original mirror, used only as a
#: fallback when a Page row is missing.
LEGACY_TEMPLATES = {
    "index": "main/index.html",
    "about": "main/about.html",
    "contact": "main/contact.html",
    "areas_we_serve": "main/areas_we_serve.html",
    "kitchen": "main/kitchen.html",
    "bathroom": "main/bathroom.html",
    "basement": "main/basement.html",
    "additions": "main/additions.html",
    "painting": "main/painting.html",
    "home_improvement": "main/home_improvement.html",
    "patios_decks": "main/patios_decks.html",
    "cabinets": "main/cabinets.html",
    "woodworking": "main/woodworking.html",
    "hardscaping": "main/hardscaping.html",
    "walkways": "main/walkways.html",
    "pergolas": "main/pergolas.html",
    "lead_removal": "main/lead_removal.html",
    "sheds": "main/sheds.html",
    "lead_renovator": "main/lead_renovator.html",
}


def get_page(slug):
    """Fetch a published Page by slug, or None."""
    return Page.objects.filter(slug=slug, is_published=True).first()


def get_page_or_404(slug):
    page = get_page(slug)
    if page is None:
        raise Http404(f"No published page for slug {slug!r}")
    return page


def render_page(request, slug, **context):
    """Render a public page from its database row.

    Falls back to the mirror template when the row is missing *or* when it has
    nothing in it, so a partially imported database degrades to the static
    mirror instead of a wall of 404s. The two cases are distinguished only for
    the log line, because they need different fixes: a missing row means
    `manage.py capture_content` has not run, while a hollow row means it ran
    and captured nothing for this URL.

    A hollow row is the more dangerous of the two. Because the navbar, footer
    and head all live on the Page row, rendering it emits an empty <main> with
    no way to navigate away, so a visitor who clicks a nav link to About lands
    on a dead end that still returns 200.

    A ContentRenderError is logged loudly and re-raised: silently serving a
    half-rendered page would hide content errors from editors.
    """
    page = get_page(slug)
    legacy = LEGACY_TEMPLATES.get(slug)
    if page is None:
        logger.warning(
            "No published Page row for slug=%r; serving the static mirror "
            "template %r instead. Run `manage.py capture_content`.",
            slug, legacy)
    elif not page.has_content():
        logger.warning(
            "Page slug=%r is published but has no content; serving the static "
            "mirror template %r instead. The row exists but nothing was "
            "captured into it, so the page would otherwise render as an empty "
            "<main> with no navigation.",
            slug, legacy)
        page = None

    if page is None:
        if legacy:
            return render(request, legacy, context)
        raise Http404(f"No published page for slug {slug!r}")

    try:
        return render(request, PAGE_TEMPLATE, {"page": page, **context})
    except ContentRenderError:
        logger.exception("Content render failed for page slug=%r", slug)
        raise
