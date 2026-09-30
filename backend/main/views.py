"""Public views.

Every GET renders a page from its database row via `content.views.render_page`,
so the frontend is editable from the CRM. The view names and urls.py are
unchanged, which keeps the `{% url %}` tags inside stored content valid and the
live mirror intact.
"""
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from content.views import render_page
from crm import services
from crm.models import LeadSource, Service

logger = logging.getLogger(__name__)


def _handle_enquiry(request, *, source, redirect_to):
    """Shared POST handler for both public enquiry forms.

    The lead is written to the database first, with its notifications queued in
    the same transaction, and the visitor is redirected. No mail server is
    touched here: the request should not be able to fail, or hang, because
    somebody's SMTP provider is having a bad afternoon. `manage.py send_outbox`
    does the sending.
    """
    try:
        lead = services.capture_lead(
            name=request.POST.get("name"),
            email=request.POST.get("email"),
            phone=request.POST.get("phone", ""),
            city_or_zip=request.POST.get("city_or_zip", "") or request.POST.get("city", ""),
            service_raw=request.POST.get("service"),
            message=request.POST.get("message"),
            source=source,
            request=request,
        )
    except services.CaptureError as exc:
        messages.error(request, str(exc))
        return redirect(redirect_to)

    messages.success(request, "Your message has been sent successfully!")
    return redirect(redirect_to)


def index(request):
    if request.method == 'POST':
        return _handle_enquiry(
            request, source=LeadSource.HOME_FORM, redirect_to='/#contact',
        )
    return render_page(request, 'index')


def about(request):
    return render_page(request, 'about')


def contact(request):
    if request.method == 'POST':
        return _handle_enquiry(
            request, source=LeadSource.CONTACT_FORM, redirect_to='/contact/',
        )
    return render_page(request, 'contact')


def areas_we_serve(request):
    return render_page(request, 'areas_we_serve')


def bathroom_remodeling(request):
    return render_page(request, 'bathroom')


def kitchen_remodeling(request):
    return render_page(request, 'kitchen')


def basement_finishing(request):
    return render_page(request, 'basement')


def home_additions(request):
    return render_page(request, 'additions')


def painting(request):
    return render_page(request, 'painting')


def home_improvement(request):
    return render_page(request, 'home_improvement')


def patios_decks(request):
    return render_page(request, 'patios_decks')


def cabinets(request):
    return render_page(request, 'cabinets')


def woodworking(request):
    return render_page(request, 'woodworking')


def hardscaping(request):
    return render_page(request, 'hardscaping')


def walkway_designs(request):
    return render_page(request, 'walkways')


def pergolas(request):
    return render_page(request, 'pergolas')


def lead_removal(request):
    return render_page(request, 'lead_removal')


def shed_builder(request):
    return render_page(request, 'sheds')


def lead_renovator(request):
    return render_page(request, 'lead_renovator')


def service_page(request, slug):
    """Serve a CRM service that has no captured page of its own.

    The fifteen services the site shipped with keep their own URLs in
    urls.py and their captured markup; this catches everything after them, so
    a service added in the CRM has a working address from the moment it is
    saved rather than a catalogue row that links to a 404.

    Two cases are refused rather than guessed at. An inactive or unpublished
    service is a 404, because a hidden service whose page is reachable is not
    hidden. And a slug that belongs to no service is also a 404, which keeps
    this catch-all from swallowing typos that would otherwise render a
    branded "nothing here" page instead of the 404 a visitor should see.
    """
    service = Service.objects.visible().filter(slug=slug).first()
    if service is None:
        raise Http404(f"No service for slug {slug!r}")
    return render(request, "service.html", {"service": service})
