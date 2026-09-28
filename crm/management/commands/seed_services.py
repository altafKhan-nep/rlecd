"""Seed the canonical Service catalogue from the site's own navigation.

The two public forms historically posted incompatible values for the service
field (display strings on the home page, slugs on the contact page). This table
is the single contract both now resolve against.

Idempotent: safe to re-run after adding a service to the navigation.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Service

# Ordered to match the live navbar.
SERVICES = [
    "Bathroom Remodeling",
    "Kitchen Remodeling",
    "Basement Finishing",
    "Painting",
    "Home Improvement",
    "Patios & Decks",
    "Cabinets",
    "Woodworking",
    "Hardscaping",
    "Walkway Designs",
    "Pergolas",
    "Lead Removal",
    "Shed Builder",
    "Lead Renovator",
    "Home Additions",
]


def slugify_service(name):
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


class Command(BaseCommand):
    help = "Create or update the Service catalogue from the site navigation."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="Mark services not in the list as inactive instead of leaving them.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        slugs = []

        for order, name in enumerate(SERVICES):
            slug = slugify_service(name)
            slugs.append(slug)
            service, was_created = Service.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "sort_order": order,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        if options["deactivate_missing"]:
            missing = Service.objects.exclude(slug__in=slugs).update(is_active=False)
            if missing:
                self.stdout.write(f"  deactivated {missing} stale service(s)")

        self.stdout.write(
            self.style.SUCCESS(
                f"Service catalogue ready: {created} created, {updated} updated, "
                f"{len(SERVICES)} total."
            )
        )
