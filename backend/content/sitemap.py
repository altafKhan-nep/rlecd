"""sitemap.xml and robots.txt, generated from what is actually live.

Both files are generated rather than hand-written so they cannot advertise a
page that is in Draft or a service that is switched off. Getting that wrong is
not cosmetic: a draft URL in a sitemap is an invitation for a search engine to
cache the wrong version of the site.
"""
from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from content.models import Page
from crm.models import Service


class PageSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        # live() already excludes drafts, archived pages and scheduled pages
        # whose time has not come.
        return Page.objects.live()

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        # '/' is served by the index route, which reverse() can name, so the
        # sitemap does not have to hardcode the root path.
        return reverse("index") if obj.path == "/" else obj.path


class ServiceSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.9

    def items(self):
        return Service.objects.visible()

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class SitemapView:
    """One document holding every published page and service.

    Django's own `sitemap` view would do this, but it needs a site id and a
    per-section `Site` record, and this site has exactly one hostname. The
    tradeoff is that `robots.txt` points at one URL rather than a sitemap
    index, which is also all a single-host site needs.
    """

    sections = (PageSitemap(), ServiceSitemap())

    def __call__(self, request):
        urls = []
        seen = set()
        for section in self.sections:
            for item in section.items():
                location = section.location(item)
                if location in seen:
                    # A service whose slug matches a page path would otherwise
                    # be listed twice, which reads to a crawler as a conflict.
                    continue
                seen.add(location)
                urls.append({
                    "location": request.build_absolute_uri(location),
                    "lastmod": section.lastmod(item),
                    "changefreq": section.changefreq,
                    "priority": section.priority,
                })
        return HttpResponse(
            render_to_string("content/sitemap.xml", {"urls": urls}),
            content_type="application/xml",
        )


sitemap_xml = SitemapView()


def robots_txt(request):
    """robots.txt for a single-host site.

    Indexing follows `Page.noindex` only in the sense that a site with every
    page marked noindex is itself not worth indexing; the per-page rule is
    emitted as a meta tag by the page template instead, which is what Google
    actually reads for noindex.
    """
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /media/",
        "",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap_xml'))}",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def every_page_is_noindexed():
    """True when no live page wants to be indexed.

    Used by the admin's SEO checks to tell an editor that the whole site is
    invisible to search engines, which is otherwise invisible until traffic
    stops.
    """
    live = Page.objects.live()
    return bool(live.exists()) and not live.exclude(noindex=True).exists()
