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

from django.conf import settings
from django.test.runner import DiscoverRunner


class BackendDiscoverRunner(DiscoverRunner):
    """Pin bare test discovery to the directory that holds the apps."""

    def build_suite(self, test_labels=None, *args, **kwargs):
        if not test_labels:
            # No targets given: discover the whole project, anchored on
            # backend/ rather than on whatever directory the shell is in.
            test_labels = [str(settings.BASE_DIR)]
            kwargs.setdefault("top_level", str(settings.BASE_DIR))
        return super().build_suite(test_labels, *args, **kwargs)
