"""Section render registry: the extension point for structured editing.

Maps a `Section.type` to a template that renders it from structured fields
rather than from stored HTML. Registering, say, `"faq" -> "...faq.html"` makes
FAQ sections render from `FAQ` rows while every other section keeps its exact
captured markup.

The registry is empty on purpose
-------------------------------
`SECTION_TEMPLATES` ships empty, and that is load-bearing, not an oversight.

`content/templates/content/sections/` contains fourteen templates named after
section types. They are reference sketches, not working components: every one
of them reads fields the `Section` model does not have (`section.headline`,
`section.alignment`, `section.eyebrow`, `section.submit_label`, ...), and
`Section.content_body` is a plain text field that is empty for all 58 captured
sections. So none of them have anything to render from.

If those mappings were registered, the 19 `hero` sections would render
`<h1 class="hero-headline"></h1>` -- an empty H1 on all nineteen pages, and the
single most damaging SEO regression this project could ship. The failure would
be silent: the page still returns 200, still has a title, still looks like a
page. Only the content is gone.

So the registry is opt-in and starts empty. `template_for()` returns None for
every type until somebody registers one, which routes the section to
`RAW_SECTION` and preserves the captured markup byte for byte. `SectionRegistryTests`
asserts the empty default, so registering a type is a deliberate, visible act.

Registering a type is only safe once the model can supply its fields. The
precondition is a passing test for that template, not the template file.
"""
from django.template.loader import select_template

RAW_SECTION = "content/sections/_raw.html"

#: type -> template path. Empty by default; see the module docstring.
SECTION_TEMPLATES = {}


def template_for(section_type):
    """Return the template path registered for a type, or None for raw HTML."""
    return SECTION_TEMPLATES.get(section_type)


def register(section_type, template_path):
    """Register a structured template for a section type."""
    SECTION_TEMPLATES[section_type] = template_path
    return template_path


def unregister(section_type):
    """Remove a registration, returning sections of that type to raw HTML."""
    return SECTION_TEMPLATES.pop(section_type, None)


def get_section_template(section):
    """Resolve the template for a section instance, falling back to raw HTML."""
    path = template_for(section.type)
    if not path:
        return select_template([RAW_SECTION])
    return select_template([path, RAW_SECTION])
