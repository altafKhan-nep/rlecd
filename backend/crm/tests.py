"""Tests for the CRM.

The two behaviours that matter most and are easiest to break are covered
explicitly:

* a lead survives an email outage (the defect that used to lose enquiries)
* the public pages still render the mirrored markup (adding a CRM must not
  change what visitors see)
"""
from io import StringIO
from unittest import mock

import importlib
import os

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core import mail, management
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Page, Section
from crm import services
from crm.admin import LeadAdmin
from crm.audit import AUDIT_FIELDS
from crm.permissions import (
    Roles,
    assign_role,
    can_edit_leads,
    can_edit_users,
    can_view_leads,
    get_user_role,
    role_group_name,
    sync_role_groups,
)
from crm.models import (
    AuditLog,
    Contact,
    EmailOutbox,
    Lead,
    LeadActivity,
    LeadSource,
    LeadStatus,
    Priority,
    Service,
    Task,
    TeamMember,
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
        # The mail is queued, not sent: a visitor must not wait on SMTP, and an
        # SMTP outage must not turn into a lost enquiry.
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailOutbox.objects.filter(status="queued").count(), 2)

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
        """The visitor gets their success even with the mail server down.

        Asserted end to end: the request succeeds, the lead survives, the
        messages stay queued, and draining the queue afterwards is what fails
        rather than the form.
        """
        with mock.patch("crm.services.send_mail", side_effect=OSError("smtp down")):
            response = self.client.post("/contact/", {
                "name": "Gillian", "email": "g@example.com", "message": "Basement finish.",
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(Lead.objects.count(), 1)
            sent, failed = services.flush_outbox()

        self.assertEqual(sent, 0)
        self.assertEqual(failed, 2)
        self.assertEqual(EmailOutbox.objects.filter(status="queued").count(), 2)


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
    """Guards the container deployment configuration.

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
        # An unbounded SMTP connect would hold a request threadon open
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

    def test_unknown_parameter_is_forwarded_not_dropped(self):
        """Nothing is filtered, so a typo cannot silently disable a setting.

        Parsing no longer raises on an unknown keyword: settings.py must import
        without psycopg so the build can run `collectstatic`, which
        never connects. The parameter is still passed through verbatim, and
        libpq rejects an unrecognised one at connect time -- so a typo is a
        loud connection error, never a quietly absent security setting.
        """
        db = self.parse("postgresql://u:pw@host/db?totally_bogus=1")
        self.assertEqual(db["OPTIONS"]["totally_bogus"], "1")

    def test_blank_parameter_is_kept_not_dropped(self):
        # keep_blank_values: `?sslmode=` must be visible, not silently absent.
        db = self.parse("postgresql://u:pw@host/db?sslmode=")
        self.assertIn("sslmode", db["OPTIONS"])
        self.assertEqual(db["OPTIONS"]["sslmode"], "")

    def test_repeated_parameter_takes_the_last_value(self):
        db = self.parse("postgresql://u:pw@host/db?sslmode=disable&sslmode=require")
        self.assertEqual(db["OPTIONS"]["sslmode"], "require")

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("postgresql://u:pw@host:notaport/db")

    def test_matches_libpq_conninfo_to_dict(self):
        """Parity with psycopg's own parser.

        The pure-Python parser replaced psycopg.conninfo so the build does not
        need a libpq wheel. This pins its output to libpq's, so the two cannot
        drift. Skipped only where psycopg itself is unavailable -- which is the
        condition that made the build fail in the first place.
        """
        try:
            from psycopg.conninfo import conninfo_to_dict
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"libpq unavailable: {exc}")

        from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

        urls = [
            "postgresql://u:pw@host/db",
            "postgresql://u:pw@host:5432/db",
            "postgresql://u:p%40ss%3Aword@host:5432/db",
            "postgresql://u:pw@host/db?sslmode=require&channel_binding=require",
            "postgresql://u:pw@host/db?application_name=rlecd&connect_timeout=5",
            "postgresql://neondb_owner:secret@ep-abc-123-pooler.us-east-2.aws.neon.tech"
            "/neondb?sslmode=require&channel_binding=require",
            "postgresql://u:pw@host:5432/db?conn_max_age=120",
        ]
        for url in urls:
            with self.subTest(url=url):
                # Reference: hand libpq the URL minus the Django-only knob.
                parsed = urlparse(url)
                query = parse_qs(parsed.query)
                query.pop("conn_max_age", None)
                reference = conninfo_to_dict(
                    urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
                )
                reference.pop("connect_timeout", None)

                mine = self.parse(url)
                for field, key in (
                    ("NAME", "dbname"), ("USER", "user"),
                    ("PASSWORD", "password"), ("HOST", "host"),
                ):
                    self.assertEqual(mine[field], reference.get(key, ""), field)
                self.assertEqual(mine["PORT"], str(reference.get("port", "") or ""), "PORT")
                # OPTIONS is everything except the identifier keys, plus the
                # connect_timeout default this settings module adds.
                for key, value in reference.items():
                    if key in ("dbname", "user", "password", "host", "port"):
                        continue
                    self.assertEqual(mine["OPTIONS"].get(key), value, key)

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
    the settings module was still being imported, which failed the build
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

    def test_csrf_origins_is_a_flat_list_of_uris(self):
        """Every entry must be a bare scheme+host string.

        Two ways this went wrong: a blank value used to return the bare
        string, which Django then iterated character by character; and a
        list-shaped default got stringified and re-split, yielding one bogus
        origin like "['https://example.com']" that matches no Origin header.
        An empty list is valid and expected when nothing is configured --
        Django accepts same-origin POSTs without any entry.
        """
        for value in ("", "  ", ","):
            with self.subTest(value=value):
                cfg = self.load({**self.MINIMAL, "CSRF_TRUSTED_ORIGINS": value})
                origins = cfg["CSRF_TRUSTED_ORIGINS"]
                self.assertIsInstance(origins, list)
                for origin in origins:
                    self.assertIsInstance(origin, str)
                    self.assertTrue(
                        origin.startswith(("http://", "https://")),
                        f"{origin!r} is not a bare origin",
                    )

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


class RenderHostTests(SimpleTestCase):
    """Render generates hostnames, so ALLOWED_HOSTS must not be an exact list."""

    def _load(self, **overrides):
        mod = importlib.import_module("home_improvement.settings")
        env = {
            "RENDER": "1",
            "DEBUG": "False",
            "ALLOWED_HOSTS": "",
            "SITE_URL": "",
        }
        env.update(overrides)
        with mock.patch.dict(os.environ, env, clear=False):
            mod = importlib.reload(mod)
            self.addCleanup(importlib.reload, mod)
            return mod

    def test_blank_allowed_hosts_still_serves_generated_render_hostname(self):
        """A blank value must not 400 every Render request."""
        mod = self._load()
        self.assertIn(".onrender.com", mod.ALLOWED_HOSTS)
        self.assertNotIn(".onrender.com", mod.CSRF_TRUSTED_ORIGINS)

    def test_custom_domain_is_preserved_alongside_platform_host(self):
        mod = self._load(ALLOWED_HOSTS="example.com,www.example.com")
        self.assertEqual(
            [h for h in mod.ALLOWED_HOSTS if h != ".onrender.com"],
            ["example.com", "www.example.com"],
        )

    def test_platform_host_not_added_off_render(self):
        """Local dev and any non-Render host must not inherit the platform
        suffix, so a typo'd host still fails loudly in development."""
        mod = importlib.import_module("home_improvement.settings")
        with mock.patch.dict(os.environ, {"ALLOWED_HOSTS": "example.com"}, clear=True):
            mod = importlib.reload(mod)
            self.addCleanup(importlib.reload, mod)
            self.assertNotIn(".onrender.com", mod.ALLOWED_HOSTS)


class NoConfiguredDomainTests(TestCase):
    """A first deploy must work on the platform hostname with zero domain config.

    The operator may not own any domain yet, so ALLOWED_HOSTS, SITE_URL and
    CSRF_TRUSTED_ORIGINS all have to be optional without a 400 or a 403.
    """

    def _load(self, **overrides):
        mod = importlib.import_module("home_improvement.settings")
        env = {
            "RENDER": "1", "DEBUG": "False",
            "ALLOWED_HOSTS": "", "SITE_URL": "", "CSRF_TRUSTED_ORIGINS": "",
        }
        env.update(overrides)
        with mock.patch.dict(os.environ, env, clear=False):
            mod = importlib.reload(mod)
            self.addCleanup(importlib.reload, mod)
            return mod

    def test_no_domain_anywhere_yields_no_stray_uris(self):
        """Nothing may invent a hostname the operator never configured."""
        mod = self._load()
        self.assertEqual(mod.SITE_URL, "")
        self.assertEqual(mod.CSRF_TRUSTED_ORIGINS, [])
        self.assertNotIn("rlecd.com", str(mod.ALLOWED_HOSTS))
        self.assertNotIn("quantumcoresoftware.com", str(mod.ALLOWED_HOSTS))

    def test_generated_render_hostname_allowed(self):
        self.assertIn(".onrender.com", self._load().ALLOWED_HOSTS)

    def test_canonical_and_og_follow_the_request_host(self):
        """Tags must point at the address visitors used, not a hardcoded domain."""
        with mock.patch.dict(os.environ, {"SITE_URL": ""}, clear=False):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        head = body[: body.index("</head>")]
        self.assertIn('<link rel="canonical" href="http://testserver/">', head)
        self.assertIn('property="og:url" content="http://testserver/"', head)
        # No config-derived tag may name a domain the operator never set. The
        # business's own contact email in the body is content, left alone.
        self.assertNotIn("rlecd.com", head)

    def test_same_origin_post_works_without_csrf_trusted_origins(self):
        """Django accepts same-origin POSTs, so lead forms work on first deploy."""
        from crm.models import Lead
        with mock.patch.dict(os.environ, {"SITE_URL": ""}, clear=False):
            response = self.client.post(
                "/contact/",
                {"name": "A", "email": "a@b.c", "phone": "4105551234",
                 "service": "Kitchen", "message": "hi", "website": ""},
            )
        self.assertNotEqual(response.status_code, 403)
        self.assertTrue(Lead.objects.filter(email="a@b.c").exists())


class AuditLogTests(TestCase):
    """Who changed what, and when.

    The activity trail is the *business* history of a lead. This is the
    *accounting* of the CRM: every create, update and delete of a CRM row, with
    the actor and the request behind it. The two are separate because a lead's
    activity trail cannot be trusted to answer "who moved this to Lost".
    """

    def setUp(self):
        self.user = User.objects.create_user("staff", "s@example.com", "pw",
                                             is_staff=True)
        self.lead = Lead.objects.create(name="Ann", email="a@e.com",
                                        message="kitchen")

    def test_create_is_recorded(self):
        row = AuditLog.objects.get(model="crm.lead", object_id=str(self.lead.pk))
        self.assertEqual(row.action, AuditLog.Action.CREATE)
        self.assertEqual(row.object_repr, str(self.lead))

    def test_update_records_only_the_fields_that_moved(self):
        self.lead.status = LeadStatus.QUALIFIED
        self.lead.save()
        row = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).get()
        self.assertEqual(row.changes,
                         {"status": {"from": LeadStatus.NEW,
                                     "to": LeadStatus.QUALIFIED}})

    def test_a_save_that_changes_nothing_is_not_recorded(self):
        """Otherwise the useful rows drown in rows saying "nothing happened"."""
        before = AuditLog.objects.count()
        self.lead.save()
        self.assertEqual(AuditLog.objects.count(), before)

    def test_delete_is_recorded_after_the_row_is_gone(self):
        """The object_id is a string precisely so the evidence outlives the row."""
        pk = self.lead.pk
        self.lead.delete()
        row = AuditLog.objects.filter(action=AuditLog.Action.DELETE).get()
        self.assertEqual(row.object_id, str(pk))

    def test_actor_comes_from_the_request(self):
        from crm import audit
        from django.test import RequestFactory

        request = RequestFactory().post("/admin/crm/lead/1/change/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "203.0.113.9"
        audit.audit_context(request)
        try:
            self.lead.phone = "410-555-0100"
            self.lead.save()
        finally:
            audit.clear_audit_context()
        row = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).get()
        self.assertEqual(row.actor, self.user)
        self.assertEqual(row.ip_address, "203.0.113.9")
        self.assertEqual(row.path, "/admin/crm/lead/1/change/")

    def test_no_request_means_no_actor_rather_than_an_error(self):
        """capture_lead runs outside any request; that must not break logging."""
        self.lead.status = LeadStatus.CONTACTED
        self.lead.save()
        row = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).get()
        self.assertIsNone(row.actor)

    def test_forwarded_for_wins_behind_a_proxy(self):
        """Render terminates TLS in front of the app, so REMOTE_ADDR is the proxy."""
        from crm import audit
        from django.test import RequestFactory

        request = RequestFactory().post("/x/")
        request.user = self.user
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.4, 10.0.0.1"
        audit.audit_context(request)
        try:
            self.assertEqual(audit._ip(request), "198.51.100.4")
        finally:
            audit.clear_audit_context()

    def test_a_bulk_update_is_NOT_recorded(self):
        """Documents a real limitation, so nobody relies on a false guarantee.

        `QuerySet.update()` emits no save signals, so it is invisible here. This
        test is named to make that unmistakable rather than to assert coverage:
        it will keep passing after someone wires bulk updates up, and whoever
        does that should delete or invert it.
        """
        self.assertEqual(self.lead.status, LeadStatus.NEW)
        Lead.objects.filter(pk=self.lead.pk).update(status=LeadStatus.WON)
        self.assertFalse(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE).exists())

    def test_looping_and_saving_is_recorded(self):
        """The pattern the admin's bulk actions use, and the one to copy."""
        self.lead.status = LeadStatus.WON
        self.lead.save()
        row = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).get()
        self.assertEqual(row.changes["status"]["to"], LeadStatus.WON)

    def test_the_customers_submission_is_not_diffed(self):
        """A lead's message is paragraphs; one edit must not become a wall."""
        self.assertNotIn("message", AUDIT_FIELDS["crm.lead"])

    def test_staff_notes_are_diffed(self):
        """The opposite decision to `message`, on purpose: notes are short, and
        "who edited the note" is a question worth being able to answer."""
        self.assertIn("notes", AUDIT_FIELDS["crm.lead"])

    def test_the_audit_log_never_audits_itself(self):
        self.assertNotIn("crm.auditlog", AUDIT_FIELDS)


