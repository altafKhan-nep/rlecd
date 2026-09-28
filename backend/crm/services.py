"""Lead capture, scoring and notification.

`capture_lead()` is the single write path for every inbound enquiry. Both
public forms call it, so the two historically-divergent form contracts collapse
into one. The ordering inside it is deliberate and is the whole point:

    1. persist the Lead + CREATED activity inside a transaction
    2. only then attempt email

If step 2 raises, the lead is already safely in the database and the caller
still gets a success response. The old code had the inverse order, which meant
one SMTP hiccup destroyed the enquiry.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from .models import (
    Contact,
    Lead,
    LeadActivity,
    LeadSource,
    LeadStatus,
    Priority,
    Service,
)

logger = logging.getLogger(__name__)

HOT_THRESHOLD = 70
URGENT_THRESHOLD = 85

# Keywords that signal a buyer who is close to signing, versus a browser
# filling in a contact form for a phone number.
_HIGH_INTENT = (
    "estimate", "quote", "bid", "ready to start", "start soon", "this month",
    "next month", "contractor quote", "looking to hire", "budget",
)
_URGENT = ("urgent", "asap", "immediately", "emergency", "today")


class CaptureError(Exception):
    """Raised when the submission is missing something we cannot do without."""


def score_lead(*, message="", phone="", city_or_zip="", service=None):
    """Rule-based 0-100 lead score.

    Deliberately simple and inspectable: a salesperson should be able to look
    at a lead and understand why it scored what it did, rather than trusting an
    opaque model.
    """
    text = f"{message}".lower()

    score = 20  # baseline: a real person filled in a real form

    if phone:
        score += 15
    if city_or_zip:
        score += 10
    if service:
        score += 10

    message_length = len(message.strip())
    if message_length >= 200:
        score += 20
    elif message_length >= 60:
        score += 12
    elif message_length < 20:
        score -= 5

    if any(k in text for k in _HIGH_INTENT):
        score += 20
    if any(k in text for k in _URGENT):
        score += 15

    return max(0, min(100, score))


def priority_for(score):
    if score >= URGENT_THRESHOLD:
        return Priority.URGENT
    if score >= HOT_THRESHOLD:
        return Priority.HIGH
    return Priority.NORMAL


def resolve_service(raw):
    """Map a submitted service value onto the canonical catalogue.

    Accepts a slug, a name, or free text, so the two legacy form contracts
    (slugs from /contact/, display strings from /) both resolve. Returns
    ``(service_or_None, cleaned_raw)``.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, ""

    slugish = raw.lower().replace(" ", "-").replace("&", "and")
    service = Service.objects.filter(slug=slugish).first()
    if service is None:
        service = Service.objects.filter(name__iexact=raw).first()
    if service is None:
        service = Service.objects.filter(name__icontains=raw).first()
    return service, raw


def _attribution(data):
    return {
        "referrer": (data.get("HTTP_REFERER") or "")[:300],
        "landing_page": (data.get("HTTP_REFERER") or "")[:300],
        "utm_source": (data.get("utm_source") or "")[:100],
        "utm_medium": (data.get("utm_medium") or "")[:100],
        "utm_campaign": (data.get("utm_campaign") or "")[:150],
    }


@transaction.atomic
def capture_lead(*, name, email, message="", phone="", city_or_zip="",
                 service_raw="", source=LeadSource.OTHER, request=None):
    """Persist an enquiry and log its creation. Returns the Lead.

    Raises CaptureError if the required fields are missing.
    """
    name = (name or "").strip()
    email = (email or "").strip()
    message = (message or "").strip()
    phone = (phone or "").strip()
    city_or_zip = (city_or_zip or "").strip()

    if not name:
        raise CaptureError("Name is required.")
    if not email:
        raise CaptureError("Email address is required.")
    if not message:
        raise CaptureError("Please tell us about your project.")

    service, raw = resolve_service(service_raw)
    score = score_lead(
        message=message, phone=phone, city_or_zip=city_or_zip, service=service,
    )

    fields = {
        "name": name,
        "email": email,
        "phone": phone,
        "city_or_zip": city_or_zip,
        "service": service,
        "service_raw": raw,
        "message": message,
        "score": score,
        "priority": priority_for(score),
        "source": source,
    }
    if request is not None:
        fields.update(_attribution(request.META))

    lead = Lead.objects.create(**fields)
    LeadActivity.objects.create(
        lead=lead,
        kind=LeadActivity.Kind.CREATED,
        summary=f"Enquiry received from {lead.get_source_display().lower()}",
        detail=(
            f"Score {score}/100 · priority {lead.get_priority_display().lower()}"
            + (f" · service {service.name}" if service else "")
        ),
    )
    logger.info(
        "Lead captured lead=%s service=%s score=%s",
        lead.pk, service or raw or "-", score,
    )
    return lead


