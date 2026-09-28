from django.contrib.admin.apps import AdminConfig


class StudioAdminConfig(AdminConfig):
    """Installs the dashboard admin in place of Django's default one.

    `default_site` is only honoured on the config that replaces
    `django.contrib.admin` in INSTALLED_APPS -- `admin.site` resolves it via
    `apps.get_app_config("admin").default_site`, so setting it on a regular app
    config like `main` has no effect. Hence a subclass of AdminConfig, listed
    in place of the admin app.

    Every `@admin.register`, `admin.site` reference and `{% url 'admin:...' %}`
    tag keeps working; only the AdminSite subclass changes.
    """

    default_site = "main.admin_site.StudioAdminSite"