class RoundRobinTests(TestCase):
    """A lead that arrives at 2pm has to end up with an owner.

    The plan's exit criterion is "a lead submitted at 2pm is in the pipeline
    with an owner". Nothing assigned leads before this, so every lead sat
    unassigned until somebody noticed.
    """

    def setUp(self):
        self.users = [
            User.objects.create_user(f"u{i}", f"u{i}@e.com", "pw")
            for i in range(3)
        ]
        self.smith = User.objects.create_user("smith", "s@e.com", "pw")
        self.bath = Service.objects.create(name="Bathroom", slug="bathroom")
        self.kitchen = Service.objects.create(name="Kitchen", slug="kitchen")

    def member(self, name, user, **kwargs):
        return TeamMember.objects.create(name=name, user=user, **kwargs)

    def lead(self, **kwargs):
        defaults = dict(name="Ann", email="a@e.com", message="hello there")
        defaults.update(kwargs)
        return Lead.objects.create(**defaults)

    # --- rotation -------------------------------------------------------
    def test_leads_rotate_rather_than_piling_on_one_person(self):
        for user in self.users:
            self.member(user.username, user)
        picked = [services.assign_round_robin().user for _ in range(6)]
        self.assertEqual(len(set(picked)), 3)
        # Two each after six, in the configured order.
        self.assertEqual([u.username for u in picked],
                         ["u0", "u1", "u2", "u0", "u1", "u2"])

    def test_the_counter_is_incremented_atomically(self):
        member = self.member("solo", self.users[0])
        for _ in range(3):
            services.assign_round_robin()
        member.refresh_from_db()
        self.assertEqual(member.assignment_count, 3)

    def test_a_tie_breaks_on_sort_order_not_arbitrarily(self):
        """Two members at zero must not both be 'first' on every call."""
        self.member("b", self.users[0], sort_order=1)
        self.member("a", self.users[1], sort_order=0)
        picked = [services.assign_round_robin().name for _ in range(2)]
        self.assertEqual(picked, ["a", "b"])

    # --- eligibility ----------------------------------------------------
    def test_inactive_members_are_skipped(self):
        self.member("gone", self.users[0], is_active=False)
        self.member("here", self.users[1])
        self.assertEqual(services.assign_round_robin().name, "here")

    def test_a_member_without_a_user_is_skipped(self):
        """There is nobody to own the lead, so assigning to them is a no-op
        that would leave the lead unassigned and the counter burned."""
        self.member("no account", None)
        self.member("real", self.users[0])
        self.assertEqual(services.assign_round_robin().name, "real")

    def test_a_specialist_is_preferred_for_their_service(self):
        """Recording a service has to mean something.

        At equal assignment counts the bathroom fitter gets the bathroom lead,
        not whoever was created first.
        """
        self.member("general", self.users[0])
        plumber = self.member("bath fitter", self.users[1], service=self.bath)
        self.assertEqual(services.assign_round_robin(service=self.bath).name,
                         "bath fitter")
        # Still round-robin within the specialist tier, so the load stays even.
        self.assertEqual(services.assign_round_robin(service=self.bath).name,
                         "bath fitter")
        plumber.refresh_from_db()
        self.assertEqual(plumber.assignment_count, 2)

    def test_deactivating_the_specialist_routes_to_the_generalist(self):
        """Coverage has to have an off switch, or a holiday stops intake."""
        self.member("general", self.users[0])
        plumber = self.member("bath fitter", self.users[1], service=self.bath)
        plumber.is_active = False
        plumber.save()
        self.assertEqual(services.assign_round_robin(service=self.bath).name,
                         "general")

    def test_a_specialist_does_not_take_another_services_lead(self):
        self.member("bath fitter", self.users[0], service=self.bath)
        general = self.member("general", self.users[1])
        self.assertEqual(services.assign_round_robin(service=self.kitchen).name,
                         general.name)

    def test_a_service_mismatch_still_falls_back_to_a_generalist(self):
        """Better a generalist than nobody: the lead exists either way."""
        self.member("bath only", self.users[0], service=self.bath)
        self.member("general", self.users[1])
        self.assertEqual(services.assign_round_robin(service=self.kitchen).name,
                         "general")

    def test_territory_matching_is_exact_not_partial(self):
        """A member covering 'Baltimore' must not silently take 'Baltimore County'
        -- or, worse, a lead from anywhere else."""
        balt = self.member("balt", self.users[0], territory="Baltimore")
        self.member("everywhere", self.users[1])
        self.assertEqual(services.assign_round_robin(territory="Baltimore").name,
                         balt.name)
        self.assertEqual(services.assign_round_robin(territory="Owings Mills").name,
                         "everywhere")

    def test_no_eligible_member_returns_none_rather_than_raising(self):
        """A young team is a normal state. The lead stays visibly unassigned
        and the dashboard says so, which beats force-assigning."""
        self.assertIsNone(services.assign_round_robin())
        self.assertIsNone(services.assign_round_robin(service=self.bath))

    def test_an_empty_team_is_not_an_error(self):
        lead = self.lead()
        self.assertIsNone(services.auto_assign(lead))
        lead.refresh_from_db()
        self.assertIsNone(lead.owner)
        # No activity invented for an assignment that did not happen.
        self.assertFalse(
            lead.activities.filter(kind=LeadActivity.Kind.ASSIGNED).exists())

    # --- wired into capture ---------------------------------------------
    def test_capture_lead_assigns_an_owner_automatically(self):
        self.member("owner", self.users[0])
        lead = services.capture_lead(
            name="Ann", email="a@e.com", message="I want a kitchen",
            service_raw="kitchen")
        self.assertEqual(lead.owner, self.users[0])
        self.assertTrue(
            lead.activities.filter(kind=LeadActivity.Kind.ASSIGNED).exists())

    def test_capture_still_works_with_no_team_configured(self):
        """The whole point of the 'a lead is never lost' rule: assignment is
        additive and must not be able to fail the capture."""
        lead = services.capture_lead(
            name="Ann", email="a@e.com", message="I want a kitchen")
        self.assertIsNotNone(lead.pk)
        self.assertIsNone(lead.owner)

    def test_rotation_spans_separate_captures(self):
        for user in self.users:
            self.member(user.username, user)
        owners = [
            services.capture_lead(name=f"n{i}", email=f"{i}@e.com",
                                  message="hello there").owner
            for i in range(3)
        ]
        self.assertEqual([u.username for u in owners], ["u0", "u1", "u2"])


