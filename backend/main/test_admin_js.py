"""Tests for the admin's own JavaScript.

These files are not covered by anything else. A Django test renders HTML, and
a browser bug is not in the HTML: the section change form rendered perfectly
and was completely unusable, because a script never let the tab go idle. So
the invariants worth pinning here are the ones a static read of the file can
check -- that an observer callback is idempotent, that a guard precedes a DOM
write -- rather than the ones only a browser can see.
"""
import pathlib
import re

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parents[2]
JS_DIR = REPO / "frontend" / "static" / "admin" / "js"


def body_of(source, function_name):
    """Return the source of a top-level function declaration."""
    match = re.search(r"function %s\s*\([^)]*\)\s*\{" % re.escape(function_name),
                      source)
    if not match:
        raise AssertionError(f"no function {function_name}() in this file")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced braces in {function_name}()")


class MediaPickerScriptTests(SimpleTestCase):
    def setUp(self):
        self.source = (JS_DIR / "media_picker.js").read_text()

    def test_refresh_preview_cannot_retrigger_its_own_observer(self):
        """The bug that made the section form unusable.

        media_picker.js observes the whole document for child-list changes so
        it can pick up inline rows Django adds later. It then rebuilds each
        picker's preview from the input's value. A rebuild is itself a
        child-list change, so the observer fired, which rebuilt, which fired
        again -- forever. The tab never went idle, the page never finished
        loading, and because the form lived inside the observed document it
        could not be saved either. The section change form has two pickers
        holding a value, which is why it hung; the page form's single picker
        was empty, which is why that one looked fine.

        The fix is that refreshPreview returns before writing anything when the
        value it would render is already on screen. This asserts the guard
        exists and that it comes *before* the first write, because a guard
        after the rebuild would be no guard at all.
        """
        body = body_of(self.source, "refreshPreview")

        writes = [
            "preview.textContent",
            "preview.appendChild",
            "preview.hidden",
            "clear.hidden",
        ]
        first_write = min(
            (body.find(w) for w in writes if body.find(w) != -1),
            default=-1)
        self.assertNotEqual(
            first_write, -1, "refreshPreview no longer writes to the preview")

        guard = re.search(r"dataset\.pickerSignature\s*===\s*signature", body)
        self.assertIsNotNone(
            guard,
            "refreshPreview has no idempotence guard. It is reached from a "
            "MutationObserver on the whole document, so rebuilding the preview "
            "on every pass will hang any page whose picker holds a value.")
        self.assertLess(
            guard.start(), first_write,
            "the idempotence guard has to come before the first DOM write; "
            "after the rebuild it protects nothing")

        # And the signature has to be recorded, or every pass re-renders.
        self.assertRegex(
            body, r"dataset\.pickerSignature\s*=\s*signature",
            "the guard compares a signature it never stores, so it can never "
            "match")

    def test_the_signature_covers_the_path(self):
        """A guard keyed on nothing would never see a change and freeze the
        preview on whatever it first drew."""
        body = body_of(self.source, "refreshPreview")
        self.assertRegex(
            body, r"var signature\s*=\s*[^;]*path",
            "the preview signature must be derived from the input's value, or "
            "a changed path will not re-render")

    def test_the_observer_is_still_wired_up(self):
        """The observer is what picks up Django's dynamically added inline
        rows. The fix made its callback idempotent rather than removing it."""
        self.assertIn("MutationObserver", self.source)
        self.assertIn("data-media-picker", self.source)


class RichTextScriptTests(SimpleTestCase):
    def setUp(self):
        self.source = (JS_DIR / "rich_text.js").read_text()

    def test_mount_is_guarded_before_it_touches_the_dom(self):
        """Same failure mode as the picker, guarded by a flag on the row.

        mount() appends a toolbar, an editor and a status line into the
        surface. That is a child-list change under a document-wide observer,
        so re-running without a guard is the same infinite loop. The guard
        therefore has to be set before the appends, not after.
        """
        body = body_of(self.source, "mount")
        guard = re.search(r"dataset\.rteMounted\s*=\s*'1'", body)
        self.assertIsNotNone(
            guard, "mount() has no re-entry guard and will loop under the "
                   "document observer")
        first_append = body.find("appendChild")
        self.assertNotEqual(first_append, -1, "mount() no longer builds the editor")
        self.assertLess(
            guard.start(), first_append,
            "the re-entry guard must be set before mount() appends anything, "
            "or the append retriggers the observer before the guard exists")
