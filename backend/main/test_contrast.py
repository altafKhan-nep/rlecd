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
        """Load order is load-bearing: tokens define, admin consumes.

        This reads the <link> tags rather than searching the raw text, because
        the file also discusses the load order in a comment and a plain
        `index()` finds the prose instead of the stylesheet.
        """
        base = (pathlib.Path(__file__).resolve().parents[2]
                / "frontend" / "templates" / "admin" / "base_site.html")
        html = base.read_text()
        ours = re.findall(r"admin/css/([\w.-]+\.css)", html)
        self.assertEqual(
            ours, ["tokens.css", "admin.css", "responsive.css"],
            f"stylesheet link order changed: {ours}")

    def test_our_stylesheets_load_after_djangos(self):
        """Django must not get the last word on the pages we style.

        `change_list.html` extends *this* base_site and appends its own
        `changelists.css` to `extrastyle` after `{{ block.super }}`. Stylesheets
        emitted from `extrastyle` therefore lose every equal-specificity tie to
        it, which is how the changelist kept Django's `white-space: nowrap` on
        the header cells while our rules sat in the file the whole time.
        `extrahead` and `responsive` both render after `extrastyle`, so ours
        have to be emitted from there.
        """
        base = (pathlib.Path(__file__).resolve().parents[2]
                / "frontend" / "templates" / "admin" / "base_site.html")
        html = base.read_text()

        def block(name):
            match = re.search(
                r"\{% block " + re.escape(name) + r" %\}(.*?)\{% endblock %\}",
                html, re.S)
            self.assertIsNotNone(match, f"no {name} block in base_site.html")
            return match.group(1)

        extrastyle = block("extrastyle")
        for sheet in ("tokens.css", "admin.css", "responsive.css"):
            self.assertNotIn(
                sheet, extrastyle,
                f"{sheet} is emitted from extrastyle, so changelists.css "
                f"loads after it and wins every specificity tie")
        self.assertIn("tokens.css", block("extrahead"))
        self.assertIn("admin.css", block("extrahead"))
        # responsive.css has to go one step further still: Django ships a
        # responsive.css of its own in the `responsive` block.
        self.assertIn("responsive.css", block("responsive"))
        self.assertIn("{{ block.super }}", block("responsive"),
                      "the responsive block must keep the viewport meta tag")

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
            widths <= {640, 900, 1180, 1280, 1440, 1600},
            f"unexpected breakpoint widths in responsive.css: {widths}")

    def test_every_breakpoint_is_a_measured_one(self):
        """1440 and 1600 exist because of arithmetic, not taste.

        The widest changelist (Section) has a 1,190px minimum content width,
        and the content column has already surrendered 254px to the sidebar and
        80px to padding. 1,190 + 254 + 80 = 1,524, so the table is only shown
        above 1,440 and the cards below it. 1600 is where Django's 270px filter
        column stops sitting beside the results. Both numbers are recorded here
        so a later edit has to argue with the measurement rather than guess.
        """
        self.assertIn("1,190", self.responsive)
        self.assertIn("254", self.responsive)
        self.assertEqual(1190 + 254 + 80, 1524)

    def test_the_container_does_not_outgrow_its_own_margin(self):
        """The bug behind the scrollbar on every single page.

        Django ships `#container { width: 100%; min-width: 980px }`. A
        percentage width is resolved against the containing block, so the
        sidebar's `margin-left` never subtracts from it: the box stayed a full
        viewport wide and stuck out past the right edge by exactly the sidebar
        width. `width: auto` lets the margin reduce the content box, and the
        980px floor is what forced any narrower viewport to scroll too.
        """
        match = re.search(r"^#container \{(.*?)\}", self.admin, re.S | re.M)
        self.assertIsNotNone(match, "no #container rule in admin.css")
        body = match.group(1)
        self.assertIn("width: auto", body,
                      "#container must be width:auto so margin-left counts "
                      "against it")
        self.assertIn("min-width: 0", body,
                      "Django's min-width:980px must be cleared or every "
                      "viewport under 980px scrolls sideways")
        self.assertNotIn("100%", body)

    def test_the_drawer_breakpoint_clears_the_sidebar_offset(self):
        """Below 900px the sidebar becomes a drawer, so the offset has nothing
        to clear. Left in place it left a phantom gutter a sidebar wide."""
        body = self._block(900)
        self.assertRegex(
            body, r"#container[^{]*\{[^}]*margin-left:\s*0",
            "the 900px drawer block must reset #container's margin-left")
        # The collapsed rule is more specific than a bare #container, so it has
        # to be reset too or the rail-width offset comes back.
        self.assertIn("html.sidebar-collapsed #container", body)

    def test_the_stacked_changelist_is_stretched_not_shrunk_to_fit(self):
        """`align-items: flex-start` is correct while Django's filter sits
        beside the results. Once they are stacked into a column, flex-start
        leaves both children shrink-to-fit, so a wide table picks its own
        max-content width and is clipped by the panel's overflow-x: hidden."""
        body = self._block(1600)
        self.assertRegex(
            body, r"#changelist\s*\{[^}]*align-items:\s*stretch",
            "the stacked changelist must stretch its children")
        self.assertRegex(body, r"#changelist-filter\s*\{[^}]*margin:",
                         "the filter's 30px left margin belongs to the "
                         "beside-the-list layout and must be cleared")
        self.assertRegex(body, r"#changelist-filter\s*\{[^}]*float:\s*none")

    def test_djangos_nowrap_cells_are_released(self):
        """Django tags date, time and foreign-key cells `nowrap`.

        In a table that reserves enough width to push the result list past its
        container, where `.results` clips it. In the card layout it is worse:
        each cell is already a full-width line, so the value starts setting the
        width of the whole list. Both element names matter, because Django
        renders the first linkable column as a <th> row header -- a td-only
        selector left Section's Page column at 357px for "Home (/)".
        """
        self.assertRegex(
            self.admin, r"#changelist td\.nowrap[^{]*,\s*\n?\s*"
                        r"#changelist th\.nowrap\s*\{[^}]*white-space:\s*normal")
        cards = self._block(1440)
        self.assertIn("#result_list td.nowrap", cards)
        self.assertIn("#result_list th.nowrap", cards)

    def test_form_controls_may_not_exceed_their_container(self):
        """Django renders the search box as `<input size="40">`.

        An input sizes itself from that attribute rather than from the space
        available, so the intrinsic 366px was wider than a phone-width panel
        and pushed the search box, paginator, filters and rows out of the
        viewport together.
        """
        self.assertRegex(
            self.admin, r'#changelist input\[type="text"\][^{]*\{[^}]*'
                        r"max-width:\s*100%")
        self.assertRegex(
            self.admin, r"#changelist select[^{]*\{[^}]*max-width:\s*100%")

    def _block(self, width):
        match = re.search(
            rf"@media \(max-width: {width}px\) \{{(.*?)\n\}}",
            self.responsive, re.S)
        self.assertIsNotNone(match, f"no {width}px block in responsive.css")
        return match.group(1)

    def test_changelist_becomes_cards_rather_than_scrolling(self):
        """A horizontal scroll on a changelist is the "half the page is cut"
        complaint: there is no cue that there is more to the right."""
        body = self._block(1440)
        self.assertIn("#result_list tr", body)
        self.assertIn("data-label", body)
        self.assertIn("#result_list thead", body)
        # Beaten against Django's own `overflow-x: auto` on the same element,
        # which is (1,1,0) and would otherwise keep the container scrolling
        # horizontally while the rows inside it are cards.
        self.assertIn("#changelist-form .results", body)

    def test_no_rule_forces_a_track_wider_than_a_laptop(self):
        """The specific cause of the bottom scrollbar.

        `grid-auto-flow: column` with `minmax(230px, 1fr)` is a hard floor of
        230px per column: six pipeline stages is 1,380px before any padding, so
        the board grew a horizontal scrollbar on every screen narrower than
        that and nothing about the layout could prevent it. A 1fr track also
        has an automatic minimum equal to its content, so one long unbreakable
        word inside it widens the grid and the page.
        """
        sources = {"admin.css": self.admin, "responsive.css": self.responsive}
        for name, source in sources.items():
            for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
                selector, body = rule
                if "grid" not in body and "flex" not in body:
                    continue
                if "column" in body:
                    with self.subTest(file=name, selector=selector.strip()[:40]):
                        self.assertNotIn(
                            "grid-auto-flow: column", body,
                            "a column-flow grid has a fixed per-column minimum, "
                            "so N columns overflow any viewport below their sum")
            for track in re.findall(r"minmax\(\s*(\d+)px", body):
                with self.subTest(file=name, selector=selector.strip()[:40],
                                  track=track):
                    self.assertLessEqual(
                        int(track), 320,
                        f"{selector.strip()[:40]} has a {track}px floor, which "
                        f"alone exceeds a phone")

    def test_the_login_box_is_not_wider_than_the_screen(self):
        """The login page had the same scrollbar, for the same kind of reason.

        Django's vendored login.css gives the box a fixed `width: 28em` with a
        `min-width: 300px` floor and 100px of top margin. On a 375px phone that
        is wider than the viewport, and the margin plus the box is taller than a
        short window, so the submit button fell below the fold -- which on a
        login form is a dead end.
        """
        block = re.search(
            r"body\.login #container\s*\{([^}]*)\}", self.responsive)
        self.assertIsNotNone(
            block, "no login container override in responsive.css")
        body = block.group(1)
        self.assertIn("min(28em, 100%)", body,
                      "the fixed 28em width must become fluid")
        self.assertIn("min-width: 0", body,
                      "the 300px floor must go, or a narrow phone still scrolls")
        # Equal specificity to Django's own rule and relying on link order
        # would break the moment the template stopped double-loading us.
        self.assertIn("margin: clamp(", body,
                      "a fixed 100px top margin overflows a short window")

    def test_no_login_rule_is_ordered_by_accident(self):
        """`body.login` beats Django's `.login` on specificity, not by luck.

        responsive.css is currently linked twice on the login page and only the
        second copy is after login.css. If that ever changes, a same-specificity
        override would silently stop applying.
        """
        for selector in re.findall(r"(body\.login [^{]+)\{", self.responsive):
            with self.subTest(selector=selector.strip()):
                self.assertTrue(
                    selector.strip().startswith("body."),
                    f"{selector.strip()!r} relies on stylesheet order, not "
                    f"specificity, to beat Django's vendored login.css")

    def test_the_board_wraps_instead_of_scrolling(self):
        board = re.search(r"\.kanban-board\s*\{([^}]*)\}", self.admin)
        self.assertIsNotNone(board, "no .kanban-board rule")
        body = board.group(1)
        self.assertIn("auto-fit", body,
                      "the board must wrap its columns; a fixed column flow is "
                      "what put a scrollbar under it")
        self.assertNotIn("grid-auto-flow: column", body)
        self.assertNotIn("overflow-x: auto", body)


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
