"""Test runner that works regardless of the directory it is invoked from.

Django's default `DiscoverRunner` discovers from the current working directory.
That silently finds zero tests when `manage.py test` is run from the repository
root: the apps live one level down in `backend/`, and the root holds only the
`api/` and `scripts/` directories, neither of which matches `test*.py`. The
command still exits successfully, so the failure is easy to miss.

The apps import as top-level `main`, `crm` and `content` (that is the whole
point of putting `backend/` on `sys.path`: it keeps the app labels recorded in
migrations and referenced by the stored page markup). Discovery has to agree
with that, so both the start directory and the import top level are pinned to
`backend/`.

Explicitly named targets still work and are still resolved against the caller's
cwd, e.g. `manage.py test crm.tests.DeploymentSettingsTests`.
"""

import unittest

from django.conf import settings
from django.core.cache import cache
from django.test.runner import DiscoverRunner


class CacheClearingTestCase:
    """Drop the content cache before every test class.

    TestCase rolls the database back after each test, but the cache lives
    outside that transaction. Without this, a class that renders the
    navigation populates the cache and the next class -- which may have just
    deleted the Navigation row in setUp, or never created one -- would be
    served the previous class's tree. That is not a test isolation nit; it
    turns a real caching bug into a flaky test, or hides one entirely.
    """

    @classmethod
    def setUpClass(cls):
        cache.clear()
        super().setUpClass()


class BackendDiscoverRunner(DiscoverRunner):
    """Pin bare test discovery to the directory that holds the apps."""

    def build_suite(self, test_labels=None, *args, **kwargs):
        if not test_labels:
            # No targets given: discover the whole project, anchored on
            # backend/ rather than on whatever directory the shell is in.
            test_labels = [str(settings.BASE_DIR)]
            kwargs.setdefault("top_level", str(settings.BASE_DIR))
        return super().build_suite(test_labels, *args, **kwargs)

    def run_suite(self, suite, **kwargs):
        return super().run_suite(self._clear_cache_between_classes(suite), **kwargs)

    @staticmethod
    def _clear_cache_between_classes(suite):
        """Wrap every TestCase in the suite so the cache is dropped per class.

        Recurses, because discovery nests suites: the top level is grouped by
        app package, not by test class.
        """
        wrapped = unittest.TestSuite()
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                wrapped.addTest(
                    BackendDiscoverRunner._clear_cache_between_classes(item))
            elif isinstance(item, unittest.TestCase):
                item.__class__ = type(
                    f"CacheClearing{item.__class__.__name__}",
                    (CacheClearingTestCase, item.__class__),
                    {"__module__": item.__class__.__module__},
                )
                wrapped.addTest(item)
            else:
                wrapped.addTest(item)
        return wrapped
