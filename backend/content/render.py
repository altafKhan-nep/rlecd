"""Rendering for database-backed pages.

A page's body is a list of section rows; rendering is an ordered concatenation
of their rendered HTML. Because the importer split the original markup only at
lossless boundaries, that concatenation reproduces the pre-import output
exactly, which is what keeps the mirror regression suite meaningful.

Trust boundary
--------------
Section HTML is compiled by the project's template engine so that
`{% static %}`, `{% url %}` and `{% csrf_token %}` keep working in stored
content. That makes content-editing accounts equivalent in power to template
authors: a section cannot call arbitrary Python, but it can emit arbitrary
markup and read any template context variable. Sections are therefore only
editable by staff you would already trust to change templates. When editorial
roles are added, gate Section, Page, Testimonial, FAQ and Project change
permissions on those roles rather than on the default "staff" bit.
"""
from functools import lru_cache

import re

from django.conf import settings
from django.template import Context, RequestContext, TemplateSyntaxError, engines
from django.utils.html import escape


class ContentRenderError(Exception):
    """A stored section could not be compiled or rendered."""


@lru_cache(maxsize=1)
def _engine():
    """The project's configured template engine.

    Deliberately not `django.template.Template`: that builds a fresh engine
    which ignores the `builtins` and libraries configured in settings, so
    `{% static %}` inside stored content would fail to compile.
    """
    return engines["django"].engine


@lru_cache(maxsize=4096)
def _compile(source):
    return _engine().from_string(source)


@lru_cache(maxsize=1)
def _templates_enabled():
    return getattr(settings, "CONTENT_ALLOW_TEMPLATES", True)


def clear_template_cache():
    """Drop compiled templates.

    Call after bulk section edits, otherwise a cached compilation of the old
    markup keeps being served for up to `maxsize` distinct sources.
    """
    _compile.cache_clear()


def build_context(request=None, extra=None):
    """One context for a whole page.

    Sections are rendered in a loop, so building the context per section would
    re-run every context processor once per section — 58 times for this site.
    A RequestContext is required for `{% csrf_token %}` and for `user`/`perms`.
    """
    data = dict(extra or {})
    if request is not None:
        return RequestContext(request, data)
    return Context(data)


def render_source(source, context, label):
    """Compile and render one chunk of stored template source."""
    if not _templates_enabled():
        # Escape mode: content is treated as literal text, not markup.
        return escape(source or "")
    try:
        return _compile(source or "").render(context)
    except TemplateSyntaxError as exc:
        raise ContentRenderError(f"{label}: {exc}") from exc


_IMAGE_MARKER = re.compile(r"<!--rlecd-image:(\d+)-->")


def resolve_images(section, source):
    """Put each section's <img> tags back where the importer left markers.

    Substitution happens on the template *source*, before compilation, so the
    `{% static %}` inside a rebuilt tag is resolved by the engine exactly as it
    was in the captured markup.

    A marker with no matching row raises rather than being dropped. Silently
    removing an image would leave a page that looks fine and is missing a
    photo, which is the failure mode this whole change exists to prevent.
    """
    if "rlecd-image:" not in (source or ""):
        return source or ""

    images = {index: image for index, image
              in enumerate(section.images.all(), start=1)}

    def swap(match):
        index = int(match.group(1))
        image = images.get(index)
        if image is None:
            raise ContentRenderError(
                f"section {section.pk} ({section.key}) references image "
                f"{index} but has no such image row -- the image was deleted "
                f"without removing its marker from the content."
            )
        return image.to_html()

    return _IMAGE_MARKER.sub(swap, source)


def render_section(section, context=None, request=None, extra=None):
    """Render a single Section to HTML."""
    ctx = context if context is not None else build_context(request, extra)
    return render_source(
        resolve_images(section, section.render_source()), ctx,
        f"section {section.pk} ({section.key})")


def render_sections(page, request=None, context=None, extra=None):
    """Concatenate a page's visible sections in order."""
    data = dict(extra or {})
    if context is not None:
        data.update({k: v for k, v in context.items() if k not in ("request",)})
    ctx = build_context(request, data)
    out = [
        render_source(resolve_images(s, s.render_source()), ctx,
                      f"section {s.pk} ({s.key}) on page {page.slug}")
        for s in page.visible_sections()
    ]
    return "".join(out)


def render_head(page, request=None, context=None, extra=None):
    """Render a page's stored per-page <head> block."""
    if not page.head_html:
        return ""
    data = dict(extra or {})
    if context is not None:
        data.update({k: v for k, v in context.items() if k != "request"})
    ctx = build_context(request, data)
    return render_source(page.head_html, ctx, f"head_html for {page.slug}")
