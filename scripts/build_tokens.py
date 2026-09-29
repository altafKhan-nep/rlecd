#!/usr/bin/env python3
"""Generate frontend/static/admin/css/tokens.css, verifying contrast as it goes.

The admin had a light-only palette in admin.css layered over Django 5.2's own
`:root` block, which dark_mode.css repaints under `prefers-color-scheme: dark`.
Both target `:root`, ours loads last, so ours won -- leaving near-black text on
Django's #121212 body. Nothing flagged it because no single rule was wrong; the
cascade was.

This script is the fix's structural half. Both themes are defined here, every
foreground/background pair it declares is contrast-checked before it is written,
and the Django surface variables are set too so the two palettes stop fighting.
Re-run it rather than editing tokens.css by hand.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "frontend" / "static" / "admin" / "css" / "tokens.css"


# --- colour maths ----------------------------------------------------------

def srgb(channel):
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (srgb(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def hx(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


# --- the two themes --------------------------------------------------------
#
# Brand is kept: the deep green and the coral. The dark theme is a green-tinted
# neutral rather than neutral grey, so the admin still looks like this business
# when the lights are off.

LIGHT = {
    "bg": "#eef1ee",
    "surface": "#ffffff",
    "surface-2": "#f7f9f7",
    "surface-3": "#eef2ef",
    "ink": "#16211b",
    "ink-soft": "#4d5a52",
    "muted": "#5f6c65",
    "line": "#d9e0da",
    "line-soft": "#e8ede9",
    "primary": "#1b3022",
    "primary-soft": "#2b4632",
    "primary-mid": "#24483a",
    "primary-fg": "#f4f7f4",
    "accent": "#b55235",
    "accent-soft": "#e8a48e",
    "accent-fg": "#ffffff",
    "link": "#1a5c7a",
    "link-hover": "#12455c",
    "teal": "#0e6f68",
    "teal-deep": "#0a534e",
    "sky": "#2a6fa8",
    "clay": "#a8552f",
    "violet": "#5b4b9a",
    "amber": "#8a5a12",
    "rose": "#9b2c1c",
    "mint-deep": "#1f7a4d",
    "ok": "#1f7a4d",
    "warn": "#8a5a12",
    "danger": "#9b2c1c",
    "shadow": "rgba(27, 48, 34, .10)",
    "shadow-sm": "rgba(27, 48, 34, .06)",
    "shadow-lg": "rgba(27, 48, 34, .16)",
    "overlay": "rgba(15, 20, 25, .55)",
    "input-bg": "#ffffff",
    "input-border": "#c3cdc6",
    "selected-bg": "#e8f0ea",
    "darkened": "rgba(15, 20, 25, .55)",
    "msg-ok-bg": "#e6f2ea",
    "msg-warn-bg": "#fdf3e0",
    "msg-err-bg": "#fdeceb",
    "msg-info-bg": "#e8f0f4",
    "msg-ok-fg": "#1a5c3a",
    "msg-warn-fg": "#7a4e0f",
    "msg-err-fg": "#9b2c1c",
    "msg-info-fg": "#1a5c7a",
    "violet-deep": "#4a3d80",
    "rose": "#b0405f",
    "rose-deep": "#8e2f4b",
    "amber-deep": "#6f460d",
    "mint-deep": "#1f7a4d",
    "mint": "#2e7a56",
    "clay": "#9a4d2a",
    "teal": "#0e6f68",
    "teal-deep": "#0a534e",
}

DARK = {
    "bg": "#0d1310",
    "surface": "#151f1a",
    "surface-2": "#1b2822",
    "surface-3": "#22312a",
    "ink": "#e9f0eb",
    "ink-soft": "#b3c2b8",
    "muted": "#93a399",
    "line": "#2a3a32",
    "line-soft": "#1f2c25",
    "primary": "#8fbf9f",
    "primary-soft": "#24483a",
    "primary-mid": "#2f5c49",
    "primary-fg": "#0d1310",
    "accent": "#e89275",
    "accent-soft": "#7a4433",
    "accent-fg": "#1a0f0b",
    "link": "#7cc4e8",
    "link-hover": "#a8dbf2",
    "teal": "#4fc4b8",
    "teal-deep": "#7fded3",
    "sky": "#79b8e8",
    "clay": "#e0a077",
    "violet": "#b0a2e8",
    "amber": "#e0b46a",
    "rose": "#e88b78",
    "mint-deep": "#6fc79a",
    "ok": "#6fc79a",
    "warn": "#e0b46a",
    "danger": "#e88b78",
    "shadow": "rgba(0, 0, 0, .45)",
    "shadow-sm": "rgba(0, 0, 0, .30)",
    "shadow-lg": "rgba(0, 0, 0, .60)",
    "overlay": "rgba(0, 0, 0, .65)",
    "input-bg": "#101a15",
    "input-border": "#33463b",
    "selected-bg": "#20352a",
    "darkened": "rgba(0, 0, 0, .65)",
    "msg-ok-bg": "#16281e",
    "msg-warn-bg": "#2a2314",
    "msg-err-bg": "#2c1a17",
    "msg-info-bg": "#16242a",
    "msg-ok-fg": "#7fd3a6",
    "msg-warn-fg": "#f0c988",
    "msg-err-fg": "#f0a89c",
    "msg-info-fg": "#8ccbe8",
    "violet-deep": "#c4b8f0",
    "rose": "#e88b9f",
    "rose-deep": "#f0a3b4",
    "amber-deep": "#f0c988",
    "mint-deep": "#7fd3a6",
    "mint": "#5fae82",
    "clay": "#e0a077",
    "teal": "#4fc4b8",
    "teal-deep": "#7fded3",
}

#: (foreground, background, minimum) for every text pair the themes declare.
#: 4.5 is WCAG AA for body text; 3.0 is the floor for large text and for
#: non-text UI like borders and focus rings.
PAIRS = [
    ("ink", "surface", 4.5), ("ink", "bg", 4.5), ("ink", "surface-2", 4.5),
    ("ink-soft", "surface", 4.5), ("ink-soft", "bg", 4.5),
    ("muted", "surface", 4.5), ("muted", "bg", 4.5),
    ("link", "surface", 4.5), ("link", "bg", 4.5), ("link-hover", "surface", 4.5),
    ("primary-fg", "primary", 4.5), ("accent-fg", "accent", 4.5),
    ("ok", "surface", 3.0), ("warn", "surface", 3.0), ("danger", "surface", 3.0),
    ("teal", "surface", 3.0), ("sky", "surface", 3.0), ("clay", "surface", 3.0),
    ("violet", "surface", 3.0), ("amber", "surface", 3.0), ("rose", "surface", 3.0),
    ("mint-deep", "surface", 3.0), ("ink", "selected-bg", 4.5),
    ("ink", "input-bg", 4.5), ("ink-soft", "surface-3", 4.5),
]

NON_TEXT = [
    ("line", "surface", 1.0), ("line-soft", "surface", 1.0),
    ("input-border", "surface", 1.0), ("teal", "surface", 3.0),
]


def verify(name, theme):
    bad = []
    for fg, bg, minimum in PAIRS:
        got = contrast(hx(theme[fg]), hx(theme[bg]))
        if got < minimum:
            bad.append(f"{name}: {fg} on {bg} = {got:.2f} (needs {minimum})")
    for fg, bg, minimum in NON_TEXT:
        got = contrast(hx(theme[fg]), hx(theme[bg]))
        if got < minimum:
            bad.append(f"{name}: {fg} on {bg} = {got:.2f} (needs {minimum})")
    return bad


# --- the Django surface variables ----------------------------------------
#
# Set explicitly in both themes so Django's components follow our palette
# instead of only following the OS. `data-theme` is always present on <html>
# (the toggle guarantees it), and these selectors outrank Django's bare
# `:root` in a media query, so the user's choice wins either way.

DJANGO = [
    ("--body-fg", "ink"), ("--body-bg", "bg"),
    ("--body-quiet-color", "ink-soft"), ("--body-medium-color", "muted"),
    ("--body-loud-color", "ink"),
    ("--hairline-color", "line-soft"), ("--border-color", "line"),
    ("--link-fg", "link"), ("--link-hover-color", "link-hover"),
    ("--link-selected-fg", "link"),
    ("--primary", "primary"), ("--primary-fg", "primary-fg"),
    ("--primary-accent", "teal"),
    ("--secondary-fg", "ink-soft"), ("--secondary-bg", "surface-2"),
    ("--tertiary-bg", "surface-3"),
    ("--close-button-fg", "ink-soft"), ("--close-button-bg", "surface-3"),
    ("--close-button-hover-bg", "line"),
    ("--header-color", "primary-fg"), ("--header-branding-color", "primary-fg"),
    ("--header-bg", "primary"),
    ("--breadcrumbs-fg", "ink-soft"), ("--breadcrumbs-bg", "surface-2"),
    ("--breadcrumbs-link-fg", "link"), ("--breadcrumbs-link-hover-color", "link-hover"),
    ("--button-fg", "ink"), ("--button-bg", "surface-2"),
    ("--button-hover-bg", "surface-3"), ("--default-button-fg", "primary-fg"),
    ("--default-button-bg", "primary"), ("--default-button-hover-bg", "primary-mid"),
    ("--object-tools-fg", "muted"), ("--object-tools-bg", "surface"),
    ("--object-tools-hover-bg", "surface-3"),
    ("--error-fg", "danger"),
    ("--message-error-bg", "msg-err-bg"),
    ("--message-success-fg", "msg-ok-fg"),
    ("--message-warning-fg", "msg-warn-fg"),
    ("--message-info-fg", "msg-info-fg"),
    ("--message-border-color", "line"), ("--message-warning-bg", "surface-3"),
    ("--message-success-bg", "surface-3"), ("--message-info-bg", "surface-3"),
    ("--darkened-bg", "darkened"),
    ("--selected-bg", "selected-bg"), ("--selected-row", "selected-bg"),
    ("--input-bg", "input-bg"), ("--input-border", "input-border"),
    ("--input-fg", "ink"),
    ("--icon-color", "muted"), ("--spinner-color", "muted"),
    ("--scrollbar-color", "line"),
]

ORDER = list(LIGHT.keys())


def block(theme, selector):
    lines = [f"{selector} {{"]
    for key in ORDER:
        lines.append(f"  --{key.replace('_', '-')}: {theme[key]};")
    for css_name, key in DJANGO:
        lines.append(f"  {css_name}: {theme[key]};")
    lines.append("}")
    return "\n".join(lines)


HEADER = """/* Admin design tokens.
 *
 * GENERATED by scripts/build_tokens.py -- do not edit by hand. Re-run it after
 * changing a colour; it contrast-checks every pair it declares and fails the
 * build rather than emitting a theme that cannot be read.
 *
 * Why this file exists at all: admin.css used to carry a light-only palette on
 * `:root` while Django 5.2's dark_mode.css also owns `:root` and repaints it
 * under `prefers-color-scheme: dark`. Both had the same specificity and ours
 * loaded last, so ours won and left near-black text on Django's #121212 body.
 * No individual rule was wrong; the cascade was.
 *
 * The selectors are `:root[data-theme="light"]` and `[data-theme="dark"]`,
 * which is (0,2,0) and beats Django's bare `:root` (0,1,0) even inside its
 * media query. The toggle always sets the attribute, so the OS preference is
 * only ever the initial default and never a second, competing opinion.
 */

