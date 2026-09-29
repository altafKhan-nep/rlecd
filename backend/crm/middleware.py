"""Bind the current request to the audit thread-local.

Without this the audit rows would all read `system`, because a `post_save`
signal has no way to reach the request that caused it. The middleware is what
makes "who changed this" answerable.

It is a small middleware on purpose. It does no work beyond stashing the
request and clearing it afterwards, and it clears in a `finally` so a view that
raises cannot leak one request's actor into the next request handled by the
same thread -- which would be a worse bug than having no actor at all.
"""
from crm.audit import audit_context, clear_audit_context


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        audit_context(request)
        try:
            return self.get_response(request)
        finally:
            clear_audit_context()
