"""Audit logging for CRM mutations.

Two pieces, deliberately kept apart:

`AuditContextMiddleware` puts the current request's actor, IP and path into a
thread-local. The signal handlers read it. This is what lets an audit row say
*who* made a change, which a bare `post_save` signal cannot -- by the time the
signal fires, the request that caused it is usually out of scope.

The signal handlers then record every create, update and delete of a watched
model. Signals rather than explicit `AuditLog.objects.create(...)` calls at
each mutation site, because the failure mode of the explicit approach is a new
code path that forgets to log, and the whole point of this table is that it
cannot be forgotten.

Known gap
---------
`QuerySet.update()` and `bulk_create()` do NOT emit `pre_save`/`post_save`, so
a bulk update is invisible to this table. That is a deliberate trade: those
calls are few and identifiable, and the admin's own bulk actions loop and call
`save()` precisely so they are recorded. If you add a `queryset.update()` on
an audited model, it will not be logged -- write the loop instead, or log the
change by hand. `crm.tests.AuditLogTests` pins this behaviour so the
limitation cannot be mistaken for a guarantee.

Nothing here may raise. An audit failure is logged and swallowed: a bug in the
audit path must not roll back the business transaction it was meant to record,
and must certainly not surface as a 500 to a member of staff.
"""
import logging
import threading

from django.db.models.signals import post_delete, post_save, pre_save
from django.utils.functional import SimpleLazyObject

logger = logging.getLogger(__name__)

# One slot per thread. A request is handled by one thread, so this is enough to
# attribute a mutation to the request that caused it without passing the request
# through every service call.
_context = threading.local()


def _current_request():
    return getattr(_context, "request", None)


def audit_context(request):
    """Bind the current request to this thread for the duration of a `with`.

    Used by the middleware and by tests. Returns the request so it can be used
    as a decorator or a context manager.
    """
    _context.request = request
    return request


def clear_audit_context():
    _context.request = None


def _actor(request):
    user = getattr(request, "user", None)
    if user is None or isinstance(user, SimpleLazyObject):
        # A lazy user that has not been touched yet is not an authenticated
        # user; touching it here would force a query on every mutation.
        return None
    return user if user.is_authenticated else None


def _ip(request):
    if request is None:
        return None
    # Render terminates TLS in front of the app, so the peer address is the
    # proxy's. The real client is the left-most forwarded entry.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _path(request):
    if request is None:
        return ""
    return request.path[:300]


# --- which fields get diffed ---------------------------------------------
#
# A per-model allowlist rather than "every field", for two reasons. Some fields
# are large free text (a lead's `message`), and diffing them would turn one
# edit into a wall of near-identical rows. And some fields are pure bookkeeping
# that nobody will ever ask about. Everything not listed here is still logged
# as a row -- it just does not contribute to the field-level diff.
#
# Add a field here when someone asks "who changed X", not before.
AUDIT_FIELDS = {
    "crm.service": ("name", "slug", "is_active", "sort_order"),
    # `message` is deliberately absent: it is the customer's own submission and
    # runs to paragraphs, so diffing it would put the whole enquiry in every
    # audit row. `notes` is kept -- it is a short staff annotation, and knowing
    # it changed is the point.
    "crm.lead": (
        "name", "email", "phone", "city_or_zip", "service_id", "service_raw",
        "status", "priority", "score", "source", "owner_id", "notes",
    ),
    "crm.leadactivity": ("kind", "summary", "detail", "actor_id"),
    "crm.task": ("title", "due_at", "done", "completed_at", "assigned_to_id"),
    "crm.contact": ("name", "email", "phone", "city_or_zip", "owner_id",
                    "source_lead_id", "notes"),
    "crm.teammember": ("name", "user_id", "service_id", "territory",
                       "is_active", "sort_order", "assignment_count"),
}

# Never diff these, whatever the model. A captured page's `head_html` is
# kilobytes of markup that changes on every save.
AUDIT_DENYLIST = {"head_html", "main_attrs", "nav_variant", "footer_variant",
                 "post_variant", "content_html"}


