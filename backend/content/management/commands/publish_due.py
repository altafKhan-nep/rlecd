"""Put scheduled pages live when their time arrives.

Scheduling only works if something notices when the moment passes. This
command is that something: cheap, idempotent, and safe to run from a cron
entry, a Render cron job, or by hand.

    python manage.py publish_due
    python manage.py publish_due --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from content.models import Page


class Command(BaseCommand):
    help = "Publish scheduled pages whose publish time has arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the pages that would go live without changing them.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        due = Page.objects.filter(
            status=Page.Status.SCHEDULED,
            is_published=True,
            scheduled_for__lte=now,
        ).order_by("scheduled_for")

        if options["dry_run"]:
            for page in due:
                self.stdout.write(f"  would publish {page.path} "
                                  f"(scheduled {page.scheduled_for})")
            self.stdout.write(f"{due.count()} page(s) due.")
            return

        published = 0
        for page in due:
            with transaction.atomic():
                # Re-read under the transaction: a cron job that overlaps
                # itself, or an editor who just unpublished the page, must not
                # result in two publishes or in overriding a deliberate change.
                fresh = Page.objects.select_for_update().get(pk=page.pk)
                if fresh.publish_if_due(now):
                    published += 1
                    self.stdout.write(self.style.SUCCESS(f"  published {fresh.path}"))

        if not published:
            self.stdout.write("Nothing due.")
        else:
            self.stdout.write(self.style.SUCCESS(f"{published} page(s) published."))
