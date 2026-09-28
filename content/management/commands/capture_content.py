"""Load the mirrored frontend into the database.

Reads the page templates that were captured from the live site, so the database
becomes a faithful copy of the live-verified markup rather than a
re-derivation of it. Safe to re-run.

    python manage.py capture_content            # create/refresh pages
    python manage.py capture_content --reset    # discard section edits first
    python manage.py capture_content --dry-run  # report, write nothing
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from content import importer
from content.render import clear_template_cache


class Command(BaseCommand):
    help = "Import the captured page templates into Page and Section rows."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete existing sections before importing.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without writing.")

    def handle(self, *args, **options):
        base_dir = settings.BASE_DIR

        if options["dry_run"]:
            try:
                specs = importer.collect_specs(base_dir)
            except FileNotFoundError as exc:
                raise CommandError(str(exc)) from exc
            for spec in specs:
                chunks = importer.chunk_lossless(spec["body"])
                self.stdout.write(
                    f"{spec['path']:<28} nav={spec['nav_variant'] or '-':<20} "
                    f"foot={spec['footer_variant'] or '-':<20} "
                    f"post={spec['post_variant'] or '-':<20} "
                    f"sections={len(chunks)}")
            self.stdout.write(self.style.WARNING("dry run: nothing written"))
            return

        try:
            created, updated, removed, written = importer.import_pages(
                base_dir, reset=options["reset"], log=self.stdout.write)
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        clear_template_cache()
        self.stdout.write(self.style.SUCCESS(
            f"pages: {created} created, {updated} updated, {removed} removed, "
            f"{written} sections written"))