class MergeLeadsTests(TestCase):
    """A repeat enquiry is the likeliest way this business loses someone.

    Two rows for the same job, and whichever is not followed up is a lead that
    never got a call. `match_duplicate` already found the earlier row; these
    tests cover acting on it.
    """

    def setUp(self):
        self.owner = User.objects.create_user("o", "o@e.com", "pw")
        self.old = Lead.objects.create(
            name="Ann", email="ann@e.com", message="first enquiry", score=40)
        self.new = Lead.objects.create(
            name="Ann Smith", email="ann@e.com", message="second, with more",
            phone="410-555-0100", score=80)
        for lead in (self.old, self.new):
            LeadActivity.objects.create(
                lead=lead, kind=LeadActivity.Kind.NOTED, summary="note")

    def test_the_duplicate_is_kept_not_deleted(self):
        """An irreversible delete on a lead pipeline is not a safe default: the
        duplicate may hold the only record of what the customer said."""
        services.merge_leads(self.old, [self.new])
        self.assertTrue(Lead.objects.filter(pk=self.new.pk).exists())

    def test_the_duplicate_is_taken_out_of_the_pipeline(self):
        services.merge_leads(self.old, [self.new])
        self.new.refresh_from_db()
        self.assertEqual(self.new.status, LeadStatus.LOST)
        self.assertIn(f"merged into lead #{self.old.pk}", self.new.notes)

    def test_a_blank_field_is_filled_from_the_duplicate(self):
        """A phone number captured on the second submission is data the primary
        is missing, not a conflict to resolve."""
        services.merge_leads(self.old, [self.new])
        self.old.refresh_from_db()
        self.assertEqual(self.old.phone, "410-555-0100")

    def test_the_primary_wins_when_both_have_a_value(self):
        self.old.phone = "410-555-9999"
        self.old.save()
        services.merge_leads(self.old, [self.new])
        self.old.refresh_from_db()
        self.assertEqual(self.old.phone, "410-555-9999")

    def test_the_higher_score_wins(self):
        """A repeat enquiry with more detail is the more engaged of the pair."""
        services.merge_leads(self.old, [self.new])
        self.old.refresh_from_db()
        self.assertEqual(self.old.score, 80)

    def test_priority_is_ranked_not_string_compared(self):
        """'high' sorts before 'normal' alphabetically, so a string comparison
        would pick the wrong one."""
        self.old.priority, self.old.score = Priority.NORMAL, 40
        self.old.save()
        self.new.priority, self.new.score = Priority.URGENT, 90
        self.new.save()
        services.merge_leads(self.old, [self.new])
        self.old.refresh_from_db()
        self.assertEqual(self.old.priority, Priority.URGENT)

    def test_activities_are_carried_across(self):
        services.merge_leads(self.old, [self.new])
        summaries = list(self.old.activities.values_list("summary", flat=True))
        self.assertIn("note", summaries)
        self.assertTrue(any("Merged lead" in s for s in summaries))
        # The duplicate keeps its own copy too: nothing is destroyed.
        self.assertEqual(self.new.activities.filter(summary="note").count(), 1)

    def test_open_tasks_follow_the_primary(self):
        """A follow-up owed on the duplicate is still owed, and re-pointing
        keeps it on the board instead of stranding it."""
        task = Task.objects.create(lead=self.new, title="call back")
        services.merge_leads(self.old, [self.new])
        task.refresh_from_db()
        self.assertEqual(task.lead, self.old)

    def test_merging_into_itself_is_refused(self):
        with self.assertRaises(services.MergeError):
            services.merge_leads(self.old, [self.old])

    def test_merging_nothing_is_refused(self):
        with self.assertRaises(services.MergeError):
            services.merge_leads(self.old, [])

    def test_several_duplicates_at_once(self):
        third = Lead.objects.create(name="Ann", email="ann@e.com",
                                    message="third")
        services.merge_leads(self.old, [self.new, third])
        self.assertEqual(self.old.activities.filter(
            kind=LeadActivity.Kind.MERGED).count(), 2)
        self.assertFalse(Lead.objects.filter(
            pk__in=[self.new.pk, third.pk], status=LeadStatus.NEW).exists())

    def test_match_duplicate_finds_the_earlier_lead(self):
        """The discovery half of the flow, which merge_leads then acts on."""
        matches = self.new.match_duplicate()
        self.assertEqual([lead.pk for lead in matches], [self.old.pk])


