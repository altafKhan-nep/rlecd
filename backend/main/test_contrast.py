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

CSS_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend"
    / "static"
    / "admin"
    / "css"
    / "admin.css"
)


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

    def test_stylesheet_exists(self):
        self.assertTrue(CSS_PATH.is_file(), f"missing stylesheet at {CSS_PATH}")

    def _series_gradient(self, name):
        """Pull both colour stops out of a --v-series-N gradient."""
        match = re.search(
            rf"--v-series-{name}\s*:\s*[^;]*?#([0-9A-Fa-f]{{6}})\s*0%\s*,\s*"
            rf"#([0-9A-Fa-f]{{6}})\s*100%",
            self.css,
        )
        self.assertIsNotNone(match, f"could not parse --v-series-{name}")
        return "#" + match.group(1), "#" + match.group(2)

    def _declaration(self, selector, prop="background"):
        """Read a property from a rule, ignoring colour shorthand fallbacks.

        The vibrant layer is appended to the end of the file and relies on
        source order, so several selectors appear more than once with equal
        specificity. The last match is the one that actually paints.
        """
        pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
        found = None
        for body in re.findall(pattern, self.css):
            # \b cannot be used to guard a custom property: a leading "--" is
            # all non-word characters, so there is no boundary to anchor to.
            # Look behind for anything word-like or another hyphen instead, so
            # "--panel-accent" does not match inside a longer name.
            match = re.search(rf"(?<![-\w]){re.escape(prop)}\s*:\s*([^;]+)", body)
            if match:
                found = match.group(1).strip()
        if found is None:
            self.fail(f"no {prop} declaration found for {selector}")
        return self._resolve(found)

    def _resolve(self, value):
        """Expand var(--name) references against the :root block.

        The palette is declared once as custom properties and referenced
        elsewhere, so a raw "var(--v-sky)" carries no colour for the ratio
        calculation to work with.
        """
        for _ in range(5):  # bounded: a cycle would otherwise spin forever
            match = re.search(r"var\((--[\w-]+)\)", value)
            if not match:
                break
            prop = match.group(1)
            defined = re.findall(
                rf"{re.escape(prop)}\s*:\s*([^;]+);", self.css)
            if not defined:
                self.fail(f"custom property {prop} is referenced but never set")
            value = value.replace(match.group(0), defined[-1].strip())
        return value

    def test_card_gradients_have_colourful_distinct_stops(self):
        """The point of the vibrant layer: five different, saturated cards.
        Guards against someone flattening them back to one hue."""
        series = {}
        for name in ("1", "2", "3", "4", "5"):
            series[name] = self._series_gradient(name)
            self.assertNotEqual(
                series[name][0], series[name][1],
                f"series {name} gradient has a single flat colour",
            )
        distinct = {series[n][0] for n in series}
        self.assertEqual(len(distinct), 5, "card gradients are not distinct")

    def test_stat_card_labels_are_readable_on_every_gradient(self):
        for name in ("1", "2", "3", "4", "5"):
            for stop, background in zip(("start", "end"),
                                        self._series_gradient(name)):
                with self.subTest(card=name, stop=stop):
                    ratio = contrast_ratio("#FFFFFF", background)
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"card {name} {stop} {background}: white text is "
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

    def test_panel_titles_are_readable_on_white(self):
        for accent in ("panel-crm", "panel-pipeline", "panel-content",
                        "panel-service"):
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
