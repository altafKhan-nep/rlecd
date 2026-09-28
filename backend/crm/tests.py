"""Tests for the CRM.

The two behaviours that matter most and are easiest to break are covered
explicitly:

* a lead survives an email outage (the defect that used to lose enquiries)
* the public pages still render the mirrored markup (adding a CRM must not
  change what visitors see)
"""
from unittest import mock

import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from crm import services
from crm.admin import LeadAdmin
from crm.models import (
    Contact,
    Lead,
    LeadActivity,
    LeadSource,
    LeadStatus,
    Priority,
    Service,
    Task,
)

PUBLIC_PAGES = [
    "/", "/about/", "/contact/", "/areas-we-serve/", "/kitchen-remodeling/",
    "/bathroom-remodeling/", "/basement-finishing/", "/home-additions/",
    "/painting/", "/home-improvement/", "/patios-decks/", "/cabinets/",
    "/woodworking/", "/hardscaping/", "/walkway-designs/", "/pergolas/",
    "/lead-removal/", "/shed-builder/", "/lead-renovator/",
]


def make_service(name="Kitchen Remodeling", slug="kitchen-remodeling"):
    return Service.objects.create(name=name, slug=slug)


class CaptureTests(TestCase):
    def test_capture_creates_lead_and_created_activity(self):
        make_service()
        lead = services.capture_lead(
            name="Dana Scully", email="dana@example.com", message="Full gut "
            "renovation of a 1920s kitchen in Ruxton.",
            phone="4105551234", city_or_zip="Ruxton, MD 21201",
            service_raw="Kitchen Remodeling", source=LeadSource.CONTACT_FORM,
        )

        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(lead.service.slug, "kitchen-remodeling")
        self.assertEqual(lead.source, LeadSource.CONTACT_FORM)
        self.assertTrue(lead.is_open)

        activity = LeadActivity.objects.get(lead=lead)
        self.assertEqual(activity.kind, LeadActivity.Kind.CREATED)
        self.assertIn(str(lead.score), activity.detail)

    def test_capture_rejects_missing_required_fields(self):
        for kwargs in (
            {"name": "", "email": "a@b.com", "message": "hello there"},
            {"name": "A", "email": "", "message": "hello there"},
            {"name": "A", "email": "a@b.com", "message": "   "},
        ):
            with self.subTest(**{k: v for k, v in kwargs.items() if not v}):
                with self.assertRaises(services.CaptureError):
                    services.capture_lead(**kwargs)
        self.assertEqual(Lead.objects.count(), 0)

    def test_unresolvable_service_is_kept_verbatim(self):
        lead = services.capture_lead(
            name="A", email="a@b.com", message="x", service_raw="Underwater Basket Weaving",
        )
        self.assertIsNone(lead.service)
        self.assertEqual(lead.service_raw, "Underwater Basket Weaving")

    def test_both_legacy_service_contracts_resolve(self):
        """Home page posted display strings, contact page posted slugs."""
        make_service("Kitchen Remodeling", "kitchen-remodeling")
        make_service("Bathroom Remodeling", "bathroom-remodeling")

        for raw in ("Kitchen Remodeling", "kitchen-remodeling", "kitchen"):
            with self.subTest(raw=raw):
                service, _ = services.resolve_service(raw)
                self.assertIsNotNone(service)
                self.assertEqual(service.name, "Kitchen Remodeling")

        service, _ = services.resolve_service("bath")
        self.assertIsNotNone(service)
        self.assertEqual(service.name, "Bathroom Remodeling")


class ScoringTests(TestCase):
    def test_detailed_enquiry_with_phone_outscores_a_thin_one(self):
        thin = services.score_lead(message="hi", phone="", city_or_zip="")
        rich = services.score_lead(
            message="Looking for a quote for a full kitchen remodel, we are "
                    "ready to start next month and have a firm budget.",
            phone="4105551234", city_or_zip="Towson, MD 21204",
        )
        self.assertLess(thin, rich)
        self.assertGreaterEqual(rich, services.HOT_THRESHOLD)
        self.assertTrue(Lead(score=rich).is_hot)

    def test_score_is_clamped_to_0_100(self):
        huge = services.score_lead(
            message="URGENT! " * 500, phone="1", city_or_zip="x",
        )
        self.assertLessEqual(huge, 100)
        self.assertGreaterEqual(services.score_lead(message=""), 0)

    def test_priority_thresholds(self):
        self.assertEqual(services.priority_for(90), Priority.URGENT)
        self.assertEqual(services.priority_for(75), Priority.HIGH)
        self.assertEqual(services.priority_for(20), Priority.NORMAL)


