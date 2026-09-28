"""Import helpers: load the captured frontend templates into database rows.

The page templates in content/source_templates/main/ are the markup the mirror
build produced from the live site. This module reads their Django blocks and
turns them into Page and Section rows, so the database becomes a faithful copy
of the already-verified markup rather than a re-derivation of the live HTML.

Everything is written to be *lossless*: the section splitter cuts only at
top-level HTML boundaries and carries the inter-node text with each chunk, so
re-joining the sections reproduces the captured body byte for byte. That is what
allows a database-backed page to still match the live site exactly, and it is
asserted by content.tests.MirrorRegressionTests.
"""
import re

from django.utils.text import slugify

TEMPLATE_SPECS = {
    "index": ("/", "Home"),
    "about": ("/about/", "About"),
    "contact": ("/contact/", "Contact"),
    "areas_we_serve": ("/areas-we-serve/", "Areas We Serve"),
    "kitchen": ("/kitchen-remodeling/", "Kitchen Remodeling"),
    "bathroom": ("/bathroom-remodeling/", "Bathroom Remodeling"),
    "basement": ("/basement-finishing/", "Basement Finishing"),
    "additions": ("/home-additions/", "Home Additions"),
    "painting": ("/painting/", "Painting"),
    "home_improvement": ("/home-improvement/", "Home Improvement"),
    "patios_decks": ("/patios-decks/", "Patios & Decks"),
    "cabinets": ("/cabinets/", "Cabinets"),
    "woodworking": ("/woodworking/", "Woodworking"),
    "hardscaping": ("/hardscaping/", "Hardscaping"),
    "walkways": ("/walkway-designs/", "Walkway Designs"),
    "pergolas": ("/pergolas/", "Pergolas"),
    "lead_removal": ("/lead-removal/", "Lead Removal"),
    "sheds": ("/shed-builder/", "Shed Builder"),
    "lead_renovator": ("/lead-renovator/", "Lead Renovator"),
}

_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>")
_VOID = {"img", "br", "hr", "input", "meta", "link", "source", "area", "base",
         "col", "embed", "param", "track", "wbr"}


def top_level_spans(fragment):
    """Byte spans of a fragment's top-level nodes."""
    spans, depth, start = [], 0, None
    for t in _TAG.finditer(fragment):
        closing, name, selfc = t.group(1), t.group(2).lower(), t.group(4)
        if selfc or name in _VOID:
            if depth == 0:
                spans.append((t.start(), t.end()))
            continue
        if closing:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, t.end()))
                start = None
        else:
            if depth == 0:
                start = t.start()
            depth += 1
    return spans


def chunk_lossless(fragment):
    """Split a fragment into ordered chunks that rejoin byte-identically.

    Each chunk owns the text that precedes its node, so whitespace and comments
    between elements survive the round trip.
    """
    spans = top_level_spans(fragment)
    if not spans:
        return [fragment] if fragment else []
    chunks, prev = [], 0
    for start, end in spans:
        chunks.append(fragment[prev:end])
        prev = end
    if prev < len(fragment):
        chunks.append(fragment[prev:])
    return chunks


def leading_tag(chunk):
    m = re.search(r"<([a-zA-Z][\w-]*)", chunk)
    return m.group(1).lower() if m else ""


def infer_type(chunk):
    """Heuristic CRM label. Never changes what is rendered."""
    body = chunk.lower()
    if leading_tag(chunk) == "style":
        return "style"
    if "<form" in body:
        return "form"
    if 'class="hero' in body or "<h1" in body:
        return "hero"
    if "<img" in body and ("gallery" in body or "slider" in body or "swiper" in body):
        return "gallery"
    if "faq" in body or "accordion" in body:
        return "faq"
    if "testimonial" in body or "review" in body:
        return "testimonials"
    if leading_tag(chunk) == "main":
        return "hero"
    if "<h2" in body or "<h3" in body:
        return "text"
    return "html"


# --- reading the generated templates -----------------------------------
# Capture deliberately reads the templates that are already rendering, rather
# than re-deriving content from the cached live HTML. The templates are the
# verified ground truth, so this cannot drift from what the mirror check
# compares against, and `{% static %}` / `{% url %}` / `{% csrf_token %}` tags
# are carried over already resolved-as-tags rather than re-derived.

_BLOCK = re.compile(
    r"\{%\s*block\s+(?P<name>[\w-]+)\s*%\}(?P<body>.*?)\{%\s*endblock\s*%\}",
    re.S,
)
_INCLUDE = re.compile(r"\{%\s*include\s+[\"'](?P<path>[^\"']+)[\"']\s*%\}")

#: blocks a Page row owns, and the Page field each maps to.
BLOCK_FIELDS = {
    "head_meta": "head_html",
    "nav": "nav_variant",
    "footer": "footer_variant",
    "post_body": "post_variant",
    "main_attrs": "main_attrs",
}


def parse_blocks(template_source):
    """{block name: body} for every {% block %} in a template."""
    return {m.group("name"): m.group("body") for m in _BLOCK.finditer(template_source)}


def included_partial(block_body):
    """The partial a block includes, or '' if the block is empty.

    The path is kept exactly as the template wrote it (e.g.
    "partials/_nav_2.html") because that is what {% include %} resolves.
    """
    m = _INCLUDE.search(block_body or "")
    return m.group("path") if m else ""


