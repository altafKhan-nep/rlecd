"""Create or refresh the role groups described in crm.permissions.

Idempotent: re-running re-syncs each group's permission set, so a model
that gained or lost a permission does not silently keep a stale grant.

    python manage.py sync_roles
    python manage.py sync_roles --assign manager --user alice
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from crm.permissions import (
    Roles, assign_role, get_role_label, get_user_role, sync_role_groups,
)


class Command(BaseCommand):
    help = "Create or refresh the CRM role groups and optionally assign one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assign",
            choices=Roles.ALL,
            help="Role to grant to the user named by --user.",
        )
        parser.add_argument(
            "--user",
            help="Username to grant --assign to.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        role = options["assign"]
        username = options["user"]
        if bool(role) != bool(username):
            raise CommandError("--assign and --user must be given together.")

        groups, missing = sync_role_groups()
        for key, group in groups.items():
            self.stdout.write(f"  {get_role_label(key):<15} {group.name} ({group.permissions.count()} permissions)")

        if role:
            user_model = get_user_model()
            try:
                user = user_model.objects.get(username=username)
            except user_model.DoesNotExist:
                raise CommandError(f"No such user: {username}")
            previous = get_user_role(user)
            assign_role(user, role, replace=True)
            if previous and previous != role:
                self.stdout.write(f"  {username}: {previous} -> {role}")
            else:
                self.stdout.write(f"  {username}: {role}")

        if missing:
            self.stdout.write(self.style.WARNING(
                "Unknown permission codenames (check Roles.PERMISSIONS): "
                + ", ".join(sorted(missing))
            ))

        self.stdout.write(self.style.SUCCESS(f"{len(groups)} role groups synced."))
