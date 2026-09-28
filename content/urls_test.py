"""URLConf used only by the content tests.

Adds a `/test/` route backed by a database Page row, and keeps every real
public route so the mirror regression tests exercise the actual URLConf.
"""
from django.urls import path

from content.views import render_page
from main.urls import urlpatterns as main_urlpatterns


def test_page(request):
    return render_page(request, "test")


urlpatterns = [
    path("test/", test_page, name="test_page"),
    *main_urlpatterns,
]
