"""Cache invalidation utilities for content changes.

Implements correct cache invalidation so that CRM edits immediately appear
on the public website. If Redis is unavailable, the site fails gracefully
rather than becoming unusable.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY_SITE_SETTINGS = "site_settings"
CACHE_KEY_NAVIGATION = "navigation"
CACHE_KEY_SERVICES = "services"
CACHE_KEY_PAGE_PREFIX = "page:"
CACHE_KEY_SERVICE_PREFIX = "service:"


def invalidate_site_settings_cache():
    """Invalidate the global SiteSetting cache.

    Called when SiteSettings are saved. Affects navbar, footer, contact
    sections, CTA buttons, SEO, Open Graph, Twitter cards, JSON-LD, schema,
    copyright, and contact links.
    """
    try:
        cache.delete(CACHE_KEY_SITE_SETTINGS)
    except Exception:
        logger.warning("Failed to invalidate site settings cache", exc_info=True)


def invalidate_navigation_cache():
    """Invalidate the navigation cache.

    Called when Navigation or MenuItem rows change. Affects desktop nav,
    mobile nav, service dropdowns, and footer links.
    """
    try:
        cache.delete(CACHE_KEY_NAVIGATION)
    except Exception:
        logger.warning("Failed to invalidate navigation cache", exc_info=True)


def invalidate_services_cache():
    """Invalidate the services cache.

    Called when Service rows change. Affects homepage service cards,
    service dropdowns, lead form service selectors, and service pages.
    """
    try:
        cache.delete(CACHE_KEY_SERVICES)
    except Exception:
        logger.warning("Failed to invalidate services cache", exc_info=True)


def invalidate_page_cache(page_id):
    """Invalidate a specific page's cache.

    Called when a Page or its Sections change.
    """
    try:
        cache.delete(f"{CACHE_KEY_PAGE_PREFIX}{page_id}")
    except Exception:
        logger.warning(f"Failed to invalidate page cache for {page_id}", exc_info=True)


def invalidate_service_cache(service_id):
    """Invalidate a specific service's cache.

    Called when a Service changes. Affects the service page, homepage
    service cards, navigation, and lead form service selector.
    """
    try:
        cache.delete(f"{CACHE_KEY_SERVICE_PREFIX}{service_id}")
    except Exception:
        logger.warning(f"Failed to invalidate service cache for {service_id}", exc_info=True)


def invalidate_all_content_caches():
    """Invalidate all content caches.

    Called after bulk operations or imports.
    """
    invalidate_site_settings_cache()
    invalidate_navigation_cache()
    invalidate_services_cache()