def capture_page_spec(template_source, base_blocks, path, title):
    """Turn one page template into Page field values.

    A page template only overrides the blocks that differ from base.html, so any
    block it does not define inherits base's value — resolved here exactly the
    way Django's template inheritance would.
    """
    blocks = parse_blocks(template_source)
    spec = {"path": path, "title": title}

    head = blocks.get("head_meta")
    if head is not None:
        spec["head_html"] = head.strip()
    else:
        spec["head_html"] = (base_blocks.get("head_meta") or "").strip()

    for block, field in BLOCK_FIELDS.items():
        if block in ("head_meta", "main_attrs"):
            continue
        if block in blocks:
            spec[field] = included_partial(blocks[block])
        else:
            spec[field] = included_partial(base_blocks.get(block, ""))

    # base.html renders "<main{% block main_attrs %}>", so a value that carries
    # its own leading space is what produces "<main class=...>". Only trailing
    # whitespace is stripped: leading space is structural, not cosmetic.
    spec["main_attrs"] = (blocks.get("main_attrs") or "").rstrip()
    spec["body"] = (blocks.get("content") or "").strip()
    return spec


# --- driving the import -------------------------------------------------

#: Where the page templates captured from the live site live, most preferred
#: first. They are import input and legacy-fallback markup, not the runtime
#: templates: the running site renders content/page.html from the database.
SOURCE_TEMPLATE_DIRS = ("content/source_templates/main", "main/templates/main")
BASE_TEMPLATE_DIRS = ("main/templates", "content/source_templates")

#: chunks that carry page structure rather than editable copy. Marked locked so
#: the CRM shows a warning instead of letting staff break the page shell.
LOCKED_TAGS = {"style", "main"}


def _first_existing(base_dir, rels, filename):
    for rel in rels:
        candidate = base_dir / rel / filename
        if candidate.exists():
            return candidate
    tried = ", ".join(f"{rel}/{filename}" for rel in rels)
    raise FileNotFoundError(f"not found, tried: {tried}")


def _first_existing_dir(base_dir, rels):
    for rel in rels:
        candidate = base_dir / rel
        if candidate.is_dir():
            return candidate
    tried = ", ".join(rels)
    raise FileNotFoundError(f"no page template directory found, tried: {tried}")


def collect_specs(base_dir):
    """Build one spec dict per page, from the captured templates.

    base.html and the page templates are located independently: base.html is
    the runtime shell and lives in main/templates, while the page templates are
    the captured mirror source and live in content/source_templates/main.
    """
    base_path = _first_existing(base_dir, BASE_TEMPLATE_DIRS, "base.html")
    pages_dir = _first_existing_dir(base_dir, SOURCE_TEMPLATE_DIRS)
    base_blocks = parse_blocks(base_path.read_text(encoding="utf-8"))

    specs = []
    missing = []
    for stem, (path, title) in TEMPLATE_SPECS.items():
        src = pages_dir / f"{stem}.html"
        if not src.exists():
            missing.append(stem)
            continue
        spec = capture_page_spec(
            src.read_text(encoding="utf-8"), base_blocks, path, title)
        spec["slug"] = stem
        specs.append(spec)

    if not specs:
        raise FileNotFoundError(
            f"no page templates matched in {pages_dir}")
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} page template(s) missing in {pages_dir}: "
            f"{', '.join(missing)}")
    return specs


def import_pages(base_dir, *, reset=False, log=None):
    """Create/update Page rows and their sections from the captured templates.

    Returns (created, updated, removed, sections_written). Safe to re-run.
    """
    from content.models import Page, Section
    from django.db import transaction

    specs = collect_specs(base_dir)
    created = updated = removed = written = 0

    with transaction.atomic():
        for spec in specs:
            page, made = Page.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "title": spec["title"],
                    "path": spec["path"],
                    "head_html": spec["head_html"],
                    "main_attrs": spec["main_attrs"],
                    "nav_variant": spec["nav_variant"],
                    "footer_variant": spec["footer_variant"],
                    "post_variant": spec["post_variant"],
                },
            )
            created += made
            updated += not made
            written += _sync_sections(page, spec, reset=reset)

        stale = Page.objects.exclude(slug__in=[s["slug"] for s in specs])
        for page in stale:
            if log:
                log(f"  removing {page.path} (no template)")
            page.delete()
            removed += 1

    return created, updated, removed, written


def _sync_sections(page, spec, reset):
    """Make a page's sections match the captured chunk list."""
    from content.models import Section

    chunks = chunk_lossless(spec["body"])
    if reset:
        page.sections.all().delete()

    existing = {s.key: s for s in page.sections.all()}
    seen = set()

    for i, chunk in enumerate(chunks):
        label = Section.derive_label(chunk, i)
        base_key = slugify(label)[:80] or f"section-{i + 1:02d}"
        key = f"{base_key}-{i + 1:02d}"
        stype = infer_type(chunk)
        locked = leading_tag(chunk) in LOCKED_TAGS
        seen.add(key)

        if key in existing:
            section = existing[key]
            if reset or section.content_html != chunk:
                section.content_html = chunk
                section.label = label
                section.type = stype
                section.position = i
                section.is_locked = locked
                section.save()
        else:
            Section.objects.create(
                page=page, key=key, label=label, type=stype, position=i,
                content_html=chunk, is_locked=locked,
            )

    # Drop sections whose chunk is gone, unless an editor renamed them.
    for key, section in existing.items():
        if key not in seen and not section.is_locked:
            section.delete()

    return len(chunks)
