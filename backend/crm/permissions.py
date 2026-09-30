"""Role-based permissions for CRM.

Defines roles and their permissions:
- Super Admin: full access
- Administrator: full access except user management
- Manager: manage leads, tasks, contacts; view reports
- Sales: manage own leads, tasks; view own contacts
- Content Editor: edit content only; NO access to leads, users, or settings
- Media Editor: manage media library only
- Read Only: view only; no modifications

Roles are not a parallel permission system. A role is a named
``Group`` that holds the real Django model permissions listed in
:attr:`Roles.PERMISSIONS`, so ``ModelAdmin`` enforces access on its own
and a role can never drift away from what Django checks. Anything not
carrying a role group falls back to plain ``user.has_perm`` resolution,
which keeps per-model grants (and existing superusers) working.
"""
from django.contrib.auth.models import Group, Permission

#: App labels a role codename may live in. Codenames such as
#: ``view_page`` are unique per app, not globally. ``auth`` is here because
#: managing staff accounts is itself a permission (``auth.change_user``).
ROLE_APP_LABELS = ("auth", "crm", "content")

#: Prefix for the Group that represents a role. Prefixing keeps roles
#: distinguishable from hand-made groups, so ``get_user_role`` cannot
#: mistake an unrelated group called "manager" for the manager role.
ROLE_GROUP_PREFIX = "role:"


class Roles:
    """Role definitions with their permission codenames."""

    SUPER_ADMIN = "super_admin"
    ADMINISTRATOR = "administrator"
    MANAGER = "manager"
    SALES = "sales"
    CONTENT_EDITOR = "content_editor"
    MEDIA_EDITOR = "media_editor"
    READ_ONLY = "read_only"

    ALL = [
        SUPER_ADMIN, ADMINISTRATOR, MANAGER, SALES,
        CONTENT_EDITOR, MEDIA_EDITOR, READ_ONLY,
    ]

    #: Human label for the group shown in the admin.
    LABELS = {
        SUPER_ADMIN: "Super Admin",
        ADMINISTRATOR: "Administrator",
        MANAGER: "Manager",
        SALES: "Sales",
        CONTENT_EDITOR: "Content Editor",
        MEDIA_EDITOR: "Media Editor",
        READ_ONLY: "Read Only",
    }

    #: Role -> list of permission codenames
    PERMISSIONS = {
        SUPER_ADMIN: [
            "view_lead", "add_lead", "change_lead", "delete_lead",
            "view_task", "add_task", "change_task", "delete_task",
            "view_contact", "add_contact", "change_contact", "delete_contact",
            "view_service", "add_service", "change_service", "delete_service",
            "view_page", "add_page", "change_page", "delete_page",
            "view_section", "add_section", "change_section", "delete_section",
            "view_mediaitem", "add_mediaitem", "change_mediaitem", "delete_mediaitem",
            "view_user", "add_user", "change_user", "delete_user",
            "view_sitesetting", "change_sitesetting",
            "view_navigation", "add_navigation", "change_navigation", "delete_navigation",
            "view_menuitem", "add_menuitem", "change_menuitem", "delete_menuitem",
        ],
        ADMINISTRATOR: [
            "view_lead", "add_lead", "change_lead", "delete_lead",
            "view_task", "add_task", "change_task", "delete_task",
            "view_contact", "add_contact", "change_contact", "delete_contact",
            "view_service", "add_service", "change_service", "delete_service",
            "view_page", "add_page", "change_page", "delete_page",
            "view_section", "add_section", "change_section", "delete_section",
            "view_mediaitem", "add_mediaitem", "change_mediaitem", "delete_mediaitem",
            "view_sitesetting", "change_sitesetting",
            "view_navigation", "add_navigation", "change_navigation", "delete_navigation",
            "view_menuitem", "add_menuitem", "change_menuitem", "delete_menuitem",
        ],
        MANAGER: [
            "view_lead", "add_lead", "change_lead",
            "view_task", "add_task", "change_task",
            "view_contact", "add_contact", "change_contact",
            "view_service", "change_service",
            "view_page", "change_page",
            "view_section", "change_section",
            "view_mediaitem", "add_mediaitem", "change_mediaitem",
        ],
        SALES: [
            "view_lead", "add_lead", "change_lead",
            "view_task", "add_task", "change_task",
            "view_contact", "change_contact",
            "view_service",
            "view_page",
            "view_section",
            "view_mediaitem",
        ],
        CONTENT_EDITOR: [
            "view_page", "add_page", "change_page",
            "view_section", "add_section", "change_section",
            "view_mediaitem", "add_mediaitem", "change_mediaitem",
            "view_navigation", "change_navigation",
            "view_menuitem", "change_menuitem",
        ],
        MEDIA_EDITOR: [
            "view_mediaitem", "add_mediaitem", "change_mediaitem", "delete_mediaitem",
        ],
        READ_ONLY: [
            "view_lead", "view_task", "view_contact", "view_service",
            "view_page", "view_section", "view_mediaitem",
        ],
    }