def _watched_fields(model):
    return AUDIT_FIELDS.get(f"{model._meta.app_label}.{model._meta.model_name}")


def _old_values(instance):
    return getattr(instance, "_audit_old_values", {})


def _stash_old_values(sender, instance, **kwargs):
    """Remember the row as it was, so post_save can say what moved.

    Runs on pre_save, before the new values are written. Only for watched
    models, and only for rows that already exist -- a new row has nothing to
    diff against and is logged as a create instead.
    """
    if not _watched_fields(sender) or instance.pk is None:
        return
    try:
        previous = sender.objects.filter(pk=instance.pk).values(
            *_watched_fields(sender)
        ).first()
    except Exception:
        # A missing table (unmigrated app) must not break the save.
        return
    if previous is not None:
        instance._audit_old_values = previous


def _record(sender, instance, *, action, changes=None, summary=""):
    """Write one audit row. Never raises."""
    try:
        request = _current_request()
        from crm.models import AuditLog

        AuditLog.objects.create(
            actor=_actor(request),
            action=action,
            model=f"{sender._meta.app_label}.{sender._meta.model_name}",
            object_id=str(instance.pk),
            object_repr=str(instance)[:300],
            changes=changes or {},
            summary=summary[:300],
            ip_address=_ip(request),
            path=_path(request),
        )
    except Exception:
        # Deliberately broad: this must not take a business transaction down.
        logger.exception("Audit logging failed for %s %s", sender.__name__, instance.pk)


def _on_save(sender, instance, created, **kwargs):
    fields = _watched_fields(sender)
    if not fields:
        return
    if created:
        _record(sender, instance, action="create",
                 summary=f"Created {sender._meta.verbose_name}")
        return

    old = _old_values(instance)
    if not old:
        # pre_save could not read the previous row (unmigrated table, or the
        # row was created inside this same request). Log the change without a
        # diff rather than skipping it.
        _record(sender, instance, action="update",
                 summary=f"Updated {sender._meta.verbose_name}")
        return

    changes = {}
    for field in fields:
        before, after = old.get(field), getattr(instance, field, None)
        if before != after:
            changes[field] = {"from": before, "to": after}
    if not changes:
        # A save that changed nothing worth recording. Writing a row for it
        # would bury the rows that matter.
        return
    _record(sender, instance, action="update", changes=changes,
            summary=_describe(changes))


def _on_delete(sender, instance, **kwargs):
    if not _watched_fields(sender):
        return
    _record(sender, instance, action="delete",
             summary=f"Deleted {sender._meta.verbose_name}")


def _describe(changes):
    """One line naming the fields that moved, e.g. "status, owner".

    Field names only -- the values are in `changes`, and a summary that
    repeated them would be a second copy to keep in sync.
    """
    return ", ".join(changes)[:300]


def connect():
    """Wire the handlers. Called once from CrmConfig.ready().

    Connected in ready() rather than at module import so the app registry is
    populated and the models above are importable.
    """
    watched = _watched_models()
    for model in watched:
        pre_save.connect(_stash_old_values, sender=model, dispatch_uid="audit.stash")
        post_save.connect(_on_save, sender=model, dispatch_uid="audit.save")
        post_delete.connect(_on_delete, sender=model, dispatch_uid="audit.delete")


def _watched_models():
    """Every model with an entry in AUDIT_FIELDS, resolved lazily.

    Resolved here rather than at import time so a missing model (an app not
    installed, a field renamed) degrades to "that model is not audited" instead
    of breaking `manage.py` at startup.
    """
    from django.apps import apps

    models = []
    for label in AUDIT_FIELDS:
        app_label, model_name = label.split(".")
        try:
            models.append(apps.get_model(app_label, model_name))
        except LookupError:
            logger.warning("Audit: %s is in AUDIT_FIELDS but not installed", label)
    return models