class PipelineBoardTests(TestCase):
    """The board answers "what is stuck", which a sorted table does not.

    Read-only by design: moving a lead stays a deliberate, confirmed action on
    the changelist, because a card that silently moved on drag would be a worse
    thing to hand a salesperson than a link.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.client.force_login(self.admin)

    def lead(self, name="Ann", **kwargs):
        defaults = dict(name=name, email=f"{name}@e.com", message="hello there")
        defaults.update(kwargs)
        return Lead.objects.create(**defaults)

    def test_the_board_renders_one_column_per_stage(self):
        response = self.client.get(reverse("admin:pipeline_board"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/kanban.html")
        self.assertEqual(len(response.context["board"]["columns"]),
                         len(LeadStatus.choices))

    def test_a_lead_appears_as_a_card_linking_to_it(self):
        lead = self.lead("Ann")
        response = self.client.get(reverse("admin:pipeline_board"))
        self.assertContains(
            response,
            reverse("admin:crm_lead_change", args=[lead.pk]))

    def test_columns_are_ordered_by_pipeline_stage(self):
        """Won and Lost come last, so the board reads left to right as the
        journey does."""
        labels = [c["label"] for c in self.client.get(
            reverse("admin:pipeline_board")).context["board"]["columns"]]
        self.assertEqual(labels[0], dict(LeadStatus.choices)[LeadStatus.NEW])
        self.assertEqual(labels[-2:], ["Won", "Lost"])

    def test_an_unowned_lead_is_called_out(self):
        """The lead exists but nobody owns it, which is how it gets forgotten.
        This is the one thing the board should interrupt you for."""
        self.lead()
        response = self.client.get(reverse("admin:pipeline_board"))
        self.assertContains(response, "is-unowned")

    def test_an_owned_lead_shows_the_owner(self):
        owner = User.objects.create_user("sam", "sam@e.com", "pw")
        self.lead(owner=owner)
        response = self.client.get(reverse("admin:pipeline_board"))
        self.assertContains(response, "sam")

    def test_hot_leads_are_badged_and_counted(self):
        self.lead(score=services.HOT_THRESHOLD)
        board = self.client.get(reverse("admin:pipeline_board")).context["board"]
        self.assertEqual(board["hot"], 1)

    def test_the_totals_add_up(self):
        self.lead("A")
        self.lead("B")
        board = self.client.get(reverse("admin:pipeline_board")).context["board"]
        self.assertEqual(board["total"], 2)
        self.assertEqual(board["unassigned"], 2)

    def test_a_column_is_capped_so_the_board_stays_a_board(self):
        """A board rendering four hundred rows is a list with extra steps."""
        for i in range(5):
            self.lead(f"P{i}")
        with mock.patch.object(
                __import__("main.admin_site", fromlist=["StudioAdminSite"]
                           ).StudioAdminSite, "BOARD_LIMIT", 2):
            board = self.client.get(
                reverse("admin:pipeline_board")).context["board"]
        self.assertEqual(board["columns"][0]["count"], 2)

    def test_an_empty_pipeline_says_so_rather_than_rendering_blanks(self):
        response = self.client.get(reverse("admin:pipeline_board"))
        self.assertContains(response, "No open leads")

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("admin:pipeline_board")).status_code, 302)

    def test_the_nav_offers_the_board(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("admin:pipeline_board"))


class TeamAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.client.force_login(self.admin)
        self.user = User.objects.create_user("sam", "sam@e.com", "pw")

    def test_changelist_loads(self):
        TeamMember.objects.create(name="Sam", user=self.user)
        self.assertEqual(
            self.client.get(reverse("admin:crm_teammember_changelist")).status_code,
            200)

    def test_it_reports_open_leads_per_member(self):
        """Otherwise a member looks idle while holding twenty open leads.

        Asserted through the rendered list, since that is what the operator
        reads, rather than by calling the column method directly.
        """
        TeamMember.objects.create(name="Sam", user=self.user)
        url = reverse("admin:crm_teammember_changelist")
        self.assertContains(self.client.get(url), "Sam")
        Lead.objects.create(name="A", email="a@e.com", message="hi",
                            owner=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "open_leads")

    def test_merge_action_refuses_a_single_lead(self):
        """Merging one lead into itself is the mistake the guard exists for."""
        lead = Lead.objects.create(name="A", email="a@e.com", message="hi")
        response = self.client.post(reverse("admin:crm_lead_changelist"), {
            "action": "action_merge_leads",
            "_selected_action": [str(lead.pk)],
        }, follow=True)
        lead.refresh_from_db()
        self.assertEqual(lead.status, LeadStatus.NEW)
        self.assertContains(response, "Select at least two leads to merge")

    def test_merge_action_folds_the_selection_into_the_oldest(self):
        old = Lead.objects.create(name="A", email="a@e.com", message="first",
                                  created_at=timezone.now())
        new = Lead.objects.create(name="A", email="a@e.com", message="second",
                                  created_at=timezone.now() + timezone.timedelta(days=1))
        self.client.post(reverse("admin:crm_lead_changelist"), {
            "action": "action_merge_leads",
            "_selected_action": [str(new.pk), str(old.pk)],
        }, follow=True)
        new.refresh_from_db()
        self.assertEqual(new.status, LeadStatus.LOST)
        self.assertIn(LeadActivity.Kind.MERGED,
                      list(old.activities.values_list("kind", flat=True)))

    def test_round_robin_action_reports_when_nobody_is_eligible(self):
        """Silently doing nothing would look like the action worked."""
        lead = Lead.objects.create(name="A", email="a@e.com", message="hi")
        response = self.client.post(reverse("admin:crm_lead_changelist"), {
            "action": "action_assign_round_robin",
            "_selected_action": [str(lead.pk)],
        }, follow=True)
        self.assertContains(response, "no eligible team member")


class RolePermissionTests(TestCase):
    """Roles are Groups holding real Django permissions.

    Asserted through the admin rather than the helper functions, because a
    helper that agrees with itself proves nothing: what matters is that the
    URLs a role should not reach are actually refused.
    """

    def setUp(self):
        self.lead = Lead.objects.create(name="A", email="a@e.com", message="hi")
        self.groups, self.missing = sync_role_groups()
        self.assertEqual(self.missing, [], "Roles.PERMISSIONS names a codename no model provides")

    def staff(self, username, role):
        user = User.objects.create_user(
            username=username, password=f"pw-{username}-123", is_staff=True)
        if role:
            user.groups.add(self.groups[role])
        return user

    def test_every_declared_role_resolves_to_real_permissions(self):
        for role, group in self.groups.items():
            with self.subTest(role=role):
                self.assertTrue(
                    group.permissions.exists(),
                    f"role {role} ended up with no permissions")

    def test_a_manager_can_reach_leads_but_not_users(self):
        user = self.staff("mgr", Roles.MANAGER)
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("admin:crm_lead_changelist")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:auth_user_changelist")).status_code, 403)

    def test_a_content_editor_is_locked_out_of_leads(self):
        """The point of the role: editing copy must not expose enquiries."""
        user = self.staff("editor", Roles.CONTENT_EDITOR)
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("admin:crm_lead_changelist")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin:content_page_changelist")).status_code, 200)

    def test_read_only_sees_pages_but_cannot_change_them(self):
        user = self.staff("ro", Roles.READ_ONLY)
        page = Page.objects.create(title="P", slug="p", path="/p/")
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("admin:content_page_changelist")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:content_page_change", args=[page.pk])).status_code,
            200)
        response = self.client.post(reverse("admin:content_page_change", args=[page.pk]), {
            "title": "P", "slug": "p", "path": "/p/", "status": "published",
            "is_published": "on", "show_in_menu": "on", "_save": "Save",
        })
        self.assertIn(response.status_code, (403, 302))
        page.refresh_from_db()
        self.assertEqual(page.title, "P")

    def test_a_direct_permission_grant_still_works_without_a_role(self):
        """Superuser setup and per-model grants must keep working."""
        user = User.objects.create_user(
            username="viewer", password="pw-viewer-123", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="crm", codename="view_lead"))
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("admin:crm_lead_changelist")).status_code, 200)

    def test_superuser_bypasses_everything(self):
        boss = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.assertEqual(get_user_role(boss), Roles.SUPER_ADMIN)
        self.assertTrue(can_edit_users(boss))
        self.assertTrue(can_edit_leads(boss))

    def test_assign_role_replaces_the_previous_one(self):
        user = self.staff("mover", Roles.SALES)
        assign_role(user, Roles.MANAGER, replace=True)
        user = User.objects.get(pk=user.pk)
        self.assertEqual(get_user_role(user), Roles.MANAGER)

    def test_an_unknown_role_is_rejected_rather_than_silently_ignored(self):
        user = self.staff("typo", Roles.SALES)
        with self.assertRaises(ValueError):
            assign_role(user, "wizard")

    def test_a_group_that_merely_shares_a_role_name_is_not_a_role(self):
        user = self.staff("impostor", None)
        user.groups.add(Group.objects.create(name="manager"))
        self.assertIsNone(get_user_role(user))
        self.assertFalse(can_view_leads(user))


class SyncRolesCommandTests(TestCase):
    def test_it_reports_every_role(self):
        out = StringIO()
        call_command("sync_roles", stdout=out)
        text = out.getvalue()
        for role in Roles.ALL:
            self.assertIn(role_group_name(role), text)

    def test_it_is_idempotent(self):
        call_command("sync_roles", stdout=StringIO())
        first = {g.name: set(g.permissions.values_list("codename", flat=True))
                 for g in Group.objects.all()}
        call_command("sync_roles", stdout=StringIO())
        second = {g.name: set(g.permissions.values_list("codename", flat=True))
                  for g in Group.objects.all()}
        self.assertEqual(first, second)

    def test_it_assigns_a_role_with_both_flags(self):
        User.objects.create_user(username="alice", password="pw-alice-123")
        call_command("sync_roles", "--assign", Roles.MANAGER,
                     "--user", "alice", stdout=StringIO())
        self.assertEqual(get_user_role(User.objects.get(username="alice")),
                         Roles.MANAGER)

    def test_it_refuses_half_an_assignment(self):
        with self.assertRaises(CommandError):
            call_command("sync_roles", "--assign", Roles.MANAGER,
                         stdout=StringIO())

    def test_it_reports_an_unknown_user(self):
        with self.assertRaises(CommandError):
            call_command("sync_roles", "--assign", Roles.MANAGER,
                         "--user", "nobody", stdout=StringIO())


class OutboxTests(TestCase):
    """The enquiry path queues; a command sends.

    Every assertion here is about the same guarantee: the promise to answer an
    enquiry is written down before anything is sent, and a failure to send is
    recorded rather than swallowed.
    """

    def setUp(self):
        self.lead = services.capture_lead(
            name="Fox Mulder", email="fox@example.com", message="Need a quote.",
        )

    def test_capturing_a_lead_queues_two_messages(self):
        rows = EmailOutbox.objects.filter(lead=self.lead)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            sorted(rows.values_list("kind", flat=True)),
            sorted([EmailOutbox.Kind.ADMIN, EmailOutbox.Kind.CUSTOMER]),
        )
        self.assertEqual(mail.outbox, [])

    def test_the_admin_message_names_the_service_and_score(self):
        body = EmailOutbox.objects.get(
            lead=self.lead, kind=EmailOutbox.Kind.ADMIN).body
        self.assertIn(self.lead.name, body)
        self.assertIn(f"{self.lead.score}/100", body)

    def test_the_capture_and_the_queue_are_one_transaction(self):
        """A queue that can disagree with the pipeline is worse than none."""
        with mock.patch("crm.services.enqueue_notifications",
                        side_effect=RuntimeError("outbox down")):
            with self.assertRaises(RuntimeError):
                services.capture_lead(name="A", email="a@e.com", message="hi")
        self.assertFalse(Lead.objects.filter(email="a@e.com").exists())
        self.assertEqual(EmailOutbox.objects.filter(lead__email="a@e.com").count(), 0)

    def test_flushing_sends_both_messages(self):
        sent, failed = services.flush_outbox()
        self.assertEqual((sent, failed), (2, 0))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(EmailOutbox.objects.filter(status="sent").count(), 2)

    def test_sending_records_who_was_emailed(self):
        services.flush_outbox()
        kinds = LeadActivity.objects.filter(
            lead=self.lead, kind=LeadActivity.Kind.EMAILED).count()
        self.assertEqual(kinds, 2)

    def test_a_failure_is_recorded_and_backs_off(self):
        with mock.patch("crm.services.send_mail", side_effect=OSError("smtp down")):
            services.flush_outbox()
        row = EmailOutbox.objects.first()
        row.refresh_from_db()
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.status, EmailOutbox.Status.QUEUED)
        self.assertIn("smtp down", row.last_error)
        self.assertGreater(row.next_attempt_at, timezone.now())

    def test_a_message_waiting_to_retry_is_not_sent_early(self):
        with mock.patch("crm.services.send_mail", side_effect=OSError("down")):
            services.flush_outbox()
        sent, failed = services.flush_outbox()
        self.assertEqual((sent, failed), (0, 0))

    def test_it_stops_after_three_attempts(self):
        """Retrying forever at a dead address is how a queue becomes a landfill."""
        with mock.patch("crm.services.send_mail", side_effect=OSError("down")):
            for _ in range(EmailOutbox.MAX_ATTEMPTS):
                EmailOutbox.objects.update(
                    status=EmailOutbox.Status.QUEUED,
                    next_attempt_at=timezone.now())
                services.flush_outbox()
            # One more pass must not produce another attempt.
            EmailOutbox.objects.update(next_attempt_at=timezone.now())
            sent, failed = services.flush_outbox()
        self.assertEqual((sent, failed), (0, 0))
        row = EmailOutbox.objects.first()
        row.refresh_from_db()
        self.assertEqual(row.attempts, EmailOutbox.MAX_ATTEMPTS)
        self.assertEqual(row.status, EmailOutbox.Status.FAILED)
        self.assertTrue(row.is_exhausted)

    def test_one_bad_message_does_not_stop_the_next(self):
        good = EmailOutbox.objects.filter(
            lead=self.lead, kind=EmailOutbox.Kind.CUSTOMER).first()
        with mock.patch("crm.services.send_mail") as sender:
            sender.side_effect = [OSError("down"), None]
            sent, failed = services.flush_outbox()
        self.assertEqual((sent, failed), (1, 1))
        good.refresh_from_db()
        self.assertEqual(good.status, EmailOutbox.Status.SENT)

    def test_a_lead_with_no_email_still_notifies_the_office(self):
        lead = Lead.objects.create(name="Anon", email="anon@example.com")
        lead.email = ""
        rows = services.enqueue_notifications(lead)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, EmailOutbox.Kind.ADMIN)

    def test_the_outbox_survives_the_lead_being_deleted(self):
        EmailOutbox.objects.filter(lead=self.lead).update(lead=None)
        self.lead.delete()
        self.assertEqual(EmailOutbox.objects.count(), 2)
        sent, _ = services.flush_outbox()
        self.assertEqual(sent, 2)


class SendOutboxCommandTests(TestCase):
    def setUp(self):
        self.lead = services.capture_lead(
            name="Fox", email="fox@example.com", message="Quote please.")
        self.admin = User.objects.create_superuser("boss", "b@e.com", "pw")
        self.client.force_login(self.admin)

    def test_it_sends_and_reports(self):
        out = StringIO()
        call_command("send_outbox", stdout=out)
        self.assertIn("2 sent", out.getvalue())

    def test_it_reports_a_stuck_queue(self):
        """A run that sends nothing must say the queue is stuck, not "done"."""
        EmailOutbox.objects.update(attempts=EmailOutbox.MAX_ATTEMPTS)
        out = StringIO()
        call_command("send_outbox", stdout=out)
        self.assertIn("retry-failed", out.getvalue())

    def test_retry_failed_requeues_and_sends(self):
        EmailOutbox.objects.update(attempts=EmailOutbox.MAX_ATTEMPTS,
                                   status=EmailOutbox.Status.FAILED)
        call_command("send_outbox", "--retry-failed", stdout=StringIO())
        self.assertEqual(EmailOutbox.objects.filter(status="sent").count(), 2)

    def test_the_outbox_screen_is_reachable(self):
        url = reverse("admin:crm_emailoutbox_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)
        html = self.client.get(url).content.decode()
        self.assertIn("Acknowledgement to the customer", html)
        self.assertIn("agm@rlecd.com", html)

    def test_an_operator_can_requeue_by_hand(self):
        row = EmailOutbox.objects.first()
        self.client.post(reverse("admin:crm_emailoutbox_changelist"), {
            "action": "retry_selected", "_selected_action": [str(row.pk)],
        })
        row.refresh_from_db()
        self.assertEqual(row.attempts, 0)
        self.assertTrue(row.is_ready)

    def test_an_operator_can_mark_a_hand_sent_message_as_done(self):
        row = EmailOutbox.objects.first()
        self.client.post(reverse("admin:crm_emailoutbox_changelist"), {
            "action": "discard_selected", "_selected_action": [str(row.pk)],
        })
        row.refresh_from_db()
        self.assertEqual(row.status, EmailOutbox.Status.SENT)
        self.assertIsNotNone(row.sent_at)

    def test_outbox_messages_cannot_be_typed_in(self):
        response = self.client.get(reverse("admin:crm_emailoutbox_add"))
        self.assertIn(response.status_code, (403, 302))
