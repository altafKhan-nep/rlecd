from django.conf import settings
from django.core.cache import cache

from content.cache import (
    CACHE_KEY_SERVICES,
    CACHE_KEY_SITE_SETTINGS,
)
from content.nav import mark_current, navigation_tree


def site(request):
    """Expose the canonical site origin to every template.

    Used for <link rel="canonical">, Open Graph URLs and the JSON-LD @id so
    those tags stay correct when the site is served from localhost, staging
    or production without editing templates.

    When SITE_URL is unset the origin is derived from the incoming request
    instead. A first deployment does not know its own hostname until after the
    build, so anything hardcoded here would point canonical and og: tags at a
    domain that is either wrong or not ours. request.get_host() is already
    filtered against ALLOWED_HOSTS by Django, so this cannot be used to forge
    an arbitrary origin.
    """
    origin = settings.SITE_URL
    if not origin and request is not None:
        origin = f"{request.scheme}://{request.get_host()}"
    return {"SITE_URL": origin}


def site_settings(request):
    """Expose the global SiteSetting singleton to every template.

    This is the true global source of truth for all business content. Every
    public template reads from this — navbar, footer, contact sections, CTA
    buttons, SEO, Open Graph, Twitter cards, JSON-LD, schema, copyright, and
    contact links. No business phone number, email, company name, or address
    is hardcoded in public templates.

    Cached: the singleton is read by every request, including admin ones, and
    it changes only when an editor saves it, which invalidates the key.
    """
    from content.models import SiteSetting

    cached = cache.get(CACHE_KEY_SITE_SETTINGS)
    if cached is not None:
        return {"site_settings": cached}

    try:
        settings_obj = SiteSetting.load()
    except Exception:
        settings_obj = SiteSetting()
    cache.set(CACHE_KEY_SITE_SETTINGS, settings_obj, None)
    return {"site_settings": settings_obj}


def navigation(request):
    """Expose navigation menus to every template.

    One database configuration generates both desktop and mobile navigation.
    No separate hardcoded desktop/mobile service lists exist.

    The tree itself is cached; the "you are here" flag is applied per request.
    """
    current_path = getattr(request, "path", None)
    groups = {
        slug: mark_current(items, current_path)
        for slug, items in navigation_tree().items()
    }
    return {"navigation": groups}


def services(request):
    """Expose active services to every template.

    Used for homepage service cards, service dropdowns, lead form service
    selectors, and related content. Only services a visitor is allowed to see
    are returned, which is Service.objects.visible(): published, active, and
    within any scheduled publishing window.
    """
    from crm.models import Service

    cached = cache.get(CACHE_KEY_SERVICES)
    if cached is None:
        try:
            cached = list(Service.objects.visible().order_by("sort_order", "name"))
        except Exception:
            cached = []
        cache.set(CACHE_KEY_SERVICES, cached, None)
    return {"services": cached}
