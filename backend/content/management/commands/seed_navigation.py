"""Seed the Navigation and MenuItem tree that replaces the hardcoded navbars.

Until this existed, the Navigation and MenuItem tables were empty on every
database in the world: nothing in the codebase created a row. The navbar
reached the right places only through the fallback branch of the template,
which rebuilds the service list from the Service catalogue at render time. The
Studio's menu screens were therefore editing rows that no deployment created.

The order and the Font Awesome icons below are copied from the captured
navbar, which is the only record of them. They are worth seeding rather than
discarding: the icons are a design decision, and once they live here an editor
can change one without a deploy.

Idempotent, and safe to re-run. Items are matched on (navigation, parent, label)
so a re-run updates an edited label's icon instead of creating a second copy.
Use --prune to delete seeded items that are no longer in this file.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from content.models import MenuItem, Navigation

# The fifteen services, in the order the captured navbar listed them, with the
# icon it used for each. label must match crm.services.SERVICES so the seeded
# item lines up with the Service row it points at.
SERVICES = [
    ("Bathroom Remodeling", "fas fa-bath"),
    ("Kitchen Remodeling", "fas fa-utensils"),
    ("Basement Finishing", "fas fa-tools"),
    ("Painting", "fas fa-paint-roller"),
    ("Home Improvement", "fas fa-home"),
    ("Patios & Decks", "fas fa-hammer"),
    ("Cabinets", "fas fa-border-all"),
    ("Woodworking", "fas fa-tree"),
    ("Hardscaping", "fas fa-mountain"),
    ("Walkway Designs", "fas fa-shoe-prints"),
    ("Pergolas", "fas fa-archway"),
    ("Lead Removal", "fas fa-shield-virus"),
    ("Shed Builder", "fas fa-warehouse"),
    ("Lead Renovator", "fas fa-certificate"),
    ("Home Additions", "fas fa-plus-square"),
]

SERVICES_DROPDOWN_LABEL = "Services"

HEADER_LINKS = [
    ("Home", "/", {}),
    ("About", "/about/", {}),
    (SERVICES_DROPDOWN_LABEL, "", {}),
    ("Areas We Serve", "/areas-we-serve/", {}),
    ("Contact", "/contact/", {}),
    # A CTA renders the phone number and dial link from SiteSetting, so it is
    # seeded with a placeholder label and no url. Writing the number here would
    # bake today's phone number into a menu that would then disagree with the
    # settings an editor just changed.
    ("Call", "", {"is_cta": True, "icon": "fas fa-phone-alt"}),
]

FOOTER_LINKS = [
    ("Home", "/"),
    ("About Us", "/about/"),
    ("Areas We Serve", "/areas-we-serve/"),
    ("Contact", "/contact/"),
]


def _upsert(navigation, label, defaults, parent=None, sort_order=0):
    """Create or update one item, keyed on where it sits rather than its text.

    Keying on label would rename the row out from under itself when a label is
    corrected; keying on (parent, sort_order) would overwrite an editor's
    reordering. Label plus position is the only key that survives both.
    """
    item, created = MenuItem.objects.update_or_create(
        navigation=navigation, parent=parent, label=label,
        defaults={"sort_order": sort_order, **defaults})
    return item, created


class Command(BaseCommand):
    help = "Create or update the navigation menus from the captured navbar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete seeded items whose labels are no longer in this file.",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Replace the whole header and footer menus, discarding edits.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["rebuild"]:
            Navigation.objects.filter(slug__in=["header", "footer"]).delete()

        header, _ = Navigation.objects.update_or_create(
            slug="header", defaults={"name": "Header", "is_active": True,
                                     "sort_order": 0})
        footer, _ = Navigation.objects.update_or_create(
            slug="footer", defaults={"name": "Footer", "is_active": True,
                                     "sort_order": 1})

        created = updated = 0
        for item, was_created in self._seed_header(header):
            created += was_created
            updated += not was_created

        for item, was_created in self._seed_footer(footer):
            created += was_created
            updated += not was_created

        pruned = self._prune(header, footer) if options["prune"] else 0

        self.stdout.write(self.style.SUCCESS(
            f"Navigation ready: {created} created, {updated} updated"
            + (f", {pruned} pruned." if options["prune"] else ".")
        ))

    def _seed_header(self, header):
        # The order the captured navbar used: Home, About, the Services
        # dropdown, Areas We Serve, Contact, then the phone CTA. Getting this
        # wrong is not cosmetic -- the dropdown is the widest thing in the bar
        # and it belongs where the eye already expects it.
        for order, (label, url, extra) in enumerate(HEADER_LINKS):
            if label == SERVICES_DROPDOWN_LABEL:
                dropdown, was_created = _upsert(
                    header, label, {"url": "", "is_visible": True},
                    sort_order=order)
                yield dropdown, was_created
                for index, (child, was_made) in enumerate(
                        self._seed_service_children(header, dropdown)):
                    yield child, was_made
                continue
            defaults = {"url": url, "is_visible": True}
            defaults.update(extra)
            yield _upsert(header, label, defaults, sort_order=order)

    def _seed_service_children(self, header, dropdown):
        """Point each service item at its Service row rather than a literal url.

        A literal path would be a second copy of the mapping that already lives
        in crm.services and in urls.py, and it would go stale the first time a
        service slug changed. MenuItem.get_url() already knows how to resolve a
        Service reference, so the link follows the catalogue.
        """
        from crm.models import Service

        for index, (label, icon) in enumerate(SERVICES):
            service = Service.objects.filter(
                name=label).first() or Service.objects.filter(
                    slug=label.lower().replace(" ", "-").replace("&", "and")
            ).first()
            if service is None:
                self.stderr.write(self.style.WARNING(
                    f"  no Service row for {label!r}; "
                    f"run seed_services first. The nav item will link nowhere."
                ))
            yield _upsert(header, label,
                          {"icon": icon, "is_visible": True, "service": service},
                          parent=dropdown, sort_order=index)

    def _seed_footer(self, footer):
        for order, (label, url) in enumerate(FOOTER_LINKS):
            yield _upsert(footer, label, {"url": url, "is_visible": True},
                          sort_order=order)

    def _prune(self, header, footer):
        """Remove items this file used to create and no longer lists."""
        known = {label for label, _ in SERVICES} | \
                {label for label, _, _ in HEADER_LINKS}
        stale = MenuItem.objects.filter(navigation=header).exclude(
            label__in=known)
        removed = stale.count()
        stale.delete()

        footer_known = {label for label, _ in FOOTER_LINKS}
        stale_footer = MenuItem.objects.filter(navigation=footer).exclude(
            label__in=footer_known)
        removed += stale_footer.count()
        stale_footer.delete()
        return removed
