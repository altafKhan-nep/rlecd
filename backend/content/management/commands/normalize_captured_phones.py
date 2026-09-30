"""Repair tel: links inside captured Section HTML so they follow SiteSetting.

The original static site wrote its own phone links by hand, and the href did
not survive that trip: three captured sections carry

    <a href="tel:(443) 898-3143" class="phone-number">(443) 898-3143</a>

A display number in a dial link. Browsers cannot call "(443)" -- a tap on it
goes nowhere, and it sits on the homepage, the About page and the Contact page,
on a site whose entire purpose is being called. The visible text was already
right, which is why it reads correctly in a screenshot and fails in a handset.

capture_content copies the source HTML through verbatim, so re-capturing would
put the broken href straight back. This rewrites only the href of tel: links
and leaves the anchor text, classes and surrounding markup alone. The number
comes from SiteSetting, not from this file, so an editor who changes the phone
in the Studio and re-runs this gets consistent links without editing HTML.

Idempotent. Locked sections are skipped and reported rather than rewritten,
because a lock is a promise that a human has taken ownership of that markup.
Use --force to rewrite them anyway. Pass --dry-run to list the changes first.
"""
import re
from html import unescape

from django.core.management.base import BaseCommand
from django.db import transaction

from content.models import Section, SiteSetting

# The href of a tel: link, up to the closing quote of the attribute.
TEL_HREF = re.compile(r'(href=(["\']))tel:([^"\']*)\2', re.IGNORECASE)


class Command(BaseCommand):
    help = "Rewrite tel: hrefs in captured Section HTML from SiteSetting.phone."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would change and write nothing.")
        parser.add_argument("--force", action="store_true",
                            help="Also rewrite locked sections.")

    @transaction.atomic
    def handle(self, *args, **options):
        site = SiteSetting.load()
        target = site.effective_phone_tel
        if not target:
            self.stderr.write(self.style.ERROR(
                "SiteSetting has no phone, so there is no number to write. "
                "Run `manage.py seed_site_settings` first."))
            return

        wanted = f"tel:{target}"
        changed, skipped = [], []

        for section in Section.objects.exclude(content_html="").order_by("page__slug", "position"):
            found = TEL_HREF.findall(section.content_html)
            if not found:
                continue
            # unescape before comparing: a captured href may hold &amp; and
            # still be the same number.
            wrong = [f"tel:{unescape(h).strip()}" for _, _, h in found
                     if unescape(h).strip() != target]
            if not wrong:
                continue

            label = f"{section.page.slug}/{section.key}"
            if section.is_locked and not options["force"]:
                skipped.append((label, wrong))
                continue

            if options["dry_run"]:
                changed.append((label, sorted(set(wrong))))
                continue

            section.content_html = TEL_HREF.sub(
                lambda m: f"{m.group(1)}{wanted}{m.group(2)}", section.content_html)
            section.save(update_fields=["content_html", "updated_at"])
            changed.append((label, sorted(set(wrong))))

        for label, wrong in changed:
            verb = "would rewrite" if options["dry_run"] else "rewrote"
            self.stdout.write(f"{verb} {label}: {', '.join(wrong)} -> {wanted}")

        for label, wrong in skipped:
            self.stdout.write(self.style.WARNING(
                f"skipped locked section {label}: {', '.join(wrong)} "
                f"(use --force to rewrite)"))

        if not changed and not skipped:
            self.stdout.write(f"Every tel: link already points at {wanted}.")
        elif not options["dry_run"] and changed:
            self.stdout.write(self.style.SUCCESS(
                f"Updated {len(changed)} section(s)."))