class NotifyTests(TestCase):
    """The regression that motivated this app: mail failure must not lose data."""

    def setUp(self):
        self.lead = services.capture_lead(
            name="Fox Mulder", email="fox@example.com", message="Need a quote.",
        )

    def test_notify_sends_admin_and_customer_mail(self):
        services.notify(self.lead)
        self.assertEqual(len(mail.outbox), 2)
        kinds = LeadActivity.objects.filter(
            lead=self.lead, kind=LeadActivity.Kind.EMAILED
        ).count()
        self.assertEqual(kinds, 2)

    def test_lead_survives_an_email_outage(self):
        with mock.patch("crm.services.send_mail", side_effect=OSError("smtp down")):
            services.notify(self.lead)  # must not raise

        self.assertTrue(Lead.objects.filter(pk=self.lead.pk).exists())
        self.assertEqual(mail.outbox, [])
        self.assertEqual(LeadActivity.objects.filter(lead=self.lead).count(), 1)


class PipelineTests(TestCase):
    def setUp(self):
        self.lead = services.capture_lead(
            name="A", email="a@b.com", message="hello", phone="4105551234",
        )

    def test_status_change_is_logged_and_stamps_contacted_at(self):
        before = LeadActivity.objects.count()
        services.set_status(self.lead, LeadStatus.CONTACTED, note="Called them")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.CONTACTED)
        self.assertIsNotNone(self.lead.contacted_at)
        self.assertEqual(LeadActivity.objects.count(), before + 1)

        entry = LeadActivity.objects.first()
        self.assertEqual(entry.kind, LeadActivity.Kind.STATUS_CHANGED)
        self.assertIn("was New", entry.summary)

    def test_setting_the_same_status_is_a_noop(self):
        before = LeadActivity.objects.count()
        services.set_status(self.lead, LeadStatus.NEW)
        self.assertEqual(LeadActivity.objects.count(), before)

    def test_duplicate_detection_by_email_and_phone(self):
        self.assertEqual(self.lead.match_duplicate().count(), 0)
        services.capture_lead(name="A again", email="A@B.com", message="second enquiry")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.match_duplicate().count(), 1)

    def test_conversion_creates_one_contact_and_is_idempotent(self):
        contact = services.convert_to_contact(self.lead)
        self.assertIsInstance(contact, Contact)
        self.assertEqual(services.convert_to_contact(self.lead).pk, contact.pk)
        self.assertEqual(Contact.objects.count(), 1)

    def test_won_and_lost_are_not_open(self):
        self.assertTrue(self.lead.is_open)
        services.set_status(self.lead, LeadStatus.WON)
        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_open)


class TaskTests(TestCase):
    def test_overdue_detection(self):
        lead = services.capture_lead(name="A", email="a@b.com", message="hi")
        task = Task.objects.create(
            lead=lead, title="Call back",
            due_at=timezone.now() - timezone.timedelta(days=1),
        )
        self.assertTrue(task.is_overdue)
        task.done = True
        task.save(update_fields=["done"])
        self.assertFalse(Task.objects.get(pk=task.pk).is_overdue)


