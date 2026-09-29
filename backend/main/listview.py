"""Shared changelist skin for every registered model.

Django's changelist already does the hard parts well -- filtering, sorting,
search, pagination and the action checkboxes all come for free. What the stock
view lacks is the furniture a content owner needs: a thumbnail so a row is
recognisable at a glance, per-row action icons instead of a hidden hover menu,
a result count that says what is on screen, and column totals for numeric
columns.

Rather than reimplement a list view, this mixin layers that furniture on top of
Django's. It plugs in through three documented hooks -- `get_list_display`,
`changelist_view` and `change_list_template` -- so adopting it is a one-line
change per ModelAdmin and there is no second set of query, filter or pagination
code to keep in sync.

Set on a subclass:

    studio_thumb         name of a method returning a thumbnail, or None
    studio_totals        {column label: queryset field} to sum over the filter
    studio_public_url    method(obj) -> public URL, adds a "view" row action
    studio_hide_actions  set True to opt out of the per-row action column
"""
import threading

from django.db.models import Sum
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from main.icons import icon

# list_display callables are invoked as `callable(obj)` with no request, but the
# row actions need one to run the permission checks. A per-thread slot is the
# least-wrong carrier: it cannot leak across threads, and it is overwritten at
# the start of every changelist request and read only while that changelist is
# rendering, so a stale value is never consulted.
_request_local = threading.local()


def _current_request():
    return getattr(_request_local, "request", None)