def notify(lead, *, send_to_customer=True):
    """Best-effort email. Never raises — a notification failure must not lose
    the lead, and must not surface as a 500 to the visitor."""
    service_label = (lead.service.name if lead.service
                     else lead.service_raw or "General enquiry")

    try:
        send_mail(
            subject=f"New {service_label} enquiry — {lead.name}",
            message=(
                f"Name: {lead.name}\n"
                f"Email: {lead.email}\n"
                f"Phone: {lead.phone or 'N/A'}\n"
                f"City/ZIP: {lead.city_or_zip or 'N/A'}\n"
                f"Service: {service_label}\n"
                f"Score: {lead.score}/100 ({lead.get_priority_display()})\n"
                f"Source: {lead.get_source_display()}\n\n"
                f"Project details:\n{lead.message}\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=settings.ADMIN_EMAIL,
            fail_silently=False,
        )
        LeadActivity.objects.create(
            lead=lead, kind=LeadActivity.Kind.EMAILED,
            summary="Notification email sent to the office",
        )
    except Exception:
        logger.exception("Admin notification failed for lead %s", lead.pk)

    if not send_to_customer or not lead.email:
        return

    try:
        send_mail(
            subject="We've received your request — Real Life Experience LLC",
            message=(
                f"Hi {lead.name},\n\n"
                "Thank you for reaching out to Real Life Experience LLC.\n\n"
                f"We have received your request for \"{service_label}\" and our "
                "team will review the details of your project shortly.\n\n"
                "Best regards,\nThe RLECD Team\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[lead.email],
            fail_silently=False,
        )
        LeadActivity.objects.create(
            lead=lead, kind=LeadActivity.Kind.EMAILED,
            summary="Acknowledgement email sent to the customer",
        )
    except Exception:
        logger.exception("Customer acknowledgement failed for lead %s", lead.pk)


def set_status(lead, new_status, *, actor=None, note=""):
    """Move a lead through the pipeline, recording the transition."""
    old_status = lead.status
    if old_status == new_status:
        return lead

    lead.status = new_status
    if new_status == LeadStatus.CONTACTED and not lead.contacted_at:
        from django.utils import timezone
        lead.contacted_at = timezone.now()
    lead.save(update_fields=["status", "contacted_at", "updated_at"])

    LeadActivity.objects.create(
        lead=lead,
        kind=LeadActivity.Kind.STATUS_CHANGED,
        summary=f"{lead.get_status_display()} (was {dict(LeadStatus.choices)[old_status]})",
        detail=note,
        actor=actor,
    )
    return lead


def assign(lead, user, *, actor=None):
    lead.owner = user
    lead.save(update_fields=["owner", "updated_at"])
    LeadActivity.objects.create(
        lead=lead, kind=LeadActivity.Kind.ASSIGNED,
        summary=f"Owner set to {user.get_username()}", actor=actor,
    )
    return lead


def convert_to_contact(lead, *, actor=None):
    """Promote a lead to a Contact exactly once."""
    if hasattr(lead, "converted_contact"):
        return lead.converted_contact

    contact = Contact.objects.create(
        name=lead.name, email=lead.email, phone=lead.phone,
        city_or_zip=lead.city_or_zip, owner=lead.owner,
        source_lead=lead, notes=lead.message,
    )
    LeadActivity.objects.create(
        lead=lead, kind=LeadActivity.Kind.NOTED,
        summary=f"Converted to contact #{contact.pk}", actor=actor,
    )
    return contact
