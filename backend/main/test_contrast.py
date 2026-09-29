"""Contrast regression tests for the custom admin stylesheet.

The vibrant layer replaced the original muted palette with saturated gradients.
That is exactly the kind of change that silently fails accessibility: white
labels on a mid-tone gradient look fine in a screenshot and are unreadable in
practice. Several pairs were below 4.5:1 on the first pass and were darkened by
hand, so this module pins the corrected values down.

Thresholds: 4.5:1 for body and small text, 3:1 for large text (>=24px, or
>=18.66px bold) and for non-text UI boundaries such as the 4px panel accent bar.
"""
import pathlib
import re

from django.test import SimpleTestCase

CSS_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "static"
    / "admin"
    / "css"
)
CSS_PATH = CSS_DIR / "admin.css"
TOKENS_PATH = CSS_DIR / "tokens.css"


def _relative_luminance(hex_colour):
    """WCAG 2.x relative luminance."""
    value = hex_colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground, background):
    """WCAG 2.x contrast ratio, 1.0 to 21.0."""
    lum_a = _relative_luminance(foreground)
    lum_b = _relative_luminance(background)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


class AdminStylesheetContrastTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = CSS_PATH.read_text()
        # The colours moved to tokens.css (generated, contrast-checked by
        # scripts/build_tokens.py). Tests that resolve a var() have to look
        # there as well as in admin.css, or every custom property reads as
        # "referenced but never set" the moment the palette is extracted.
        cls.tokens = TOKENS_PATH.read_text() if TOKENS_PATH.is_file() else ""

    def test_stylesheet_exists(self):
        self.assertTrue(CSS_PATH.is_file(), f"missing stylesheet at {CSS_PATH}")
        self.assertTrue(
            TOKENS_PATH.is_file(),
            f"missing design tokens at {TOKENS_PATH}; run scripts/build_tokens.py")

    def _series_gradient(self, tone):
        """Pull both colour stops out of a .stat-card.t-* gradient."""
        match = re.search(
            rf"\.stat-card\.{re.escape(tone)}\s*\{{[^}}]*?--card-bg:\s*"
            rf"linear-gradient\(135deg,\s*#([0-9A-Fa-f]{{6}})\s*0%,\s*"
            rf"#([0-9A-Fa-f]{{6}})\s*100%\)",
            self.css,
        )
        self.assertIsNotNone(match, f"could not parse .stat-card.{tone}")
        return "#" + match.group(1), "#" + match.group(2)

    def _declaration(self, selector, prop="background"):
        """Read a property from a rule, ignoring colour shorthand fallbacks.

        Selectors can appear more than once with equal specificity, so the
        last match is the one that actually paints.
        """
        # Grouped selectors (".pill-low, .pill-no { ... }") must match too, so
        # split on commas and test each part rather than requiring the selector
        # to open a block on its own.
        pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
        found = None
        for body in re.findall(pattern, self.css):
            match = re.search(rf"(?<![-\w]){re.escape(prop)}\s*:\s*([^;]+)", body)
            if match:
                found = match.group(1).strip()
        if found is None:
            # Try as one member of a grouped selector list.
            for group in re.findall(r"([^{}]+)\{([^}]*)\}", self.css):
                selectors, body = group
                if selector in [s.strip() for s in selectors.split(",")]:
                    match = re.search(
                        rf"(?<![-\w]){re.escape(prop)}\s*:\s*([^;]+)", body)
                    if match:
                        found = match.group(1).strip()
                        break
        if found is None:
            self.fail(f"no {prop} declaration found for {selector}")
        return self._resolve(found)

    def _theme_vars(self, theme):
        """The custom properties declared for one theme."""
        if theme == "light":
            block = re.search(
                r':root\[data-theme="light"\]\s*\{([^}]*)\}', self.tokens)
        else:
            block = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', self.tokens)
        source = block.group(1) if block else ""
        found = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", source))
        if not found:
            # Fall back to the shared :root block in tokens.css.
            shared = re.search(r"^:root\s*\{([^}]*)\}", self.tokens, re.M)
            if shared:
                found = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", shared.group(1)))
        return found

    def _resolve(self, value, theme="light"):
        """Expand var(--name) references against one theme's block.

        The palette is declared once per theme as custom properties and
        referenced elsewhere, so a raw "var(--sky)" carries no colour for the
        ratio calculation to work with. Resolving against the wrong theme gives
        the wrong answer: a colour chosen to be readable on a dark surface is
        unreadable on white, which is the whole reason the two palettes are
        separate.
        """
        theme_vars = self._theme_vars(theme)
        for _ in range(5):  # bounded: a cycle would otherwise spin forever
            match = re.search(r"var\((--[\w-]+)\)", value)
            if not match:
                break
            prop = match.group(1)
            defined = re.findall(
                rf"{re.escape(prop)}\s*:\s*([^;]+);", self.tokens) or re.findall(
                rf"{re.escape(prop)}\s*:\s*([^;]+);", self.css)
            if not defined:
                self.fail(f"custom property {prop} is referenced but never set")
            # Prefer this theme's value; fall back to the shared block.
            value = value.replace(
                match.group(0), theme_vars.get(prop, defined[-1]).strip())
        return value

    def test_card_gradients_have_colourful_distinct_stops(self):
        """Six different, saturated cards. Guards against someone flattening
        them back to one hue."""
        tones = ("t-leads", "t-hot", "t-today", "t-tasks", "t-pages", "t-media")
        series = {}
        for tone in tones:
            series[tone] = self._series_gradient(tone)
            self.assertNotEqual(
                series[tone][0], series[tone][1],
                f"{tone} gradient has a single flat colour",
            )
        distinct = {series[t][0] for t in series}
        self.assertEqual(len(distinct), 6, "card gradients are not distinct")

    def test_stat_card_labels_are_readable_on_every_gradient(self):
        for tone in ("t-leads", "t-hot", "t-today", "t-tasks", "t-pages", "t-media"):
            for stop, background in zip(("start", "end"),
                                        self._series_gradient(tone)):
                with self.subTest(card=tone, stop=stop):
                    ratio = contrast_ratio("#FFFFFF", background)
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"card {tone} {stop} {background}: white text is "
                        f"{ratio:.2f}:1",
                    )

    def test_status_pills_are_readable(self):
        """Pills are small bold text, so 3:1 is not sufficient here."""
        expected = {
            "new": None, "contacted": None, "qualified": None,
            "estimate_sent": None, "won": None, "lost": None,
        }
        for name in expected:
            background = self._declaration(f".pill-{name}")
            colours = re.findall(r"#[0-9A-Fa-f]{6}", background)
            self.assertTrue(colours, f".pill-{name} has no hex colour")
            for colour in colours:
                with self.subTest(pill=name, colour=colour):
                    ratio = contrast_ratio("#FFFFFF", colour)
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"pill '{name}' on {colour} is {ratio:.2f}:1",
                    )

    def test_score_chip_tiers_are_readable(self):
        for tier in ("s-hi", "s-mid", "s-lo"):
            background = self._declaration(f".score-chip.{tier}")
            for colour in re.findall(r"#[0-9A-Fa-f]{6}", background):
                with self.subTest(tier=tier, colour=colour):
                    ratio = contrast_ratio("#FFFFFF", colour)
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"{tier} on {colour} is {ratio:.2f}:1",
                    )

    #: The surface each theme's text is drawn on. A single hard-coded white here
    #: only ever checked the light theme, and every dark-theme colour would be
    #: judged against the wrong background.
    SURFACES = {"light": "#FFFFFF", "dark": "#151F1A"}

    def _surface(self, theme):
        return self.SURFACES[theme]

    def test_both_themes_resolve_to_real_colours(self):
        """Guards the two palettes against drifting into each other.

        If a token is only defined for one theme, the other falls back to the
        shared block and the page renders with near-invisible text -- which is
        exactly the bug this file exists to prevent.
        """
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                variables = self._theme_vars(theme)
                for name in ("--ink", "--ink-soft", "--surface", "--bg", "--line"):
                    self.assertIn(name, variables,
                                  f"{name} missing from the {theme} theme")

    def test_text_tokens_are_readable_in_both_themes(self):
        """The core check the original white-on-white audit could not make."""
        pairs = [
            ("--ink", "--surface", 4.5), ("--ink", "--bg", 4.5),
            ("--ink-soft", "--surface", 4.5), ("--muted", "--surface", 4.5),
            ("--link", "--surface", 4.5),
        ]
        for theme in ("light", "dark"):
            variables = self._theme_vars(theme)
            for fg, bg, minimum in pairs:
                with self.subTest(theme=theme, pair=f"{fg} on {bg}"):
                    fgc = re.findall(r"#[0-9A-Fa-f]{6}", variables[fg])
                    bgc = re.findall(r"#[0-9A-Fa-f]{6}", variables[bg])
                    self.assertTrue(fgc and bgc, f"{fg}/{bg} unresolvable")
                    ratio = contrast_ratio(fgc[-1], bgc[-1])
                    self.assertGreaterEqual(
                        ratio, minimum,
                        f"{theme}: {fg} on {bg} is {ratio:.2f}:1")

    def test_panel_titles_are_readable_on_white(self):
        for accent in ("panel-crm", "panel-pipeline", "panel-content",
                       "panel-service", "panel-tasks", "panel-trend",
                       "panel-services"):
            value = self._declaration(f".{accent}", prop="--panel-accent")
            colours = re.findall(r"#[0-9A-Fa-f]{6}", value)
            self.assertTrue(colours, f".{accent} resolved to {value!r}")
            for colour in colours:
                with self.subTest(panel=accent, colour=colour):
                    ratio = contrast_ratio(colour, "#FFFFFF")
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f".{accent} title on {colour} is {ratio:.2f}:1",
                    )

    def test_neutral_pills_use_dark_ink_on_light_fill(self):
        for pill in ("neutral", "low", "no"):
            background = self._declaration(f".pill-{pill}")
            colour = self._declaration(f".pill-{pill}", prop="color")
            ratio = contrast_ratio(colour, background)
            with self.subTest(pill=pill):
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f".pill-{pill} {colour} on {background} is {ratio:.2f}:1",
                )

    def test_storage_warning_text_is_readable(self):
        for colour in ("#8A4F0A", "#96580F"):
            if colour in self.css:
                ratio = contrast_ratio(colour, "#FDF1DC")
                with self.subTest(colour=colour):
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"warning {colour} on #FDF1DC is {ratio:.2f}:1",
                    )

    def test_no_escaped_icon_markup_regression(self):
        """The colour work and the icons ship together; the icon SVG is built
        in Python and needs mark_safe, which is covered in test_admin.py. This
        just confirms the stylesheet that styles those icons is still present."""
        self.assertIn(".stat-icon svg", self.css)
        self.assertIn(".stat-inner", self.css)


