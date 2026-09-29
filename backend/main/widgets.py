"""Form widgets for the admin.

The image-bearing content fields (`Page.og_image`, `Project.image`,
`TrustBadge.image`, `SectionImage.image`) are CharFields holding a path, and
deliberately so -- `content.models.MediaItem` explains why. A picker has to
respect that arrangement rather than replace it, so this widget *decorates* the
text input instead of removing it:

    the field stays text, so it can still hold a repo static path, a CDN URL
    or an upload made here, and every captured row keeps working untouched.

Choosing from the library writes the selected image's public path into that
same text input. An editor who needs a path the library does not have can
always type one, which is the escape hatch that makes a text field the right
underlying type in the first place.
"""
from django import forms
from django.contrib.admin.widgets import AdminTextInputWidget
from django.utils.translation import gettext_lazy as _

__all__ = [
    "MediaPathWidget",
    "MediaPickerFieldsMixin",
    "RichTextWidget",
]


class MediaPathWidget(AdminTextInputWidget):
    """A text input for an image path, with a picker beside it.

    Subclasses `AdminTextInputWidget` rather than `TextInput` so the field
    keeps the admin's own styling, its placeholder handling and its
    `class="vTextField"` hooks, which the changelist and inlines rely on. The
    picker is additive: clearing it, typing over it or ignoring it entirely
    all leave a perfectly valid form.

    Its template lives at `main/templates/admin/widgets/media_path.html`, not
    with the other admin templates in `frontend/templates/`. That is not a
    preference. `django.forms.renderers.DjangoTemplates` builds its own engine
    with a hard-coded `DIRS` of `django/forms/templates` plus `APP_DIRS`, so
    it never reads `TEMPLATES["DIRS"]` and cannot see `frontend/templates/`
    at all. A widget template there fails at render time with
    `TemplateDoesNotExist` on every form. The one alternative --
    `FORM_RENDERER = TemplatesSetting` -- is deprecated for removal in Django
    6.0 and would also stop finding `django/forms/widgets/*.html`, so the
    app's own template directory is the only sound home for it.
    """

    template_name = "admin/widgets/media_path.html"
    #: Names the JS uses to find the parts of this widget.
    picker_attrs = {
        "data-picker-open": "media-picker-open",
        "data-picker-preview": "media-picker-preview",
        "data-picker-clear": "media-picker-clear",
    }

    class Media:
        js = ("admin/js/media_picker.js",)


class RichTextWidget(forms.Textarea):
    """A formatting editor for section text, backed by a hidden input.

    The stored value is small HTML -- a heading, paragraphs, emphasis, lists,
    the occasional inline image. It is produced by this project's own editor,
    not typed, so nobody ever has to look at it. That is the point: the section
    body used to expose the captured site markup verbatim, which is what made
    the CMS unusable for anyone who is not a developer.

    The widget is still a form field, so it posts like every other field and
    works with inlines, validation and the changelist unchanged. Only the
    editing surface is replaced.
    """

    template_name = "admin/widgets/rich_text.html"

    class Media:
        js = ("admin/js/rich_text.js", "admin/js/media_picker.js")


class MediaPickerFieldsMixin:
    """Give named CharFields a `MediaPathWidget`, leaving the rest alone.

    `formfield_overrides` cannot be used for this. It is keyed by field *type*,
    so applying it to `CharField` would put a picker on `Page.title`,
    `Page.slug`, `Page.seo_title` and every other text field on the form. This
    mixin names the exact fields instead, so the picker appears on the image
    fields and nowhere else.

    The original widget class is checked before swapping, so a field an admin
    has deliberately overridden with something richer -- a Textarea, a
    widget with its own JS -- keeps it.
    """

    #: Field names on this model that should offer the library picker.
    media_picker_fields = ()

    #: Field names on this model that get the formatting editor.
    rich_text_fields = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in self.rich_text_fields and isinstance(
                field.widget, forms.Textarea):
            field.widget = RichTextWidget()
            return field
        if (db_field.name in self.media_picker_fields
                and isinstance(field.widget, forms.TextInput)
                and not isinstance(field.widget, MediaPathWidget)):
            # attrs is passed rather than mutated: the admin's own attributes
            # (maxlength, placeholder, the admin CSS class) have to survive.
            field.widget = MediaPathWidget(attrs=dict(field.widget.attrs))
        return field


#: Label reused by the widget template and the picker endpoint.
PICKER_BUTTON_LABEL = _("Choose from library")