class StudioListMixin:
    """Thumbnail, row actions, counts and totals on Django's changelist."""

    change_list_template = "admin/studio_changelist.html"

    #: Method name to use as the leading thumbnail column.
    studio_thumb = None
    #: {"Column label": "queryset field"} summed across the current filter.
    studio_totals = {}
    #: Method returning a public URL for a row, or None.
    studio_public_url = None
    #: Opt out of the trailing actions column.
    studio_hide_actions = False

    def get_list_display(self, request):
        """Insert the thumbnail first and the actions last, once.

        A ModelAdmin that already lists these in its own `list_display` wins --
        the mixin never duplicates a column, which would render a header the
        admin did not ask for.
        """
        columns = list(super().get_list_display(request))
        if self.studio_thumb and self.studio_thumb not in columns:
            columns.insert(0, self.studio_thumb)
        if not self.studio_hide_actions and "studio_row_actions" not in columns:
            columns.append("studio_row_actions")
        return columns

    # ------------------------------------------------------------- row actions
    def studio_row_actions(self, obj):
        """Icon links for one row, filtered to what this user may actually do.

        Every link is permission-checked against the specific object, not just
        the model, so a row the user cannot delete never offers a delete button
        that would 403 on submit.
        """
        request = _current_request()
        if request is None:
            return ""

        namespace = f"{obj._meta.app_label}_{obj._meta.model_name}"
        actions = []

        if self.studio_public_url and self.has_view_permission(request, obj):
            public = self._safe_public_url(obj)
            if public:
                actions.append(self._action("globe", public, _("View on site")))

        if self.has_change_permission(request, obj):
            actions.append(self._action(
                "pencil",
                reverse(f"admin:{namespace}_change", args=[obj.pk]),
                _("Edit"),
            ))

        if self.has_delete_permission(request, obj):
            # Confirmation lives on the delete view itself; a confirm dialog
            # here would be a second implementation of the same check.
            actions.append(self._action(
                "trash",
                reverse(f"admin:{namespace}_delete", args=[obj.pk]),
                _("Delete"),
            ))

        if not actions:
            return format_html('<span class="row-actions is-empty">—</span>')
        # The fragments are already escaped individually by format_html, so
        # joining them is the one place that must not escape again.
        return format_html('<div class="row-actions">{}</div>',
                           mark_safe("".join(actions)))

    def _safe_public_url(self, obj):
        """Call `studio_public_url`, tolerating a row with no public page.

        A row can be a draft, or a record whose page was removed, and neither is
        a reason for the changelist to error.
        """
        try:
            return self.studio_public_url(obj)
        except Exception:
            return None

    def _action(self, name, url, label):
        return format_html(
            '<a class="row-action" href="{}" title="{}" aria-label="{}">{}</a>',
            url, label, label, icon(name),
        )

    studio_row_actions.short_description = ""

    # ------------------------------------------------------- summary + totals
    def changelist_view(self, request, extra_context=None):
        """Add the on-screen result count, filter chips and column totals.

        `cl.result_count` is the number after filtering, which is the number a
        reader wants ("12 of 48 pages"); the unfiltered total is carried
        alongside it so the effect of a filter stays visible.
        """
        _request_local.request = request
        response = super().changelist_view(request, extra_context)
        context = getattr(response, "context_data", None)
        cl = context.get("cl") if context else None
        if cl is not None:
            context["studio_totals"] = self._totals(cl)
            context["studio_shown"] = cl.result_count
            try:
                context["studio_all"] = self.get_queryset(request).count()
            except Exception:
                context["studio_all"] = cl.result_count
            context["studio_filters"] = self._active_filters(cl, request)
            meta = self.model._meta
            context["studio_unfiltered_url"] = reverse(
                f"admin:{meta.app_label}_{meta.model_name}_changelist")
        return response

    def _active_filters(self, cl, request):
        """Describe the filters that are currently narrowing the list.

        Read from the ChangeList's own filter specs rather than the raw query
        string, so a chip can name a filter the way the sidebar does ("Is
        published: Yes") instead of echoing the URL parameter, and so the spec
        can say exactly which parameters it consumed -- which is what lets a
        chip drop its own selection and leave the others alone.
        """
        chips = []
        for spec in self._filter_specs(cl, request):
            params = self._spec_params(spec)
            if not params:
                continue

            # The URL that keeps every other filter but drops this one, so a
            # chip removes exactly its own selection instead of resetting the
            # whole list.
            remaining = request.GET.copy()
            for name in params:
                remaining.pop(name, None)

            chips.append({
                "label": self._chip_label(spec, params, cl),
                "remove_url": (
                    f"{request.path}?{remaining.urlencode()}"
                    if remaining else request.path
                ),
            })
        return chips

    @staticmethod
    def _filter_specs(cl, request):
        """The changelist's list-filter specs, or none if they cannot be read."""
        try:
            return cl.get_filters(request)[0] or []
        except Exception:
            return []

    @staticmethod
    def _spec_params(spec):
        """The query parameters one filter spec actually consumed.

        `used_parameters` maps each name the spec looked at to the values it
        found, so a name only counts as active when it carries a value -- that
        is what keeps an untouched filter out of the chip strip, and what keeps
        a date filter that spans two parameters as a single chip. A filter that
        reports a single name instead of a mapping is read directly.
        """
        used = getattr(spec, "used_parameters", None)
        if isinstance(used, dict):
            active = [name for name, values in used.items()
                      if values not in (None, "", [], ())]
            if active:
                return active
        name = (getattr(spec, "parameter_name", None)
                or getattr(spec, "parameter", None))
        return [name] if name else []

    def _chip_label(self, spec, params, cl):
        """`"Title: current value"`, worded the way the filter sidebar words it."""
        title = getattr(spec, "title", "")
        if callable(title):
            title = title()
        title = capfirst(str(title).strip())
        value = self._spec_value(spec, params, cl)
        return f"{title}: {value}" if title else value

    @staticmethod
    def _spec_value(spec, params, cl):
        """The selected value, in the sidebar's own wording.

        Exactly one of a spec's choices is flagged `selected`, so that choice's
        label is what a reader already recognises -- "Yes" rather than "1". A
        spec that offers no usable choices falls back to the raw value instead
        of being dropped, so an unrecognised filter still produces a chip.
        """
        try:
            try:
                choices = spec.choices(cl)
            except TypeError:
                # A filter that takes no changelist still names itself.
                choices = spec.choices()
            for choice in choices:
                if isinstance(choice, dict) and choice.get("selected"):
                    return str(choice["display"])
        except Exception:
            pass

        used = getattr(spec, "used_parameters", None)
        raw = []
        for name in params:
            values = used.get(name) if isinstance(used, dict) else None
            raw.extend(list(values) if isinstance(values, (list, tuple))
                       else [values])
        return ", ".join(str(value) for value in raw
                         if value not in (None, "", [], ()))

    def _totals(self, cl):
        """Sum the declared numeric fields over the *filtered* queryset.

        Summing the whole table while the view shows a filtered slice is the
        classic way a totals row misleads, so this aggregates `cl.queryset`
        rather than the model's default manager.
        """
        if not self.studio_totals:
            return {}
        try:
            aggregates = {
                f"studio_sum_{index}": Sum(field)
                for index, field in enumerate(self.studio_totals.values())
            }
            row = cl.queryset.aggregate(**aggregates)
        except Exception:
            # A field that cannot be summed (a boolean, a relation) must not
            # take the whole changelist down.
            return {}
        return {
            label: row.get(f"studio_sum_{index}")
            for index, (label, _field) in enumerate(self.studio_totals.items())
        }


__all__ = ["StudioListMixin"]