class DesignTokenTests(SimpleTestCase):
    """The palette is generated and theme-scoped, and that has to stay true.

    The original defect was structural, not a colour value: admin.css declared
    its palette on a bare `:root`, Django 5.2's dark_mode.css also owns `:root`
    and repaints it under `prefers-color-scheme: dark`, and ours loaded last.
    So ours won, and near-black text sat on Django's #121212 body. Every colour
    in the project was individually fine. These tests are about the structure.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tokens = TOKENS_PATH.read_text()
        cls.admin = CSS_PATH.read_text()
        cls.responsive = (CSS_DIR / "responsive.css").read_text()

    def referenced_properties(self, source):
        return set(re.findall(r"var\(\s*(--[\w-]+)", source))

    def test_no_custom_property_is_referenced_but_never_defined(self):
        """The bug class, directly.

        `--ink-soft` was referenced seventeen times and defined zero times, so
        every one of those uses silently fell back to a hard-coded mid-grey --
        which is unreadable on a dark surface. Any property that is used must
        exist in the token file.
        """
        declared = set(re.findall(r"(--[\w-]+)\s*:", self.tokens))
        for name, source in (("admin.css", self.admin),
                             ("responsive.css", self.responsive)):
            for prop in sorted(self.referenced_properties(source)):
                with self.subTest(file=name, prop=prop):
                    self.assertIn(
                        prop, declared,
                        f"{prop} is used in {name} but never defined in "
                        f"tokens.css, so it silently falls back")

    def test_admin_css_declares_no_bare_root_palette(self):
        """`:root` is how the two themes got into a fight.

        A bare `:root` block in admin.css has the same specificity as Django's
        and loads later, so it wins -- including in dark mode. Colours belong in
        tokens.css, scoped to `[data-theme=...]`.
        """
        blocks = re.findall(r"(?m)^\s*:root\s*\{", self.admin)
        self.assertEqual(
            blocks, [],
            "admin.css declares a bare :root block; move the colours to "
            "tokens.css and scope them to :root[data-theme=...]")

    def test_both_themes_define_the_same_properties(self):
        """A token present in one theme and missing from the other falls back
        to the shared block, and the page renders with the wrong colours."""
        light = set(re.findall(
            r"(--[\w-]+)\s*:", re.search(
                r':root\[data-theme="light"\]\s*\{([^}]*)\}', self.tokens).group(1)))
        dark = set(re.findall(
            r"(--[\w-]+)\s*:", re.search(
                r'\[data-theme="dark"\]\s*\{([^}]*)\}', self.tokens).group(1)))
        self.assertEqual(
            light, dark,
            f"light-only: {set(light) - set(dark)}, "
            f"dark-only: {set(dark) - set(light)}")

    def test_theme_selectors_outrank_djangos_bare_root(self):
        """The mechanism that makes an explicit choice win.

        Django's dark_mode.css uses `:root` inside a media query. Ours must be
        more specific or the OS preference overrules the user's own choice --
        which is the bug again, one layer up.
        """
        self.assertIn(':root[data-theme="light"]', self.tokens)
        self.assertIn('[data-theme="dark"]', self.tokens)

    def test_admin_pages_always_carry_an_explicit_theme(self):
        """`data-theme` must never be absent.

        If the attribute is missing, nothing of ours applies and Django's media
        query decides the palette alone -- the two-palette fight, unresolved.
        The pre-paint snippet sets it and falls back to 'light' if it throws.
        """
        base = (pathlib.Path(__file__).resolve().parents[2]
                / "frontend" / "templates" / "admin" / "base_site.html")
        html = base.read_text()
        self.assertIn("studioTheme.apply", html)
        self.assertIn("setAttribute('data-theme', 'light')", html)

    def test_tokens_css_is_loaded_before_admin_css(self):
        """Load order is load-bearing: tokens define, admin consumes."""
        base = (pathlib.Path(__file__).resolve().parents[2]
                / "frontend" / "templates" / "admin" / "base_site.html")
        html = base.read_text()
        self.assertLess(html.index("tokens.css"), html.index("admin.css"))
        self.assertLess(html.index("admin.css"), html.index("responsive.css"))

    def test_generated_tokens_are_up_to_date(self):
        """Re-running the generator must be a no-op.

        This is what stops someone hand-editing tokens.css, or changing the
        palette in the script and forgetting to regenerate: either way the
        committed file stops matching its source and the contrast guarantees go
        with it.
        """
        import subprocess
        import sys
        repo = pathlib.Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, str(repo / "scripts" / "build_tokens.py")],
            capture_output=True, text=True, cwd=repo)
        self.assertEqual(
            result.returncode, 0,
            f"token generator failed: {result.stderr}")
        self.assertEqual(
            TOKENS_PATH.read_text(), self.tokens,
            "tokens.css does not match scripts/build_tokens.py; re-run it")

    def test_the_responsive_layer_owns_every_breakpoint(self):
        """Eight ad-hoc @media blocks at five different widths is how the
        changelists ended up with no narrow-screen handling at all."""
        self.assertEqual(
            re.findall(r"@media[^\n{]*", self.admin), [],
            "admin.css has @media rules again; breakpoints live in "
            "responsive.css so there is one scale, not one per feature")
        queries = re.findall(r"@media[^\n{]*", self.responsive)
        widths = {int(w) for q in queries for w in re.findall(r"(\d{3,4})px", q)}
        self.assertTrue(
            widths <= {640, 900, 1180},
            f"unexpected breakpoint widths in responsive.css: {widths}")

    def test_changelist_becomes_cards_rather_than_scrolling(self):
        """A horizontal scroll on a changelist is the "half the page is cut"
        complaint: there is no cue that there is more to the right."""
        narrow = re.search(
            r"@media \(max-width: 900px\) \{(.*?)\n\}", self.responsive, re.S)
        self.assertIsNotNone(narrow, "no 900px block in responsive.css")
        body = narrow.group(1)
        self.assertIn("#result_list tr", body)
        self.assertIn("data-label", body)
        self.assertIn("#result_list thead", body)


class ThemeToggleTests(SimpleTestCase):
    def setUp(self):
        base = (pathlib.Path(__file__).resolve().parents[2]
                / "frontend" / "templates" / "admin" / "base_site.html")
        self.html = base.read_text()
        self.js = (pathlib.Path(__file__).resolve().parents[2]
                   / "frontend" / "static" / "admin" / "js" / "theme.js").read_text()

    def test_the_toggle_exists_and_carries_its_state(self):
        self.assertIn('id="theme-toggle"', self.html)
        self.assertIn("aria-pressed", self.html)

    def test_the_toggle_keeps_the_attribute_authoritative(self):
        """Toggling must always set the attribute, never remove it."""
        self.assertIn("setAttribute('data-theme'", self.js)
        self.assertNotIn("removeAttribute('data-theme'", self.js)

    def test_the_choice_is_remembered(self):
        self.assertIn("localStorage", self.js)

    def test_the_os_preference_is_only_a_default(self):
        """A user who has chosen a theme must not have it overridden at sunset."""
        self.assertIn("prefers-color-scheme: dark", self.js)
        self.assertIn("systemTheme", self.js)

    def test_color_scheme_follows_so_native_widgets_match(self):
        """Without this the OS keeps drawing light scrollbars on a dark page."""
        self.assertIn("colorScheme", self.js)
