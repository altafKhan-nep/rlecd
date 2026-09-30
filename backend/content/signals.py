"""Cache invalidation on write, from the model rather than the admin.

The first version of this invalidated from ``ModelAdmin.save_model``. That works
right up until something writes the row by another route -- a data migration, a
management command, a fixture load, a shell session -- and the cached copy then
sits there forever, because these entries have no expiry. The failure looks like
"the site ignores my edit", which is the worst kind of bug to chase.

Connecting to the model means the invalidation is a property of the data, not of
one editor's screen. The admin no longer has to remember.

Service is referenced by string rather than imported: content.models points at
crm.Service with a lazy reference, and importing crm.models from here would
close that loop during app loading.
"""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from content.cache import (
    invalidate_navigation_cache,
    invalidate_service_cache,
    invalidate_services_cache,
    invalidate_site_settings_cache,
)

logger = logging.getLogger(__name__)


def _bust(*invalidators):
    """Never let a cache failure stop a write from landing.

    The row is already saved by the time this runs. Refusing to complete the
    save because a cache backend is unreachable would turn a performance
    problem into a data loss problem, and the cache is by definition derived
    data.

    Each invalidator is run even if an earlier one raised, so one broken key
    cannot leave the others stale.
    """
    for invalidate in invalidators:
        try:
            invalidate()
        except Exception:
            logger.warning(
                "Cache invalidation failed in %s", invalidate, exc_info=True)


@receiver(post_save, sender="content.Navigation")
@receiver(post_delete, sender="content.Navigation")
@receiver(post_save, sender="content.MenuItem")
@receiver(post_delete, sender="content.MenuItem")
def navigation_changed(sender, **kwargs):
    _bust(invalidate_navigation_cache)


@receiver(post_save, sender="content.SiteSetting")
@receiver(post_delete, sender="content.SiteSetting")
def site_settings_changed(sender, **kwargs):
    _bust(invalidate_site_settings_cache)


@receiver(post_save, sender="crm.Service")
@receiver(post_delete, sender="crm.Service")
def service_changed(sender, instance, **kwargs):
    """A service edit reaches navigation, the sitemap and the service lists.

    A renamed service or a changed slug moves the nav link too, so the whole
    services list and the navigation both go.
    """
    pk = getattr(instance, "pk", None)
    _bust(invalidate_services_cache, invalidate_navigation_cache)
    if pk:
        _bust(lambda: invalidate_service_cache(pk))
