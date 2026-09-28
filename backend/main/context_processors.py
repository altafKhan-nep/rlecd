from django.conf import settings


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