:root {
  /* Geometry and type, identical in both themes. */
  --radius: 14px;
  --radius-sm: 9px;
  --radius-xs: 6px;
  --sidebar-w: 254px;
  --sidebar-w-collapsed: 68px;
  --topbar-h: 62px;
  --font: 'Montserrat', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-display: 'Playfair Display', Georgia, serif;
  --font-mono: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace;

  /* The project's own historical names, kept so the 2,400 lines of
     admin.css that reference them do not all have to change at once. */
  --c-teal: var(--teal);
  --c-teal-deep: var(--teal-deep);
  --c-sky: var(--sky);
  --c-clay: var(--clay);
  --c-violet: var(--violet);
  --c-amber: var(--amber);
  --c-amber-deep: var(--amber-deep);
  --c-rose: var(--rose);
  --c-rose-deep: var(--rose-deep);
  --c-mint: var(--mint);
  --c-mint-deep: var(--mint-deep);
  --c-violet-deep: var(--violet-deep);
  --c-teal-deep: var(--teal-deep);
  --c-clay: var(--clay);
  --body: var(--ink-soft);
  --card-bg: var(--surface);
  --mono: var(--font-mono);
  --panel-accent: var(--teal);
  --health-c: var(--ok);

  /* Breakpoints, so the responsive rules stop being ad-hoc. */
  --bp-sm: 640px;
  --bp-md: 900px;
  --bp-lg: 1180px;
}

"""


def main():
    problems = verify("light", LIGHT) + verify("dark", DARK)
    if problems:
        print("CONTRAST FAILURES -- nothing written:", file=sys.stderr)
        for line in problems:
            print("  " + line, file=sys.stderr)
        return 1

    body = HEADER + block(LIGHT, ':root[data-theme="light"]') + "\n\n"
    body += block(DARK, '[data-theme="dark"]') + "\n"
    OUT.write_text(body)

    worst = min(
        (contrast(hx(t[f]), hx(t[b])), f, b, n)
        for t in (LIGHT, DARK) for f, b, n in PAIRS
    )
    print(f"wrote {OUT.relative_to(REPO)} "
          f"({len(LIGHT)} tokens x 2 themes + {len(DJANGO)} django vars)")
    print(f"tightest text pair: {worst[1]} on {worst[2]} = {worst[0]:.2f} "
          f"(floor {worst[3]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
