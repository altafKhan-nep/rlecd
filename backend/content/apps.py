from django.apps import AppConfig


class ContentConfig(AppConfig):
    name = 'content'
    verbose_name = 'Website content'

    def ready(self):
        # Content writes invalidate the caches that hold derived copies of the
        # same data. Wired here so it happens for every writer, not just the
        # admin screens that happened to remember to do it.
        from content import signals  # noqa: F401
