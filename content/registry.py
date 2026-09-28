"""Section render registry.

Maps a `Section.type` to the template used to render it. Every currently
imported section renders from its stored `content_html`, because that HTML is
what the mirror check compares against — substituting a structured template
here would change the output.

This is the extension point for structured editing. Registering, say,
`"faq" -> "content/sections/faq.html"` makes new FAQ sections render from
`FAQ` rows instead of raw HTML, while captured sections keep their exact
markup until they are explicitly re-saved as structured content.
"""
from django.template.loader import select_template

RAW_SECTION = "content/sections/_raw.html"

#: type -> template path, consulted in order. Empty means "use stored HTML".
SECTION_TEMPLATES = {}


def template_for(section_type):
    """Return the template path for a section type, or None for raw HTML."""
    return SECTION_TEMPLATES.get(section_type)


def register(section_type, template_path):
    """Register a structured template for a section type."""
    SECTION_TEMPLATES[section_type] = template_path
    return template_path


def get_section_template(section):
    """Resolve the template for a section instance, falling back to raw HTML."""
    path = template_for(section.type)
    if not path:
        return select_template([RAW_SECTION])
    return select_template([path, RAW_SECTION])