class PublicFormTests(TestCase):
    """End-to-end through the real URLs, CSRF included."""

    def test_contact_form_post_persists_a_lead(self):
        make_service("Kitchen Remodeling", "kitchen-remodeling")
        response = self.client.post("/contact/", {
            "name": "Walter Bishop", "email": "bishop@example.com",
            "phone": "4105559999", "service": "kitchen-remodeling",
            "message": "Kitchen remodel, ready to start soon.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/contact/")

        lead = Lead.objects.get()
        self.assertEqual(lead.name, "Walter Bishop")
        self.assertEqual(lead.source, LeadSource.CONTACT_FORM)
        self.assertEqual(lead.service.slug, "kitchen-remodeling")
        self.assertEqual(len(mail.outbox), 2)

    def test_home_form_post_persists_a_lead(self):
        response = self.client.post("/#contact", {
            "name": "Cyd", "email": "cyd@example.com",
            "service": "Painting", "message": "Painting the whole house.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lead.objects.get().source, LeadSource.HOME_FORM)

    def test_invalid_submission_is_rejected_without_creating_a_lead(self):
        response = self.client.post("/contact/", {
            "name": "", "email": "x@example.com", "message": "hi",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lead.objects.count(), 0)

    def test_form_post_still_works_when_email_is_broken(self):
        with mock.patch("crm.services.send_mail", side_effect=OSError("smtp down")):
            response = self.client.post("/contact/", {
                "name": "Gillian", "email": "g@example.com", "message": "Basement finish.",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lead.objects.count(), 1)


class AdminPipelineTests(TestCase):
    """The admin is the CRM UI, so its mutations need to work and be logged."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "staffer", "staffer@example.com", "pw",
        )
        self.client.force_login(self.admin)
        self.service = make_service()
        self.lead = services.capture_lead(
            name="Admin Check", email="a@b.com", message="Quote please.",
            service_raw="Kitchen Remodeling",
        )

    def _change_payload(self, **overrides):
        """A full admin change-form POST, including every inline's management
        form. Omitting them makes that inline formset invalid, which silently
        blocks the whole save — real browsers always submit them."""
        data = {
            "name": self.lead.name, "email": self.lead.email,
            "phone": self.lead.phone, "city_or_zip": self.lead.city_or_zip,
            "service": self.service.pk, "message": self.lead.message,
            "status": self.lead.status, "priority": self.lead.priority,
            "source": self.lead.source, "notes": "", "owner": "",
            "_save": "Save",
        }
        for prefix, total in (("tasks", "1"), ("activities", "0")):
            data.update({
                f"{prefix}-TOTAL_FORMS": total,
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            })
        data.update({
            "tasks-0-id": "", "tasks-0-title": "", "tasks-0-due_at": "",
            "tasks-0-done": "", "tasks-0-assigned_to": "",
        })
        data.update(overrides)
        return data

    def test_changelist_and_detail_render(self):
        self.assertEqual(self.client.get("/admin/crm/lead/").status_code, 200)
        response = self.client.get(f"/admin/crm/lead/{self.lead.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enquiry received")  # activity timeline

    def test_editing_status_on_the_form_persists_and_is_logged(self):
        url = f"/admin/crm/lead/{self.lead.pk}/change/"
        response = self.client.post(url, self._change_payload(status="qualified"))

        self.assertEqual(response.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.QUALIFIED)
        self.assertIn(
            "Status changed",
            [a.get_kind_display() for a in self.lead.activities.all()],
        )

    def test_bulk_actions(self):
        url = "/admin/crm/lead/"
        selected = [str(self.lead.pk)]

        self.client.post(url, {"action": "action_assign_to_me",
                               "_selected_action": selected})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.owner, self.admin)

        self.client.post(url, {"action": "action_mark_qualified",
                               "_selected_action": selected})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.QUALIFIED)

        self.client.post(url, {"action": "action_create_followup",
                               "_selected_action": selected})
        self.assertEqual(self.lead.tasks.count(), 1)

        self.client.post(url, {"action": "action_convert_to_contact",
                               "_selected_action": selected})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, LeadStatus.WON)
        self.assertEqual(Contact.objects.count(), 1)

    def test_score_is_system_managed_but_priority_is_overridable(self):
        readonly = LeadAdmin.readonly_fields
        self.assertIn("score", readonly)
        self.assertNotIn("priority", readonly)


class MirrorRegressionTests(TestCase):
    """Adding the CRM must not alter the mirrored public pages."""

    def test_every_public_page_still_renders(self):
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_pages_still_expose_live_markup(self):
        html = self.client.get("/").content.decode()
        self.assertIn('href="/static/css/style.css"', html)
        self.assertIn('class="navbar"', html)
        self.assertIn("REAL LIFE EXPERIENCE LLC", html)
        # homepage-only social sidebar must remain homepage-only
        self.assertIn('id="socialSidebar"', html)
        self.assertNotIn('id="socialSidebar"', self.client.get("/about/").content.decode())

    def test_forms_still_carry_csrf(self):
        for path in ("/", "/contact/"):
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), "csrfmiddlewaretoken")


class AdminLoginPageTests(TestCase):
    """The branded admin login is a full template override.

    Restyling a login page is only safe while the form contract with Django is
    intact, so these assert the fields, the `next` input and CSRF are all still
    there, and that authentication still behaves.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            "staffer", "s@example.com", "Str0ngPass!")

    def test_login_page_renders_with_branding(self):
        html = self.client.get("/admin/login/").content.decode()
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)
        self.assertIn("Real Life", html)
        self.assertIn("/static/img/rlecd_maryland_logo.png", html)
        self.assertIn("Montserrat", html)
        self.assertIn("Playfair", html)

    def test_form_contract_is_preserved(self):
        html = self.client.get("/admin/login/").content.decode()
        self.assertIn('id="login-form"', html)
        self.assertIn('method="post"', html)
        self.assertIn("csrfmiddlewaretoken", html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)
        # Dropping `next` bounces staff back to the login screen after sign-in.
        self.assertIn('name="next"', html)

    def test_labels_are_bound_to_their_inputs(self):
        html = self.client.get("/admin/login/").content.decode()
        self.assertIn('for="id_username"', html)
        self.assertIn('for="id_password"', html)

    def test_admin_chrome_is_suppressed(self):
        html = self.client.get("/admin/login/").content.decode()
        self.assertNotIn('id="header"', html)
        self.assertNotIn('id="nav-breadcrumbs"', html)

    def test_no_dead_controls_are_rendered(self):
        """This project registers no admin password-reset URLs.

        The template must therefore not emit a link to them, and must not offer
        a "remember me" checkbox that Django's AdminAuthenticationForm has no
        field to receive.
        """
        html = self.client.get("/admin/login/").content.decode()
        self.assertNotIn("password_reset", html)
        self.assertNotIn("thisis__remember", html)
        self.assertNotIn("Keep me signed in", html)

    def test_wrong_password_shows_an_announced_error_and_stays_signed_out(self):
        response = self.client.post(
            "/admin/login/", {"username": "staffer", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('role="alert"', html)
        self.assertIn("correct username and password", html)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_valid_login_redirects_to_next(self):
        response = self.client.post(
            "/admin/login/?next=/admin/content/page/",
            {"username": "staffer", "password": "Str0ngPass!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/content/page/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_still_enforces_csrf(self):
        enforcing = Client(enforce_csrf_checks=True)
        enforcing.get("/admin/login/")
        response = enforcing.post(
            "/admin/login/", {"username": "staffer", "password": "Str0ngPass!"})
        self.assertEqual(response.status_code, 403)

    def test_inputs_are_border_box(self):
        """Regression guard for horizontal overflow on narrow viewports.

        admin base.css does not set border-box on form controls, so a
        `width: 100%` input plus padding rendered 6px wider than the viewport
        and produced a horizontal scrollbar on phones. The browser probe caught
        it; this keeps the rule from being dropped.
        """
        html = self.client.get("/admin/login/").content.decode()
        self.assertIn("box-sizing: border-box", html)

    def test_submit_button_outranks_admin_input_type_submit(self):
        """admin styles `input[type=submit]` at specificity (0,1,1).

        A bare `.rl-submit` rule loses to it and the button renders Django's
        accent blue instead of the brand green, so the type attribute is part of
        the selector on purpose.
        """
        html = self.client.get("/admin/login/").content.decode()
        self.assertIn("input[type=submit].rl-submit", html)


class DeploymentSettingsTests(TestCase):
    """Guards the serverless deployment configuration.

    These settings only matter once deployed, so a regression here would not
    surface in local testing. Each one caused a real, hard-to-diagnose failure.
    """

    def test_csrf_origins_have_no_doubled_scheme(self):
        # SITE_URL already includes a scheme, so prefixing one yields
        # "https://https://host", which matches no Origin header and makes
        # every form POST 403 while pages render normally.
        for origin in settings.CSRF_TRUSTED_ORIGINS:
            self.assertTrue(
                origin.startswith(("http://", "https://")),
                f"CSRF origin {origin!r} is missing a scheme",
            )
            self.assertNotIn(
                "://", origin.split("://", 1)[1],
                f"CSRF origin {origin!r} contains a doubled scheme",
            )

    def test_origin_normalisation(self):
        from home_improvement.settings import _origin

        self.assertEqual(_origin("https://rlecd.com"), "https://rlecd.com")
        self.assertEqual(_origin("rlecd.com"), "https://rlecd.com")
        self.assertEqual(_origin("https://rlecd.com/"), "https://rlecd.com")
        self.assertEqual(_origin("http://localhost:8000"), "http://localhost:8000")
        self.assertEqual(_origin(""), "")

    def test_preflight_flags_console_email_backend(self):
        # The console backend is correct locally and silently drops every
        # notification in production, so the guard against shipping it is
        # what matters, not the currently configured value (the test runner
        # replaces EMAIL_BACKEND with locmem anyway).
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "preflight", settings.REPO_ROOT / "scripts" / "preflight.py"
        )
        preflight = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preflight)

        with self.settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"):
            preflight.FAILURES.clear()
            preflight.WARNINGS.clear()
            preflight.check_email()
            self.assertTrue(
                any("console backend" in f for f in preflight.FAILURES),
                f"console backend not flagged; failures={preflight.FAILURES}",
            )

        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_USE_SSL=True,
            EMAIL_USE_TLS=True,
        ):
            preflight.FAILURES.clear()
            preflight.check_email()
            self.assertTrue(
                any("mutually exclusive" in f for f in preflight.FAILURES),
                f"SSL+TLS not flagged; failures={preflight.FAILURES}",
            )

    def test_email_timeout_is_bounded(self):
        # An unbounded SMTP connect would hold a serverless invocation open
        # until the platform kills it.
        self.assertIsNotNone(getattr(settings, "EMAIL_TIMEOUT", None))
        self.assertLessEqual(settings.EMAIL_TIMEOUT, 30)

    def test_hsts_preload_not_enabled_by_default(self):
        # Preload/includeSubDomains are per-registrable-domain commitments and
        # must not be inherited on a shared platform hostname.
        self.assertFalse(settings.SECURE_HSTS_PRELOAD)
        self.assertFalse(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_whitenoise_middleware_is_installed(self):
        self.assertIn("whitenoise.middleware.WhiteNoiseMiddleware", settings.MIDDLEWARE)


class DatabaseUrlParsingTests(SimpleTestCase):
    """DATABASE_URL handling.

    The first version of this parsed the query string against a hand-written
    allowlist, which silently dropped anything it did not recognise --
    including `channel_binding=require`, a security setting that must never
    disappear quietly. These tests pin the behaviour that replaced it.
    """

    def parse(self, url):
        from home_improvement.settings import _postgres_from_url

        return _postgres_from_url(url)

    def test_preserves_channel_binding(self):
        # Regression: this was dropped by the allowlist, silently disabling
        # SCRAM channel binding.
        db = self.parse(
            "postgresql://u:pw@host/db?sslmode=require&channel_binding=require"
        )
        self.assertEqual(db["OPTIONS"]["channel_binding"], "require")
        self.assertEqual(db["OPTIONS"]["sslmode"], "require")

    def test_forwards_arbitrary_libpq_parameters(self):
        db = self.parse("postgresql://u:pw@host/db?application_name=rlecd&target_session_attrs=read-write")
        self.assertEqual(db["OPTIONS"]["application_name"], "rlecd")
        self.assertEqual(db["OPTIONS"]["target_session_attrs"], "read-write")

    def test_typo_in_parameter_raises_instead_of_being_dropped(self):
        from psycopg import ProgrammingError

        with self.assertRaises(ProgrammingError):
            self.parse("postgresql://u:pw@host/db?totally_bogus=1")

    def test_unknown_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("mysql://u:pw@host/db")

    def test_conn_max_age_is_django_level_not_libpq(self):
        # libpq rejects conn_max_age as an unknown connection option, so it
        # must be stripped before the URI is validated and applied by Django.
        db = self.parse("postgresql://u:pw@host/db?conn_max_age=120")
        self.assertEqual(db["CONN_MAX_AGE"], 120)
        self.assertNotIn("conn_max_age", db["OPTIONS"])

    def test_default_connection_pooling_and_timeout(self):
        db = self.parse("postgresql://u:pw@host/db")
        self.assertEqual(db["CONN_MAX_AGE"], 60)
        self.assertEqual(db["OPTIONS"]["connect_timeout"], 10)

    def test_percent_encoded_password_is_decoded(self):
        db = self.parse("postgresql://u:p%40ss%3Aword@host:5432/db")
        self.assertEqual(db["PASSWORD"], "p@ss:word")
        self.assertEqual(db["PORT"], "5432")
        self.assertEqual(db["NAME"], "db")
        self.assertEqual(db["HOST"], "host")

    def test_neon_pooled_url_shape(self):
        # The shape Neon actually issues: pooled hostname, no explicit port.
        db = self.parse(
            "postgresql://neondb_owner:secret@ep-abc-123-pooler.us-east-2.aws.neon.tech"
            "/neondb?sslmode=require&channel_binding=require"
        )
        self.assertEqual(db["NAME"], "neondb")
        self.assertEqual(db["USER"], "neondb_owner")
        self.assertIn("-pooler", db["HOST"])
        self.assertEqual(db["PORT"], "")

    def test_database_identifier_keys_are_not_duplicated_into_options(self):
        db = self.parse("postgresql://u:pw@host:5432/db?sslmode=require")
        for key in ("dbname", "user", "password", "host", "port"):
            self.assertNotIn(key, db["OPTIONS"])


class TestDiscoveryLayoutTests(SimpleTestCase):
    """Guards the frontend/backend split against silent test loss.

    Django's default discovery searches the current working directory. After the
    move, `manage.py test` run from the repository root found *zero* tests and
    still exited 0, which would let a broken suite pass CI unnoticed. These
    tests fail loudly if that ever regresses.
    """

    def test_apps_live_in_backend_not_at_repo_root(self):
        # The reason the default runner is not usable: nothing importable as a
        # test package sits at the root any more.
        self.assertTrue((settings.BASE_DIR / "main" / "tests.py").is_file())
        self.assertTrue((settings.BASE_DIR / "crm" / "tests.py").is_file())
        self.assertTrue((settings.BASE_DIR / "content" / "tests.py").is_file())
        self.assertFalse((settings.REPO_ROOT / "main").exists())
        self.assertFalse((settings.REPO_ROOT / "crm").exists())
        self.assertFalse((settings.REPO_ROOT / "content").exists())

    def test_custom_test_runner_is_configured(self):
        self.assertEqual(
            settings.TEST_RUNNER, "home_improvement.runner.BackendDiscoverRunner"
        )

    def test_runner_pins_discovery_to_backend(self):
        from home_improvement.runner import BackendDiscoverRunner

        runner = BackendDiscoverRunner(verbosity=0, interactive=False)
        # With no explicit targets, both the start dir and the import top level
        # must be backend/ -- pinning only top_level still discovers nothing,
        # because Django defaults start_dir to '.'.
        with mock.patch.object(
            type(runner).__bases__[0], "build_suite", return_value=mock.Mock()
        ) as parent_build:
            runner.build_suite(None)
        args, kwargs = parent_build.call_args
        self.assertEqual(args[0], [str(settings.BASE_DIR)])
        self.assertEqual(kwargs["top_level"], str(settings.BASE_DIR))

    def test_explicit_test_labels_are_left_alone(self):
        from home_improvement.runner import BackendDiscoverRunner

        runner = BackendDiscoverRunner(verbosity=0, interactive=False)
        with mock.patch.object(
            type(runner).__bases__[0], "build_suite", return_value=mock.Mock()
        ) as parent_build:
            runner.build_suite(["crm.tests"])
        args, kwargs = parent_build.call_args
        self.assertEqual(args[0], ["crm.tests"])
        self.assertNotIn("top_level", kwargs)


class EnvVarToleranceTests(SimpleTestCase):
    """A blank env var must not fail the build.

    A deployment dashboard keeps a key whose value field was left empty as an
    empty string rather than dropping it. `config(..., cast=int)` on such a
    value raised `ValueError: invalid literal for int() with base 10: ''` while
    the settings module was still being imported, which failed the Vercel build
    with a traceback naming neither the variable nor the fix.

    These run settings import in a subprocess against an injected repository so
    the developer's real .env cannot mask the behaviour being tested.
    """

    # Runs in a subprocess so the real .env cannot supply the values under
    # test. decouple resolves config through a lazily-built `config.config`
    # (a Config wrapping a repository) and Config.get reads os.environ first,
    # so both must be replaced for the injected values to be authoritative.
    DRIVER = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path({backend!r}) / "backend"))

import decouple
from decouple import UndefinedValueError, Config

class DictRepository:
    def __init__(self, data):
        self.data = data
    def __contains__(self, key):
        return key in self.data
    def __getitem__(self, key):
        if key in self.data:
            return self.data[key]
        raise UndefinedValueError(key)

for _key in list(os.environ):
    if _key.isupper() and ("EMAIL" in _key or "SECRET" in _key or "ALLOWED" in _key):
        del os.environ[_key]

decouple.config.config = Config(DictRepository(json.loads(sys.argv[1])))

os.environ["DJANGO_SETTINGS_MODULE"] = "home_improvement.settings"
import django
django.setup()
from django.conf import settings
keys = {keys!r}
print(json.dumps({{k: getattr(settings, k) for k in keys}}))
"""

    KEYS = [
        "DEBUG", "ALLOWED_HOSTS", "SITE_URL", "CSRF_TRUSTED_ORIGINS",
        "EMAIL_BACKEND", "EMAIL_HOST", "EMAIL_PORT", "EMAIL_USE_TLS",
        "EMAIL_USE_SSL", "EMAIL_HOST_USER", "DEFAULT_FROM_EMAIL",
        "ADMIN_EMAIL", "EMAIL_TIMEOUT", "SECURE_HSTS_SECONDS",
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", "SECURE_HSTS_PRELOAD",
        "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
        "CONTENT_ALLOW_TEMPLATES", "WHITENOISE_MAX_AGE",
    ]

    def load(self, env, expect_error=None):
        """Import settings in a subprocess; return resolved values or the error."""
        import json as _json
        import subprocess
        import sys as _sys

        script = self.DRIVER.format(
            backend=str(settings.REPO_ROOT), keys=self.KEYS
        )
        proc = subprocess.run(
            [_sys.executable, "-c", script, _json.dumps(env)],
            capture_output=True, text=True, cwd=str(settings.REPO_ROOT),
        )
        if expect_error:
            self.assertNotEqual(proc.returncode, 0, f"expected failure, got:\n{proc.stdout}")
            self.assertIn(expect_error, proc.stdout + proc.stderr)
            return None
        self.assertEqual(
            proc.returncode, 0,
            f"settings import failed:\n{proc.stdout}\n{proc.stderr}",
        )
        return _json.loads(proc.stdout.strip().splitlines()[-1])

    MINIMAL = {"SECRET_KEY": "x" * 50, "DEBUG": "False"}

    def test_minimal_environment_imports(self):
        """The site must be deployable before SMTP is configured."""
        cfg = self.load(self.MINIMAL)
        self.assertIs(cfg["DEBUG"], False)
        self.assertIn("console.EmailBackend", cfg["EMAIL_BACKEND"])
        self.assertEqual(cfg["EMAIL_PORT"], 587)
        self.assertIs(cfg["EMAIL_USE_TLS"], True)
        self.assertIs(cfg["EMAIL_USE_SSL"], False)
        self.assertEqual(cfg["EMAIL_TIMEOUT"], 10)

    def test_blank_int_vars_fall_back_to_defaults(self):
        for name, expected in (
            ("EMAIL_PORT", 587),
            ("EMAIL_TIMEOUT", 10),
            ("SECURE_HSTS_SECONDS", 31536000),
            ("WHITENOISE_MAX_AGE", 3600),
        ):
            with self.subTest(var=name):
                cfg = self.load({**self.MINIMAL, name: ""})
                self.assertEqual(cfg[name], expected)

    def test_blank_bool_vars_keep_their_default_not_false(self):
        """A blank must not silently become False.

        decouple's cast=bool calls bool('') -> False, which for the secure
        cookie flags is a silent security downgrade.
        """
        cfg = self.load({
            **self.MINIMAL,
            "SESSION_COOKIE_SECURE": "", "CSRF_COOKIE_SECURE": "",
            "SECURE_SSL_REDIRECT": "",
        })
        self.assertIs(cfg["SESSION_COOKIE_SECURE"], True)
        self.assertIs(cfg["CSRF_COOKIE_SECURE"], True)
        self.assertIs(cfg["SECURE_SSL_REDIRECT"], True)

    def test_explicit_false_still_works(self):
        """...but a deliberate 'False' must be honoured, not treated as blank."""
        cfg = self.load({
            **self.MINIMAL,
            "SECURE_HSTS_PRELOAD": "False",
            "SECURE_HSTS_INCLUDE_SUBDOMAINS": "false",
            "EMAIL_USE_TLS": "False",
            "DEBUG": "False",
        })
        self.assertIs(cfg["SECURE_HSTS_PRELOAD"], False)
        self.assertIs(cfg["SECURE_HSTS_INCLUDE_SUBDOMAINS"], False)
        self.assertIs(cfg["EMAIL_USE_TLS"], False)

    def test_csrf_origins_always_a_nonempty_list(self):
        """Regression: a blank value once returned a bare string.

        Django iterates CSRF_TRUSTED_ORIGINS, so a string made it validate each
        character as an origin and `manage.py check` failed with 17 errors.
        """
        for value in ("", "  ", ","):
            with self.subTest(value=value):
                cfg = self.load({**self.MINIMAL, "CSRF_TRUSTED_ORIGINS": value})
                origins = cfg["CSRF_TRUSTED_ORIGINS"]
                self.assertIsInstance(origins, list)
                self.assertTrue(origins)
                for origin in origins:
                    self.assertTrue(origin.startswith(("http://", "https://")))

    def test_allowed_hosts_blank_falls_back_and_never_empty(self):
        cfg = self.load({**self.MINIMAL, "ALLOWED_HOSTS": ""})
        self.assertIsInstance(cfg["ALLOWED_HOSTS"], list)
        self.assertTrue(cfg["ALLOWED_HOSTS"])

    def test_admin_email_drops_trailing_comma(self):
        cfg = self.load({**self.MINIMAL, "ADMIN_EMAIL": "a@b.c,"})
        self.assertEqual(cfg["ADMIN_EMAIL"], ["a@b.c"])
        cfg = self.load({**self.MINIMAL, "ADMIN_EMAIL": ""})
        self.assertEqual(cfg["ADMIN_EMAIL"], [])

    def test_real_typos_still_fail_and_name_the_variable(self):
        for env, needle in (
            ({"EMAIL_PORT": "notanumber"}, "EMAIL_PORT must be a whole number"),
            ({"SECURE_HSTS_SECONDS": "abc"}, "SECURE_HSTS_SECONDS must be a whole number"),
            ({"EMAIL_PORT": "0"}, "EMAIL_PORT must be >= 1"),
            ({"WHITENOISE_MAX_AGE": "-1"}, "WHITENOISE_MAX_AGE must be >= 0"),
            ({"ALLOWED_HOSTS": ", ,"}, "ALLOWED_HOSTS resolved to an empty list"),
        ):
            with self.subTest(env=env):
                self.load({**self.MINIMAL, **env}, expect_error=needle)
