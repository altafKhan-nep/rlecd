from django.conf import settings


def site(request):
    """Expose the canonical site origin to every template.

    Used for <link rel="canonical">, Open Graph URLs and the JSON-LD @id so
    those tags stay correct when the site is served from localhost, staging
    or production without editing templates.
    """
    return {"SITE_URL": settings.SITE_URL}
