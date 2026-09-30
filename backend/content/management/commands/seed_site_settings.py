"""Seed the SiteSetting singleton with the business facts it was built to hold.

SiteSetting is a singleton row at pk=1, and nothing in the codebase ever
created it. SiteSetting.load() is a get_or_create, so the row appears on the
first request of a brand new database -- empty. Every public template reads the
phone number, address, email, and social links from this row and from nowhere
else; that was the whole point of the refactor that removed the hardcoded
copies. So a fresh Neon database produces a site that renders 200 and looks
structurally fine while carrying no business identity at all: no number to call,
no address, no social links, and a blank meta description.

The values below are copied from the templates as they stood before the facts
were extracted into this model, which is the only record of them. They are worth
seeding rather than left to a first-run editor: the phone number in particular is
load-bearing on this site, because the nav CTA and every tel: link are built
from it (see the is_cta note in seed_navigation).

Safe to re-run. Blank fields are filled in and existing values are left alone,
so a re-run cannot undo an editor's corrections in the Studio. Pass --force to
overwrite everything from the values in this file.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from content.models import SiteSetting

# Business facts as they were hardcoded in the pre-SiteSetting templates.
# "phone" is the dialable form and "phone_display" the human form, matching
# the two fields: effective_phone_tel strips the punctuation from phone for
# tel: links, and effective_phone_display prefers phone_display when rendering.
# The +1 country code is kept so the number dials outside the US.
DEFAULTS = {
    "company_name": "REAL LIFE EXPERIENCE LLC",
    "legal_company_name": "REAL LIFE EXPERIENCE LLC",
    "tagline": (
        "A premier design-build home remodeling company providing 'by the "
        "book' craftsmanship in Maryland for over 18 years."
    ),
    "phone": "+14438983143",
    "phone_display": "(443) 898-3143",
    "email": "agm@rlecd.com",
    "address": "Serving Greater Maryland Metro",
    "city": "Reisterstown",
    "state": "MD",
    "zip_code": "21136",
    "service_area_summary": "Serving Greater Maryland Metro and the surrounding counties.",
    "business_hours": "Monday - Friday, 8:00 AM - 6:00 PM",
    "years_in_business": 18,
    "facebook_url": "https://www.facebook.com/reallifeexperiencellc",
    "instagram_url": "https://www.instagram.com/rlecd/",
    "default_seo_title": "REAL LIFE EXPERIENCE LLC | Home Remodeling in Maryland",
    "seo_title_suffix": "Home Renovation & Remodeling",
    "default_seo_description": (
        "Contact REAL LIFE EXPERIENCE LLC for premium home remodeling and "
        "custom cabinetry in Reisterstown and across Maryland. Get a free "
        "renovation consultation today!"
    ),
    "default_seo_keywords": (
        "REAL LIFE EXPERIENCE LLC, home renovation Maryland, home remodeling "
        "MD, kitchen remodeling, bathroom renovation Reisterstown, basement "
        "finishing, home additions, residential construction, remodeling "
        "contractor"
    ),
    "default_og_image": "/static/img/rlecd_maryland_logo.png",
    "primary_cta_label": "Call Now",
}


class Command(BaseCommand):
    help = "Seed the SiteSetting singleton with the business facts from the original site."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing values, not just fill in blanks.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        force = options["force"]
        obj, created = SiteSetting.objects.get_or_create(pk=1)

        filled, skipped = [], []
        for field, value in DEFAULTS.items():
            current = getattr(obj, field)
            # company_name carries a non-blank model default, so a plain
            # truthiness test would report it as "kept" on a fresh row and make
            # the output imply a value was deliberately preserved. Only count
            # it as skipped when it actually differs from what we would write.
            if current == value:
                continue
            if current and not force:
                skipped.append(field)
                continue
            setattr(obj, field, value)
            filled.append(field)

        obj.save()

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created SiteSetting with {len(filled)} values."))
        elif filled:
            verb = "Overwrote" if force else "Filled"
            suffix = "value(s)" if force else "blank field(s)"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} {len(filled)} {suffix}: {', '.join(sorted(filled))}"))
        else:
            self.stdout.write("SiteSetting already complete; nothing to fill.")

        if skipped and not force:
            self.stdout.write(
                f"Kept {len(skipped)} existing value(s). Use --force to overwrite.")
        elif force:
            self.stdout.write(self.style.WARNING("--force: overwrote existing values."))

        phone = obj.effective_phone_tel
        if phone:
            self.stdout.write(f"  tel: link     -> tel:{phone}")
        self.stdout.write(f"  address       -> {obj.effective_address or '(empty)'}")
        if not obj.tagline:
            self.stdout.write(self.style.WARNING(
                "tagline is blank, so every page's meta description falls back "
                "to an empty string. Set it in the Studio."))