def role_group_name(role):
    """Return the Group name that represents ``role``."""
    return f"{ROLE_GROUP_PREFIX}{role}"


def get_role_permissions(role):
    """Return a list of permission codenames for a role."""
    return Roles.PERMISSIONS.get(role, [])


def get_role_label(role):
    return Roles.LABELS.get(role, role.replace("_", " ").title())


def get_user_role(user):
    """Return the role key held by ``user``, or ``None``.

    The first role group wins if a user somehow carries two; that is a
    configuration mistake rather than a case worth arbitrating here.
    """
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return Roles.SUPER_ADMIN
    for name in user.groups.values_list("name", flat=True):
        if name.startswith(ROLE_GROUP_PREFIX):
            return name[len(ROLE_GROUP_PREFIX):]
    return None


def resolve_permission(codename):
    """Return the Permission matching ``codename``, or ``None``."""
    return (
        Permission.objects.select_related("content_type")
        .filter(codename=codename, content_type__app_label__in=ROLE_APP_LABELS)
        .order_by("content_type__app_label")
        .first()
    )


def has_role_permission(user, permission_codename):
    """Check if a user has a permission through their role.

    Super admins always pass. A user carrying a role group is judged by
    that role's list. Anyone else is judged by their actual Django
    permissions, so direct grants keep working.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    role = get_user_role(user)
    if role is not None:
        return permission_codename in get_role_permissions(role)

    permission = resolve_permission(permission_codename)
    if permission is None:
        return False
    return user.has_perm(
        f"{permission.content_type.app_label}.{permission.codename}"
    )


def assign_role(user, role, replace=False):
    """Put ``user`` in the group for ``role`` and return that group.

    Passing ``replace=True`` drops any other role group first, so a user
    has exactly one role.
    """
    if role not in Roles.ALL:
        raise ValueError(f"Unknown role {role!r}. Expected one of {Roles.ALL}.")
    if not user or not user.is_authenticated or user.is_anonymous:
        raise ValueError("assign_role needs a concrete user instance.")

    groups, _missing = sync_role_groups()
    group = groups[role]
    user.groups.add(group)
    if replace:
        for name in user.groups.values_list("name", flat=True):
            if name.startswith(ROLE_GROUP_PREFIX) and name != group.name:
                user.groups.remove(Group.objects.get(name=name))
    return group


def sync_role_groups():
    """Create/refresh the role groups and return ``(groups, missing)``.

    ``missing`` lists codenames that no model provides, which would mean
    :attr:`Roles.PERMISSIONS` is out of date with the models.
    """
    groups, missing = {}, []
    for role in Roles.ALL:
        group, _ = Group.objects.get_or_create(name=role_group_name(role))
        wanted = []
        for codename in get_role_permissions(role):
            permission = resolve_permission(codename)
            if permission is None:
                missing.append(f"{role}:{codename}")
                continue
            wanted.append(permission)
        group.permissions.set(wanted)
        groups[role] = group
    return groups, missing


def can_edit_content(user):
    """Check if a user can edit website content."""
    return has_role_permission(user, "change_page") or has_role_permission(user, "change_section")


def can_edit_leads(user):
    """Check if a user can edit leads."""
    return has_role_permission(user, "change_lead")


def can_edit_users(user):
    """Check if a user can edit users and system settings."""
    return has_role_permission(user, "change_user") or has_role_permission(user, "change_sitesetting")


def can_edit_media(user):
    """Check if a user can edit media."""
    return has_role_permission(user, "change_mediaitem")


def can_view_leads(user):
    """Check if a user can view leads."""
    return has_role_permission(user, "view_lead")


def can_view_content(user):
    """Check if a user can view content."""
    return has_role_permission(user, "view_page")
