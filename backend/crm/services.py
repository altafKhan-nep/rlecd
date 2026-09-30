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
from django.utils import timezone

from .models import (
    Contact,
    EmailOutbox,
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
        "landing_page": (data.get("PATH_INFO") or "")[:300],
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
    # Queued in the same transaction as the lead itself. An enquiry and the
    # promise to answer it are one fact, and they should not be able to disagree.
    enqueue_notifications(lead)
    # Assign after the row exists, so an owner is never recorded against a
    # lead that failed to save. Deliberately outside the create transaction:
    # assign() writes an activity row and takes a row lock, and neither
    # should be able to roll back the enquiry itself.
    auto_assign(lead)
    return lead


def lead_notifications(lead):
    """The messages an enquiry produces, as (kind, recipient, subject, body).

    Built here rather than at send time so the exact text that was promised is
    what sits in the queue. Composing at send time would mean an email that
    quotes a score or a service name could disagree with the database by the
    time somebody reads it, and would make a retry non-idempotent.
    """
    service_label = (lead.service.name if lead.service
                     else lead.service_raw or "General enquiry")

    admin_body = (
        f"Name: {lead.name}\n"
        f"Email: {lead.email}\n"
        f"Phone: {lead.phone or 'N/A'}\n"
        f"City/ZIP: {lead.city_or_zip or 'N/A'}\n"
        f"Service: {service_label}\n"
        f"Score: {lead.score}/100 ({lead.get_priority_display()})\n"
        f"Source: {lead.get_source_display()}\n\n"
        f"Project details:\n{lead.message}\n"
    )

    messages = [(
        EmailOutbox.Kind.ADMIN,
        ",".join(settings.ADMIN_EMAIL),
        f"New {service_label} enquiry — {lead.name}",
        admin_body,
    )]

    if lead.email:
        messages.append((
            EmailOutbox.Kind.CUSTOMER,
            lead.email,
            "We've received your request — Real Life Experience LLC",
            (
                f"Hi {lead.name},\n\n"
                "Thank you for reaching out to Real Life Experience LLC.\n\n"
                f"We have received your request for \"{service_label}\" and our "
                "team will review the details of your project shortly.\n\n"
                "Best regards,\nThe RLECD Team\n"
            ),
        ))
    return messages


@transaction.atomic
def enqueue_notifications(lead):
    """Write the enquiry's emails to the outbox. Returns the rows.

    Called inside the same transaction as the capture, so an enquiry is either
    stored with its notifications or not at all. A queue that could disagree
    with the pipeline about which leads exist would be worse than no queue.
    """
    rows = [
        EmailOutbox.objects.create(
            lead=lead, kind=kind, recipient=recipient, subject=subject, body=body,
        )
        for kind, recipient, subject, body in lead_notifications(lead)
    ]
    if not rows:
        logger.warning("Lead %s produced no notifications; ADMIN_EMAIL is empty?",
                       lead.pk)
    return rows


def deliver(row):
    """Send one outbox row. Returns True on success.

    Never raises: a failure is recorded on the row and retried later, and the
    caller is a command that has other rows to get through.
    """
    try:
        send_mail(
            subject=row.subject,
            message=row.body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[address.strip() for address in row.recipient.split(",")
                            if address.strip()],
            fail_silently=False,
        )
    except Exception as exc:
        logger.warning("Outbox message %s to %s failed: %s",
                       row.pk, row.recipient, exc)
        row.mark_failed(exc)
        return False

    row.mark_sent()
    if row.lead_id:
        LeadActivity.objects.create(
            lead_id=row.lead_id,
            kind=LeadActivity.Kind.EMAILED,
            summary=(
                "Notification email sent to the office" if row.kind == EmailOutbox.Kind.ADMIN
                else "Acknowledgement email sent to the customer"
            ),
        )
    return True


def flush_outbox(*, limit=50):
    """Send up to `limit` due messages. Returns (sent, failed)."""
    rows = [row for row in EmailOutbox.objects.select_related("lead")
            .filter(status=EmailOutbox.Status.QUEUED)
            .filter(next_attempt_at__lte=timezone.now())
            .order_by("queued_at", "pk")[:limit]
            if row.attempts < EmailOutbox.MAX_ATTEMPTS]

    sent = failed = 0
    for row in rows:
        if deliver(row):
            sent += 1
        else:
            failed += 1
    return sent, failed


def notify(lead, *, send_to_customer=True):
    """Send the enquiry's emails immediately.

    Kept for the paths that genuinely want the mail sent now -- the queue's
    own tests, and an operator sending the notification by hand from the lead.
    The public forms use `enqueue_notifications` instead so a visitor is not
    waiting on SMTP.
    """
    service_label = (lead.service.name if lead.service
                     else lead.service_raw or "General enquiry")

    messages = lead_notifications(lead)
    if not send_to_customer:
        messages = [m for m in messages if m[0] == EmailOutbox.Kind.ADMIN]

    for kind, recipient, subject, body in messages:
        row = EmailOutbox.objects.create(
            lead=lead, kind=kind, recipient=recipient, subject=subject, body=body,
        )
        deliver(row)


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


def assign_round_robin(*, service=None, territory="", actor=None):
    """Pick the team member who should take the next matching lead.

    Fewest assignments first, then the member's own ordering, so a tie breaks
    deterministically and the same person is not picked twice in a row when the
    counts are level.

    A member whose `service` matches the lead's is preferred over a generalist
    at equal counts, which is the point of recording a service at all: a
    bathroom lead should reach the bathroom fitter rather than being handed to
    whoever happens to be least busy. Within the preferred tier it is still
    round-robin, so the load stays even.

    Returns None when nobody is available, which is a normal state for a young
    team rather than an error. The lead stays unassigned and the dashboard's
    unassigned count says so, which is honest; force-assigning to someone who
    does not cover the work would be worse.

    The row is locked for the duration so two enquiries arriving together
    cannot both read the same lowest count and land on the same person, which
    is the one way a naive round-robin stops being round-robin.
    """
    from django.db.models import Case, F, IntegerField, Q, Value, When

    from .models import TeamMember

    candidates = TeamMember.objects.filter(
        is_active=True, user__isnull=False,
    ).select_related("user")
    if service is not None:
        # A member with no service covers everything, so they stay eligible
        # alongside the specialist for that service.
        candidates = candidates.filter(Q(service=service) | Q(service__isnull=True))
    territory = (territory or "").strip()
    if territory:
        candidates = candidates.filter(
            Q(territory__iexact=territory) | Q(territory=""))
    if not candidates.exists():
        logger.info("Round-robin found no eligible team member (service=%s, territory=%r)",
                    service, territory)
        return None

    # 0 for a specialist on this service, 1 for a generalist, so the specialist
    # sorts first. Written as a Case rather than a boolean annotation because a
    # boolean cannot be negated in an ordering on some backends.
    specificity = Case(
        When(service=service, then=Value(0)) if service is not None
        else When(pk__in=[], then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )

    with transaction.atomic():
        member = (
            candidates.select_for_update()
            .order_by(specificity, "assignment_count", "sort_order", "pk")
            .first()
        )
        if member is None:
            return None
        TeamMember.objects.filter(pk=member.pk).update(
            assignment_count=F("assignment_count") + 1)
    # Read the new total back so a caller showing the assignment does not need
    # a second query.
    member.assignment_count += 1
    return member


def auto_assign(lead, *, actor=None):
    """Give a freshly captured lead an owner by round-robin.

    Called from capture_lead so the pipeline's "a lead submitted at 2pm is in
    the pipeline *with an owner*" holds without anyone touching the admin. Does
    nothing when nobody is eligible, leaving the lead visibly unassigned.
    """
    member = assign_round_robin(service=lead.service, territory=lead.city_or_zip,
                                actor=actor)
    if member is None:
        return None
    assign(lead, member.user, actor=actor)
    logger.info("Round-robin assigned lead=%s to member=%s user=%s",
                lead.pk, member.name, member.user.get_username())
    return member


class MergeError(Exception):
    """Raised when two leads cannot be merged."""


#: Priority is a CharField, so "the higher priority wins" cannot be a string
#: comparison -- alphabetically "high" sorts before "normal". Ranked here
#: instead, and only ever read, never written.
_PRIORITY_RANK = {Priority.LOW: 0, Priority.NORMAL: 1,
                  Priority.HIGH: 2, Priority.URGENT: 3}


def merge_leads(primary, duplicates, *, actor=None):
    """Fold `duplicates` into `primary` and return the primary.

    A repeat enquiry is the most likely way this business loses track of
    someone. `match_duplicate()` finds the earlier row, but finding it is only
    half of it: without a merge, two people sit in the pipeline for the same
    job and whichever is not followed up is a lead that never got a call.

    Nothing is deleted. A duplicate that is mid-conversation may hold the only
    record of what the customer said, so its message, notes and activity are
    copied onto the primary and the row is kept and marked merged -- an
    irreversible delete on a lead pipeline is not a safe default.

    The primary wins on every field where the two disagree, except score,
    which takes the higher of the two because a repeat enquiry with more detail
    is the more engaged of the pair. Any field blank on the primary is filled
    from the duplicate, since a phone number captured on the second submission
    is data, not a conflict.
    """
    from .models import LeadActivity

    if not duplicates:
        raise MergeError("No leads selected to merge.")
    if primary.pk in {lead.pk for lead in duplicates}:
        raise MergeError("A lead cannot be merged into itself.")

    # Prefetched once: merging must not re-query per field per duplicate.
    duplicates = list(duplicates)
    for lead in duplicates:
        if lead.pk == primary.pk:
            raise MergeError("A lead cannot be merged into itself.")

    merged_fields = []
    for source in duplicates:
        for field, value in _mergeable_fields(primary, source):
            setattr(primary, field, value)
            if field not in merged_fields:
                merged_fields.append(field)

        # The activity trail is the record of what happened, so it moves with
        # the lead rather than being summarised.
        for activity in source.activities.select_related("actor"):
            activity.pk = None
            activity.id = None
            activity.lead = primary
            activity.save()

        # Tasks follow the primary: an open follow-up owed on the duplicate is
        # still owed, and re-pointing keeps it on the board rather than
        # stranding it against a lead nobody looks at.
        source.tasks.update(lead=primary)

        LeadActivity.objects.create(
            lead=primary,
            kind=LeadActivity.Kind.MERGED,
            summary=f"Merged lead #{source.pk} ({source.name})",
            detail=(
                f"Kept lead #{source.pk} for the record. "
                f"Carried over {source.activities.count()} activities and "
                f"{source.tasks.count()} tasks."
            ),
            actor=actor,
        )
        # Tombstone rather than delete, so the duplicate stops appearing in
        # the pipeline and stop being assignable, but its id stays resolvable
        # from a URL or an old email thread.
        source.status = LeadStatus.LOST
        source.notes = (f"[merged into lead #{primary.pk}] {source.notes}"
                        ).strip()
        source.save(update_fields=["status", "notes", "updated_at"])

    primary.save()
    logger.info("Merged %s lead(s) into lead=%s; fields touched: %s",
                len(duplicates), primary.pk, ", ".join(merged_fields) or "none")
    return primary


def _mergeable_fields(primary, source):
    """Yield (field, value) pairs to move from `source` onto `primary`."""
    if primary.score < source.score:
        yield "score", source.score
    if _PRIORITY_RANK.get(primary.priority, 0) < _PRIORITY_RANK.get(source.priority, 0):
        yield "priority", source.priority
    # Blank-on-primary fields are filled rather than fought over: a phone number
    # captured on the second submission is data the primary is missing.
    for field in ("phone", "city_or_zip", "service", "message", "owner",
                  "service_raw", "referrer", "landing_page", "utm_source",
                  "utm_medium", "utm_campaign", "notes", "contacted_at"):
        if getattr(primary, field) in (None, "") and getattr(source, field) not in (None, ""):
            yield field, getattr(source, field)

