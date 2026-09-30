"""Navigation tree assembly, caching, and current-page detection.

Three jobs live here rather than in the context processor, because each one is
worth testing on its own and because a template has no business doing URL
comparison.

Building the tree is a database read. Doing it per request means every public
page pays for the same navigation twice (desktop and mobile markup) plus one
extra query per menu item to discover whether it has a dropdown. The result
does not depend on the request, so it is cached and invalidated by the model
signals in ``content.signals``.

What goes in the cache is plain data, not MenuItem instances. That is not a
preference: pickling a model clears ``_prefetched_objects_cache``, so a cached
instance list silently loses the prefetch it was built with and reverts to one
query per item. It would also tie the cache payload to the model definition,
so any field rename would raise on read instead of on write. Dicts survive both.

Marking the current page *does* depend on the request, so it is never cached: a
cached tree must not carry another visitor's path.
"""
import logging

from django.core.cache import cache
from urllib.parse import urlsplit

from content.cache import CACHE_KEY_NAVIGATION

logger = logging.getLogger(__name__)

# service and page are forward FKs, so select_related folds them into the same
# SELECT as a LEFT JOIN. Prefetching them would spend a round trip each to
# fetch rows that are usually NULL, because a menu item that has its own url
# never looks at them at all: MenuItem.get_url returns self.url when set.
_ITEM_SELECT = ("service", "page")


def _item_queryset():
    """Top-level menu items, with their children in one extra query.

    Prefetch is spelled with an explicit Prefetch rather than the string
    "children" because the string form also prefetches the children's own
    children. Navigation is one level deep, and the extra levels are two more
    round trips per request to load rows that cannot exist.
    """
    from django.db.models import Prefetch

    from content.models import MenuItem

    return MenuItem.objects.select_related(*_ITEM_SELECT).prefetch_related(
        Prefetch("children",
                 queryset=MenuItem.objects.select_related(*_ITEM_SELECT)))


def _child_dict(child):
    return {
        "label": child.label,
        "url": child.get_url(),
        "icon": child.icon,
        "show_on_desktop": child.show_on_desktop,
        "show_on_mobile": child.show_on_mobile,
        "open_in_new_tab": child.open_in_new_tab,
    }


def _item_dict(item):
    children = [_child_dict(c) for c in item.children.all() if c.is_visible]
    return {
        "label": item.label,
        "url": item.get_url(),
        "icon": item.icon,
        "is_cta": item.is_cta,
        "show_on_desktop": item.show_on_desktop,
        "show_on_mobile": item.show_on_mobile,
        "open_in_new_tab": item.open_in_new_tab,
        "has_dropdown": bool(children),
        "children": children,
    }


def navigation_tree():
    """Return ``{navigation slug: [item dicts]}`` for every active menu.

    Cached under CACHE_KEY_NAVIGATION with no expiry: these entries are
    invalidated when a Navigation or MenuItem is written, and a stale entry that
    never expires is a bug worth seeing rather than one worth hiding behind a
    TTL. See content.signals for why invalidation hangs off the model.

    A failure to read navigation must not take the public site with it, so an
    unreadable tree degrades to no tree; the templates fall back to the service
    list, which is better than a 500 on every page.
    """
    cached = cache.get(CACHE_KEY_NAVIGATION)
    if cached is not None:
        return cached

    from content.models import Navigation

    try:
        groups = []
        for nav in Navigation.objects.filter(is_active=True):
            items = _item_queryset().filter(
                navigation=nav, is_visible=True, parent__isnull=True
            ).order_by("sort_order", "label")
            groups.append((nav.slug, [_item_dict(item) for item in items]))
        tree = dict(groups)
    except Exception:
        logger.warning("Failed to build navigation", exc_info=True)
        return {}

    cache.set(CACHE_KEY_NAVIGATION, tree, None)
    return tree


def is_current_url(target, current_path):
    """True when ``target`` is the page being served at ``current_path``.

    The comparison is deliberately stricter than "startswith". A nav link to
    ``/kitchen/`` should not light up while the visitor is reading
    ``/kitchen-renovation-guide/``, and a prefix match is exactly how a nav bar
    ends up with two "active" items on nested pages.

    Only same-site paths are comparable. A tel:, mailto:, or https: link never
    matches, no matter what the visitor is looking at, and neither does the
    placeholder ``#`` -- a dead link should not claim to be the current page.
    """
    if not target or not current_path:
        return False

    target = str(target).strip()
    if not target or target == "#":
        return False
    if urlsplit(target).scheme or urlsplit(target).netloc:
        return False
    if not target.startswith("/"):
        return False

    # A trailing slash is a routing detail, not a different page, and captured
    # nav markup is inconsistent about it. Accept a request as well as a path so
    # callers do not have to remember to unwrap it.
    if hasattr(current_path, "path"):
        current_path = current_path.path
    target_path = urlsplit(target).path.rstrip("/") or "/"
    current_path = urlsplit(str(current_path)).path.rstrip("/") or "/"
    return target_path == current_path


def mark_current(items, current_path):
    """Return a copy of ``items`` with an is_current flag on every link.

    Dicts are rebuilt rather than mutated, so the cached tree is never touched
    by a per-request decision.
    """
    marked = []
    for item in items:
        children = [dict(child, is_current=is_current_url(child["url"],
                                                           current_path))
                    for child in item["children"]]
        marked.append(dict(item, children=children,
                           is_current=is_current_url(item["url"],
                                                     current_path)))
    return marked
