"""Send the queued enquiry emails.

The public forms write to `crm.EmailOutbox` and return; this is what turns
those rows into mail. Run it from a cron entry (every minute is reasonable),
from a Render cron job, or by hand when somebody notices the queue is deep.

    python manage.py send_outbox
    python manage.py send_outbox --limit 200 --verbose
    python manage.py send_outbox --retry-failed

Failures are never fatal to the run: one bad address must not stop the next
message from going out, which is why the exit code only reports that
something is still stuck.
"""
from django.core.management.base import BaseCommand

from crm.models import EmailOutbox


class Command(BaseCommand):
    help = "Send queued enquiry notifications."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=50,
            help="Maximum messages to send in one run.",
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Also retry messages that have used up their attempts.",
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Name each message as it is sent.",
        )

    def handle(self, *args, **options):
        if options["retry_failed"]:
            stuck = EmailOutbox.objects.filter(
                status=EmailOutbox.Status.FAILED).count()
            if stuck:
                EmailOutbox.objects.filter(
                    status=EmailOutbox.Status.FAILED).update(
                    status=EmailOutbox.Status.QUEUED, attempts=0)
                self.stdout.write(f"Re-queued {stuck} failed message(s).")

        from crm import services

        sent, failed = services.flush_outbox(limit=options["limit"])

        if options["verbose"]:
            for row in (EmailOutbox.objects.filter(status=EmailOutbox.Status.SENT)
                        .order_by("-sent_at")[:sent]):
                self.stdout.write(f"  sent to {row.recipient}: {row.subject}")

        if failed:
            self.stdout.write(self.style.WARNING(
                f"{failed} message(s) failed and will be retried."))

        exhausted = EmailOutbox.objects.filter(status=EmailOutbox.Status.QUEUED).count()
        if exhausted:
            self.stdout.write(self.style.WARNING(
                f"{exhausted} message(s) are still queued or waiting to retry. "
                "Run with --retry-failed to force them through."))

        self.stdout.write(self.style.SUCCESS(
            f"{sent} sent, {failed} failed."))
