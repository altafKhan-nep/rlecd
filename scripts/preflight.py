"""Pre-deployment configuration check.

Run this before every deploy. It exists because the three most damaging
misconfigurations on this project all fail *quietly*: the site renders, the
health check passes, and only writing is broken.

    DATABASE_URL unset          -> per-instance sqlite; leads "save" then vanish
    CSRF_TRUSTED_ORIGINS unset  -> GETs fine, every form POST 403s
    EMAIL_BACKEND = console     -> no error, notifications silently dropped

Standalone rather than a management command because it configures Django
itself (to import settings and to test a live connection), which must happen
before manage.py would load the app registry.

    .venv/bin/python scripts/preflight.py

Exit code 0 = safe to deploy. Non-zero = do not deploy.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The Django apps live in backend/ and must import as top-level `main`, `crm`
# and `content` -- the app labels recorded in migrations.
BACKEND_DIR = REPO_ROOT / "backend"
for _path in (BACKEND_DIR,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

FAILURES = []
WARNINGS = []
PASSES = []


def fail(msg):
    FAILURES.append(msg)


def warn(msg):
    WARNINGS.append(msg)


def ok(msg):
    PASSES.append(msg)


def check_secret_key():
    from django.conf import settings

    key = settings.SECRET_KEY
    if not key:
        fail("SECRET_KEY is empty.")
    elif key == "django-insecure-please-change-me":
        fail("SECRET_KEY is still the Django placeholder. Generate a real one.")
    elif len(key) < 50:
        warn(f"SECRET_KEY is only {len(key)} chars; 50+ recommended.")
    else:
        ok("SECRET_KEY present.")


def check_debug():
    from django.conf import settings

    if settings.DEBUG:
        fail(
            "DEBUG is True. In a deployed build this renders settings and "
            "environment values in error pages. Set DEBUG=False."
        )
    else:
        ok("DEBUG is False.")


def check_database():
    """Verify the database is reachable, not merely configured.

    A syntactically valid DATABASE_URL that points at a paused, wrong-credential
    or unreachable instance is the common case, and it only shows up as a 500
    on the first real request otherwise.
    """
    import django.db
    from django.conf import settings

    engine = settings.DATABASES["default"]["ENGINE"]

    if engine.endswith("sqlite3"):
        deploy = bool(os.environ.get("RENDER")) or not settings.DEBUG
        if deploy:
            fail(
                "Using sqlite. A container filesystem is ephemeral, so leads "
                "and content edits will disappear when the instance recycles. "
                "Set DATABASE_URL to a managed Postgres connection string."
            )
        else:
            warn("Using local sqlite (expected outside deployment).")
        return

    ok(f"Postgres configured: {settings.DATABASES['default']['HOST']}")

    # The driver is a compiled extension, so it can be installed and still fail
    # to load when the wheel does not match the interpreter's ABI. That is not
    # hypothetical: an earlier build died with "ImportError: no pq wrapper
    # available" because psycopg-binary's `pq` extension would not import.
    # Django loads the backend while populating the app registry, so every
    # request 500s -- not just the first query. Check it explicitly.
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict  # noqa: F401
    except Exception as exc:
        fail(
            f"The Postgres driver cannot be imported: {exc}\n"
            "    psycopg-binary ships a prebuilt libpq but it must match the\n"
            "    interpreter's ABI. If this appears only on the host, pin the\n"
            "    Python version in that host's project settings to match\n"
            "    .python-version, or build libpq via the system package."
        )
        return

    # Parameters are forwarded verbatim so nothing is silently dropped, which
    # means an unrecognised keyword is only rejected at connect time. Catch it
    # here, where the variable can still be named.
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        try:
            from psycopg.conninfo import conninfo_to_dict
            from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

            parsed = urlparse(dsn)
            query = parse_qs(parsed.query)
            query.pop("conn_max_age", None)  # Django-level, not a libpq option
            conninfo_to_dict(
                urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
            )
        except Exception as exc:
            fail(
                f"libpq rejected DATABASE_URL: {exc}\n"
                "    An unrecognised parameter is forwarded to libpq rather than\n"
                "    dropped, so a typo fails here instead of quietly disabling a\n"
                "    security setting such as channel_binding."
            )

    # Pooled vs direct matters on any host that opens more than a few
    # concurrent connections, which Render does.
    dsn = os.environ.get("DATABASE_URL", "")
    if "-pooler" not in dsn and "pgbouncer" not in dsn:
        warn(
            "DATABASE_URL does not look like a pooled connection. Neon pooled "
            "strings contain '-pooler' in the hostname. A direct connection "
            "will exhaust max_connections under a cold-start burst."
        )

    try:
        connection = django.db.connection
        connection.ensure_connection()
        ok("Database connection succeeded.")
    except Exception as exc:
        fail(
            f"Cannot connect to the database: {type(exc).__name__}: {exc}. "
            "Check the host, credentials and sslmode, and that the Neon "
            "project is not suspended."
        )
        return

    # Confirm migrations have actually been applied.
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        if plan:
            names = ", ".join(migration.app_label for migration, _ in plan[:4])
            fail(
                f"{len(plan)} unapplied migration(s) ({names}...). Run "
                "`python manage.py migrate` against this database before "
                "going live, or the site will 500 on every database read."
            )
        else:
            ok("All migrations applied.")
    except Exception as exc:
        fail(f"Could not verify migrations: {type(exc).__name__}: {exc}")

    # Content must exist; an empty content table means an unseeded deploy.
    try:
        from content.models import Page
        from crm.models import Service

        pages = Page.objects.filter(published=True).count()
        services = Service.objects.count()
        if pages == 0:
            fail(
                "No published Pages. Run `python manage.py capture_content` "
                "against this database; production does not inherit local data."
            )
        else:
            ok(f"{pages} published pages.")
        if services == 0:
            fail("No services. Run `python manage.py seed_services`.")
        else:
            ok(f"{services} services.")
    except Exception as exc:
        warn(f"Could not verify seeded content: {type(exc).__name__}: {exc}")


def check_hosts():
    from django.conf import settings

    if not settings.ALLOWED_HOSTS:
        fail("ALLOWED_HOSTS is empty; every request will 400.")
    elif any(h == "*" for h in settings.ALLOWED_HOSTS):
        fail("ALLOWED_HOSTS contains '*', which permits Host header spoofing.")
    else:
        ok(f"ALLOWED_HOSTS: {', '.join(settings.ALLOWED_HOSTS)}")


def check_csrf():
    from django.conf import settings

    if not settings.CSRF_TRUSTED_ORIGINS:
        fail(
            "CSRF_TRUSTED_ORIGINS is empty. Pages will render but every form "
            "POST (lead form and all admin forms) will fail 403."
        )
        return

    # A malformed origin never matches the browser's Origin header, so every
    # POST 403s with no other symptom. Caught here because the doubled-scheme
    # form ("https://https://host") is easy to produce and invisible otherwise.
    for origin in settings.CSRF_TRUSTED_ORIGINS:
        if "://" in origin.split("://", 1)[1]:
            fail(
                f"CSRF_TRUSTED_ORIGINS entry {origin!r} contains a doubled "
                "scheme and will never match. Use https://host only."
            )
        elif not origin.startswith(("http://", "https://")):
            fail(f"CSRF_TRUSTED_ORIGINS entry {origin!r} is missing a scheme.")
    ok(f"CSRF_TRUSTED_ORIGINS: {', '.join(settings.CSRF_TRUSTED_ORIGINS)}")

    # Render exposes the service's external hostname as RENDER_EXTERNAL_URL.
    # CSRF_TRUSTED_ORIGINS is only needed for a cross-origin deployment; the
    # generated *.onrender.com hostname is same-origin, so this is informational.
    render_host = os.environ.get("RENDER_EXTERNAL_URL", "").removeprefix("https://")
    if render_host and not any(
        render_host in o for o in settings.CSRF_TRUSTED_ORIGINS
    ):
        ok(
            f"Serving on {render_host}, which is same-origin, so it does not "
            "need an entry in CSRF_TRUSTED_ORIGINS. Add one only if you attach "
            "a custom domain and post to this host from that domain."
        )


def check_email():
    import socket
    from django.conf import settings

    backend = settings.EMAIL_BACKEND
    if "console" in backend:
        fail(
            "EMAIL_BACKEND is the console backend. Lead notifications are "
            "written to logs and delivered to nobody. Set a real SMTP backend."
        )
        return
    ok(f"Email backend: {backend}")

    if settings.EMAIL_USE_SSL and settings.EMAIL_USE_TLS:
        fail("EMAIL_USE_SSL and EMAIL_USE_TLS are both True; they are mutually exclusive.")

    if not settings.ADMIN_EMAIL or not all(settings.ADMIN_EMAIL):
        fail("ADMIN_EMAIL is empty; lead notifications have no recipient.")

    # Unbounded SMTP would hold a request thread open until it is killed.
    if not getattr(settings, "EMAIL_TIMEOUT", None):
        warn("EMAIL_TIMEOUT is unset; a hung SMTP connection could block until timeout.")

    # DNS + TCP reachability, so a typo'd host is found here not in production.
    if settings.EMAIL_HOST and not settings.EMAIL_USE_SSL:
        try:
            socket.setdefaulttimeout(5)
            socket.create_connection((settings.EMAIL_HOST, settings.EMAIL_PORT), timeout=5).close()
            ok(f"SMTP {settings.EMAIL_HOST}:{settings.EMAIL_PORT} reachable.")
        except Exception as exc:
            warn(f"Cannot reach SMTP {settings.EMAIL_HOST}:{settings.EMAIL_PORT} ({exc}).")


def check_static():
    from django.conf import settings

    static_root = Path(settings.STATIC_ROOT)
    if not static_root.exists():
        fail(
            f"STATIC_ROOT does not exist ({static_root}). Run "
            "`python manage.py collectstatic --noinput`; otherwise every "
            "CSS and image request 404s."
        )
        return

    total = sum(f.stat().st_size for f in static_root.rglob("*") if f.is_file())
    mb = total / 1048576
    ok(f"Static files collected: {mb:.1f} MB.")

    if mb > 100:
        fail(f"Static bundle is {mb:.1f} MB, which will likely exceed the function size limit.")
    elif mb > 40:
        warn(f"Static bundle is {mb:.1f} MB. Large for a single container; consider optimising images.")

    # The single worst offender, named explicitly because it is not obvious.
    big = [
        f for f in static_root.rglob("*")
        if f.is_file() and f.stat().st_size > 1_000_000 and not f.name.endswith((".gz", ".br"))
    ]
    for f in sorted(big, key=lambda x: -x.stat().st_size)[:5]:
        warn(f"  large asset: {f.stat().st_size / 1048576:.1f} MB  {f.name}")


def check_admin():
    import django.db

    try:
        from django.contrib.auth.models import User

        count = User.objects.filter(is_staff=True).count()
        if count == 0:
            fail("No staff users. Run `python manage.py createsuperuser` against this database.")
        else:
            ok(f"{count} staff user(s).")
    except Exception as exc:
        warn(f"Could not verify staff users: {type(exc).__name__}: {exc}")


def _probe_postgres_driver():
    """Return an error string if the Postgres driver cannot be imported.

    Django loads the database backend while populating the app registry, so an
    unimportable driver makes `django.setup()` itself raise and every later
    check becomes unreachable. That is how an earlier build died: psycopg-binary
    was installed, but its compiled `pq` extension did not match the
    interpreter's ABI ("ImportError: no pq wrapper available").

    Probed before django.setup() so the failure is reported as a diagnosis
    rather than a traceback that ends inside importlib.
    """
    if "DATABASE_URL" not in os.environ:
        return None
    # Read the raw value so this works before Django/decouple are involved.
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn.split("://", 1)[0].split(":", 1)[0] not in ("postgres", "postgresql"):
        return None
    try:
        import psycopg  # noqa: F401
        from psycopg.conninfo import conninfo_to_dict  # noqa: F401
    except Exception as exc:
        return str(exc)
    return None


def main():
    import django

    driver_error = _probe_postgres_driver()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "home_improvement.settings")
    if driver_error is None:
        # Production-shaped: DEBUG comes from the environment/.env, not forced.
        django.setup()

    print("=" * 68)
    print("  DEPLOY PREFLIGHT")
    print("=" * 68)

    if driver_error is not None:
        # Skip straight to the report: nothing else can run without the app
        # registry, and Django's own message does not say why.
        fail(f"The Postgres driver cannot be imported: {driver_error}")
        print()
        print("  psycopg-binary ships a prebuilt libpq, but it must match the")
        print("  interpreter's ABI. On a build host this usually means the")
        print("  interpreter that installed requirements is not the one running")
        print("  the build: pin the Python version in the host's project")
        print("  settings to match .python-version, or install libpq via the")
        print("  system package (apt-get install libpq-dev).")
        print()
        print("=" * 68)
        print(f"  {len(FAILURES)} blocking issue(s). Do not deploy.")
        return 1

    for check in (
        check_secret_key,
        check_debug,
        check_database,
        check_hosts,
        check_csrf,
        check_email,
        check_static,
        check_admin,
    ):
        try:
            check()
        except Exception as exc:  # a broken check must not mask the others
            fail(f"{check.__name__} raised {type(exc).__name__}: {exc}")

    for msg in PASSES:
        print(f"  PASS  {msg}")
    for msg in WARNINGS:
        print(f"  WARN  {msg}")
    for msg in FAILURES:
        print(f"  FAIL  {msg}")

    print("-" * 68)
    if FAILURES:
        print(f"  {len(FAILURES)} blocking issue(s). Do not deploy.")
        return 1
    print(f"  Ready to deploy ({len(WARNINGS)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
