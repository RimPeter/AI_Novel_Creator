import json
import shutil
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

from allauth.account.models import EmailAddress
from stripe._stripe_object import StripeObject
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from .billing import process_webhook_event, sync_checkout_session, sync_subscription_record
from .llm import LLMResult, SYSTEM_PROMPT, call_llm
from .models import (
    BillingCompanyProfile,
    BillingInvoice,
    Character,
    GenerationRun,
    HomeUpdate,
    Location,
    NovelProject,
    OutlineNode,
    ProcessedStripeEvent,
    StoryBible,
    StoryBibleDocument,
    UserSubscription,
    UserTextModelPreference,
)
from .signals import sync_legacy_account_emails
from .views import _get_story_bible_context


class AuthenticatedTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="password123",
        )
        self.client.force_login(self.user)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AccountEmailFlowTests(TestCase):
    def test_legacy_accounts_get_verified_primary_email_records(self):
        user = get_user_model().objects.create_user(
            username="legacywriter",
            email="legacywriter@example.com",
            password="password123",
        )

        sync_legacy_account_emails()

        email_address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertTrue(email_address.primary)
        self.assertTrue(email_address.verified)

    def test_request_login_code_marks_legacy_email_as_verified(self):
        user = get_user_model().objects.create_user(
            username="legacycode",
            email="legacycode@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )

        response = self.client.post(
            reverse("account_request_login_code"),
            data={"email": user.email},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertTrue(email_address.verified)

    def test_password_reset_marks_legacy_email_as_verified(self):
        user = get_user_model().objects.create_user(
            username="legacyreset",
            email="legacyreset@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )

        response = self.client.post(
            reverse("account_reset_password"),
            data={"email": user.email},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertTrue(email_address.verified)

    def test_signup_sends_confirmation_email_and_requires_verification(self):
        response = self.client.post(
            reverse("account_signup"),
            data={
                "username": "newwriter",
                "email": "newwriter@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("newwriter@example.com", mail.outbox[0].to)
        self.assertNotIn("_auth_user_id", self.client.session)

        email_address = EmailAddress.objects.get(email="newwriter@example.com")
        self.assertFalse(email_address.verified)

    def test_login_page_offers_email_sign_in_code_recovery(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email me a sign-in code")

    def test_password_reset_sends_email_to_verified_address(self):
        user = get_user_model().objects.create_user(
            username="recoverme",
            email="recoverme@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=True,
        )

        response = self.client.post(
            reverse("account_reset_password"),
            data={"email": user.email},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.email, mail.outbox[0].to)

    def test_password_change_sends_security_notification_email(self):
        user = get_user_model().objects.create_user(
            username="changeme",
            email="changeme@example.com",
            password="password123",
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=True,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("account_change_password"),
            data={
                "oldpassword": "password123",
                "password1": "NewPassword123!",
                "password2": "NewPassword123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(user.email in message.to for message in mail.outbox))
        self.assertTrue(any("password" in message.subject.lower() for message in mail.outbox))


class NavbarVisibilityTests(TestCase):
    def test_anonymous_users_do_not_see_projects_or_more_dropdowns(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Home")
        self.assertContains(response, "Sign in")
        self.assertNotContains(response, "<summary>Projects</summary>", html=False)
        self.assertNotContains(response, "<summary>More</summary>", html=False)


class LLMTests(TestCase):
    def test_call_llm_replaces_em_dash_and_uses_global_instruction(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Wait\u2014no. Use this\u2014instead."))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            result = call_llm(prompt="Test prompt", model_name="test-model", params={"temperature": 0.2, "max_tokens": 42})

        self.assertEqual(result.text, "Wait-no. Use this-instead.")
        self.assertEqual(
            result.usage,
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )
        kwargs = mocked.call_args.kwargs
        messages = kwargs["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual(kwargs["max_completion_tokens"], 42)
        self.assertNotIn("max_tokens", kwargs)

    def test_call_llm_accepts_explicit_max_completion_tokens(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Plain text."))],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            call_llm(
                prompt="Test prompt",
                model_name="test-model",
                params={"temperature": 0.2, "max_tokens": 42, "max_completion_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["max_completion_tokens"], 64)
        self.assertNotIn("max_tokens", kwargs)

    def test_call_llm_omits_temperature_for_gpt5_family_models(self):
        fake_response = SimpleNamespace(
            output_text="Plain text.",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response) as mocked:
            result = call_llm(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                params={"temperature": 0.4, "max_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["max_output_tokens"], 64)
        self.assertEqual(kwargs["instructions"], SYSTEM_PROMPT)
        self.assertEqual(kwargs["input"], "Test prompt")
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertEqual(result.text, "Plain text.")
        self.assertEqual(result.usage, {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13})

    def test_call_llm_keeps_temperature_for_4x_models(self):
        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Plain text."))],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4, total_tokens=13),
        )

        with patch("main.llm.client.chat.completions.create", return_value=fake_response) as mocked:
            call_llm(
                prompt="Test prompt",
                model_name="gpt-4.1-mini",
                params={"temperature": 0.4, "max_tokens": 64},
            )

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0.4)

    def test_call_llm_reads_responses_api_content_parts_when_output_text_missing(self):
        fake_response = SimpleNamespace(
            output_text="",
            output=[SimpleNamespace(content=[SimpleNamespace(text="Part one.")])],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response):
            result = call_llm(
                prompt="Test prompt",
                model_name="o4-mini",
                params={"max_tokens": 64},
            )

        self.assertEqual(result.text, "Part one.")

    def test_call_llm_reads_responses_api_text_value_objects(self):
        fake_response = SimpleNamespace(
            output_text=None,
            output=[SimpleNamespace(content=[SimpleNamespace(text=SimpleNamespace(value="Nested text."))])],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response):
            result = call_llm(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                params={"max_tokens": 64},
            )

        self.assertEqual(result.text, "Nested text.")

    def test_call_llm_reads_responses_api_dict_output_without_returning_text_type_marker(self):
        fake_response = SimpleNamespace(
            output_text="",
            text=SimpleNamespace(format=SimpleNamespace(type="text")),
            output=[
                {
                    "content": [
                        {"type": "output_text", "text": "Draft paragraph."},
                    ]
                }
            ],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )

        with patch("main.llm.client.responses.create", return_value=fake_response):
            result = call_llm(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                params={"max_tokens": 64},
            )

        self.assertEqual(result.text, "Draft paragraph.")

    def test_call_llm_ignores_gpt5_metadata_only_responses_and_falls_back(self):
        responses_response = SimpleNamespace(
            output_text="",
            text=SimpleNamespace(format=SimpleNamespace(type="text"), verbosity="medium"),
            reasoning=SimpleNamespace(effort="medium"),
            output=[SimpleNamespace(type="reasoning", content=None)],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )
        chat_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Fallback text."))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )

        with patch("main.llm.client.responses.create", return_value=responses_response):
            with patch("main.llm.client.chat.completions.create", return_value=chat_response):
                result = call_llm(
                    prompt="Test prompt",
                    model_name="gpt-5-mini",
                    params={"max_tokens": 64},
                )

        self.assertEqual(result.text, "Fallback text.")
        self.assertEqual(result.usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    def test_call_llm_falls_back_to_chat_completions_when_responses_text_is_empty(self):
        responses_response = SimpleNamespace(
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=6, output_tokens=3, total_tokens=9),
        )
        chat_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Fallback text."))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )

        with patch("main.llm.client.responses.create", return_value=responses_response) as responses_mock:
            with patch("main.llm.client.chat.completions.create", return_value=chat_response) as chat_mock:
                result = call_llm(
                    prompt="Test prompt",
                    model_name="gpt-5-mini",
                    params={"max_tokens": 64},
                )

        self.assertEqual(responses_mock.call_count, 1)
        self.assertEqual(chat_mock.call_count, 1)
        self.assertEqual(result.text, "Fallback text.")
        self.assertEqual(result.usage, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


@override_settings(
    STRIPE_PUBLISHABLE_KEY="pk_test_123",
    STRIPE_SECRET_KEY="sk_test_123",
    STRIPE_WEBHOOK_SECRET="whsec_test_123",
    STRIPE_PRICE_MONTHLY="price_monthly_123",
    STRIPE_PRICE_YEARLY="price_yearly_123",
    STRIPE_PRICE_SINGLE_MONTH="price_single_month_123",
    STRIPE_PRICE_TRIAL_WEEK="price_trial_week_123",
    STRIPE_BILLING_ENABLED=True,
)
class BillingTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Billing Project",
            slug="billing-project",
            target_word_count=1000,
            owner=self.user,
        )

    def test_billing_page_renders_subscription_controls(self):
        resp = self.client.get(reverse("billing"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Billing")
        self.assertContains(resp, "Choose Monthly subscription")
        self.assertContains(resp, "Choose Yearly subscription")
        self.assertContains(resp, "Choose One month pass")
        self.assertContains(resp, "Choose One week trial")
        self.assertNotContains(resp, "Clear current status")

    def test_ferdinand_superuser_sees_clear_status_option(self):
        self.user.username = "Ferdinand"
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["username", "is_superuser", "is_staff"])

        resp = self.client.get(reverse("billing"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Clear current status")

    def test_billing_page_lists_invoices(self):
        invoice = BillingInvoice.objects.create(
            user=self.user,
            invoice_number="INV-1001",
            status="paid",
            currency="GBP",
            description="Monthly subscription",
            subtotal_amount=1500,
            total_amount=1500,
            amount_paid=1500,
        )

        resp = self.client.get(reverse("billing"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invoices")
        self.assertContains(resp, "INV-1001")
        self.assertContains(resp, reverse("billing-invoice-pdf", kwargs={"pk": invoice.pk}))
        self.assertNotContains(resp, "Edit invoice")

    @patch("main.views.get_subscription_display")
    @patch("main.views.sync_checkout_session")
    def test_billing_page_syncs_successful_checkout_session(self, mock_sync_checkout, mock_get_subscription_display):
        mock_get_subscription_display.return_value = {
            "has_subscription": True,
            "is_active": True,
            "status": "active",
            "plan_label": "Monthly",
            "current_period_end": None,
            "cancel_at_period_end": False,
        }

        resp = self.client.get(reverse("billing") + "?checkout=success&session_id=cs_test_123")

        self.assertEqual(resp.status_code, 200)
        mock_sync_checkout.assert_called_once_with(user=self.user, session_id="cs_test_123")

    @patch("main.views.get_subscription_display")
    @patch("main.views.sync_checkout_session", side_effect=KeyError(0))
    def test_billing_page_shows_exception_type_for_sync_failure(self, mock_sync_checkout, mock_get_subscription_display):
        mock_get_subscription_display.return_value = {
            "has_subscription": False,
            "is_active": False,
            "status": "inactive",
            "plan_label": "",
            "current_period_end": None,
            "cancel_at_period_end": False,
        }

        resp = self.client.get(reverse("billing") + "?checkout=success&session_id=cs_test_123")

        self.assertEqual(resp.status_code, 200)
        mock_sync_checkout.assert_called_once_with(user=self.user, session_id="cs_test_123")
        self.assertContains(resp, "Error: KeyError: 0")

    @patch("main.views.get_subscription_display")
    @patch("main.views.sync_checkout_session")
    def test_billing_page_skips_placeholder_checkout_session(self, mock_sync_checkout, mock_get_subscription_display):
        mock_get_subscription_display.return_value = {
            "has_subscription": False,
            "is_active": False,
            "status": "inactive",
            "plan_label": "",
            "current_period_end": None,
            "cancel_at_period_end": False,
        }

        resp = self.client.get(reverse("billing") + "?checkout=success&session_id=%7BCHECKOUT_SESSION_ID%7D")

        self.assertEqual(resp.status_code, 200)
        mock_sync_checkout.assert_not_called()
        self.assertContains(resp, "Stripe did not return a usable session id in the redirect.")

    @patch("main.views.create_checkout_session")
    def test_checkout_redirects_to_stripe_checkout(self, mock_create_session):
        mock_create_session.return_value = SimpleNamespace(url="https://checkout.stripe.test/session_123")

        resp = self.client.post(reverse("billing-checkout"), data={"plan": "monthly"})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://checkout.stripe.test/session_123")
        self.assertEqual(mock_create_session.call_args.kwargs["option"]["key"], "monthly")
        self.assertEqual(mock_create_session.call_args.kwargs["option"]["price_id"], "price_monthly_123")
        self.assertEqual(mock_create_session.call_args.kwargs["user"], self.user)
        self.assertIn("checkout=success", mock_create_session.call_args.kwargs["success_url"])
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", mock_create_session.call_args.kwargs["success_url"])
        self.assertNotIn("%7BCHECKOUT_SESSION_ID%7D", mock_create_session.call_args.kwargs["success_url"])

    @patch("main.views.create_checkout_session")
    def test_one_time_checkout_redirects_to_stripe_checkout(self, mock_create_session):
        mock_create_session.return_value = SimpleNamespace(url="https://checkout.stripe.test/session_456")

        resp = self.client.post(reverse("billing-checkout"), data={"plan": "single_month"})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://checkout.stripe.test/session_456")
        self.assertEqual(mock_create_session.call_args.kwargs["option"]["key"], "single_month")
        self.assertEqual(mock_create_session.call_args.kwargs["option"]["price_id"], "price_single_month_123")

    @patch("main.views.create_billing_portal_session")
    def test_portal_redirects_to_stripe_portal(self, mock_create_portal):
        UserSubscription.objects.create(
            user=self.user,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            status="active",
        )
        mock_create_portal.return_value = SimpleNamespace(url="https://billing.stripe.test/session_123")

        resp = self.client.post(reverse("billing-portal"))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://billing.stripe.test/session_123")
        self.assertEqual(mock_create_portal.call_args.kwargs["user"], self.user)

    def test_invoice_pdf_download_returns_pdf(self):
        BillingCompanyProfile.objects.create(
            company_name="Example Books Ltd",
            company_email="accounts@example.com",
            company_address="1 Example Street",
            company_tax_id="GB123456",
        )
        invoice = BillingInvoice.objects.create(
            user=self.user,
            invoice_number="INV-2002",
            status="paid",
            currency="GBP",
            description="Yearly subscription",
            subtotal_amount=10000,
            total_amount=10000,
            amount_paid=10000,
        )

        resp = self.client.get(reverse("billing-invoice-pdf", kwargs={"pk": invoice.pk}))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("INV-2002.pdf", resp["Content-Disposition"])
        self.assertTrue(resp.content.startswith(b"%PDF-"))
        self.assertIn(b"Example Books Ltd", resp.content)
        self.assertIn(b"accounts@example.com", resp.content)

    def test_invoice_pdf_download_is_scoped_to_owner(self):
        other_user = get_user_model().objects.create_user(
            username="otherbillinguser",
            email="otherbillinguser@example.com",
            password="password123",
        )
        invoice = BillingInvoice.objects.create(
            user=other_user,
            invoice_number="INV-OTHER",
            status="paid",
            total_amount=500,
            amount_paid=500,
        )

        resp = self.client.get(reverse("billing-invoice-pdf", kwargs={"pk": invoice.pk}))

        self.assertEqual(resp.status_code, 404)

    def test_superuser_can_edit_company_details(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])

        resp = self.client.post(
            reverse("billing-company-details"),
            data={
                "company_name": "Example Books Ltd",
                "company_email": "billing@example.com",
                "company_address": "1 Admin Street",
                "company_tax_id": "GB123456",
            },
        )

        self.assertEqual(resp.status_code, 302)
        profile = BillingCompanyProfile.objects.get()
        self.assertEqual(profile.company_name, "Example Books Ltd")
        self.assertEqual(profile.company_email, "billing@example.com")
        self.assertEqual(profile.company_address, "1 Admin Street")
        self.assertEqual(profile.company_tax_id, "GB123456")

    def test_regular_user_cannot_edit_company_details(self):
        BillingCompanyProfile.objects.create()

        resp = self.client.get(reverse("billing-company-details"))

        self.assertEqual(resp.status_code, 403)

    def test_superuser_sees_company_details_link(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])

        resp = self.client.get(reverse("billing"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("billing-company-details"))

    def test_ferdinand_superuser_can_clear_billing_status(self):
        self.user.username = "Ferdinand"
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["username", "is_superuser", "is_staff"])
        UserSubscription.objects.create(
            user=self.user,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            stripe_product_id="prod_123",
            stripe_price_id="price_monthly_123",
            billing_interval="month",
            status="active",
            cancel_at_period_end=True,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timedelta(days=30),
            last_checkout_session_id="cs_123",
            raw_data={"status": "active"},
        )

        resp = self.client.post(reverse("billing-clear-status"))

        self.assertEqual(resp.status_code, 302)
        record = UserSubscription.objects.get(user=self.user)
        self.assertEqual(record.stripe_customer_id, "cus_123")
        self.assertEqual(record.stripe_subscription_id, "")
        self.assertEqual(record.stripe_price_id, "")
        self.assertEqual(record.status, "")
        self.assertFalse(record.cancel_at_period_end)
        self.assertIsNone(record.current_period_end)

    def test_non_ferdinand_superuser_cannot_clear_billing_status(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        UserSubscription.objects.create(
            user=self.user,
            stripe_subscription_id="sub_123",
            status="active",
        )

        resp = self.client.post(reverse("billing-clear-status"))

        self.assertEqual(resp.status_code, 403)
        record = UserSubscription.objects.get(user=self.user)
        self.assertEqual(record.stripe_subscription_id, "sub_123")
        self.assertEqual(record.status, "active")

    def test_generation_endpoint_requires_subscription_when_billing_enabled(self):
        resp = self.client.post(
            reverse("project-brainstorm", kwargs={"slug": self.project.slug}),
            data={},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(resp.status_code, 402)
        self.assertEqual(resp.json()["ok"], False)
        self.assertIn("subscription", resp.json()["error"].lower())
        self.assertIn(reverse("billing"), resp.json()["billing_url"])

    def test_dashboard_generation_redirects_to_billing_without_subscription(self):
        resp = self.client.post(
            reverse("project-dashboard", kwargs={"slug": self.project.slug}),
            data={"action": "generate_bible"},
        )

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("billing"), resp["Location"])

    @patch("main.billing.stripe.Subscription.retrieve")
    def test_checkout_webhook_syncs_subscription_record(self, mock_retrieve):
        mock_retrieve.return_value = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": 1735689600,
            "current_period_end": 1738368000,
            "trial_end": None,
            "items": {
                "data": [
                    {
                        "price": {
                            "id": "price_monthly_123",
                            "product": "prod_123",
                            "recurring": {"interval": "month"},
                        }
                    }
                ]
            },
        }

        created = process_webhook_event(
            {
                "id": "evt_123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_123",
                        "customer": "cus_123",
                        "subscription": "sub_123",
                        "client_reference_id": str(self.user.id),
                        "metadata": {"user_id": str(self.user.id)},
                    }
                },
            }
        )

        self.assertTrue(created)
        record = UserSubscription.objects.get(user=self.user)
        self.assertEqual(record.stripe_customer_id, "cus_123")
        self.assertEqual(record.stripe_subscription_id, "sub_123")
        self.assertEqual(record.stripe_price_id, "price_monthly_123")
        self.assertEqual(record.billing_interval, "month")
        self.assertEqual(record.status, "active")
        self.assertTrue(ProcessedStripeEvent.objects.filter(stripe_event_id="evt_123").exists())

    @patch("main.billing.stripe.Invoice.retrieve")
    @patch("main.billing.stripe.Subscription.retrieve")
    @patch("main.billing.stripe.checkout.Session.retrieve")
    def test_sync_checkout_session_creates_invoice_for_subscription_checkout(self, mock_session_retrieve, mock_sub_retrieve, mock_invoice_retrieve):
        mock_session_retrieve.return_value = {
            "id": "cs_sub_123",
            "customer": "cus_123",
            "subscription": "sub_123",
            "invoice": "in_123",
            "currency": "gbp",
            "created": 1735689600,
            "client_reference_id": str(self.user.id),
            "metadata": {
                "user_id": str(self.user.id),
                "plan_key": "monthly",
                "price_id": "price_monthly_123",
            },
        }
        mock_sub_retrieve.return_value = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": 1735689600,
            "current_period_end": 1738368000,
            "trial_end": None,
            "items": {"data": [{"price": {"id": "price_monthly_123", "product": "prod_123", "recurring": {"interval": "month"}}}]},
        }
        mock_invoice_retrieve.return_value = {
            "id": "in_123",
            "number": "INV-CHECKOUT-1",
            "subscription": "sub_123",
            "customer": "cus_123",
            "status": "paid",
            "currency": "gbp",
            "created": 1735689600,
            "subtotal": 1500,
            "tax": 0,
            "total": 1500,
            "amount_paid": 1500,
            "customer_details": {
                "name": "Test Buyer",
                "email": "tester@example.com",
            },
            "lines": {"data": [{"description": "Monthly subscription"}]},
        }

        record = sync_checkout_session(user=self.user, session_id="cs_sub_123")

        self.assertIsNotNone(record)
        invoice = BillingInvoice.objects.get(user=self.user, stripe_invoice_id="in_123")
        self.assertEqual(invoice.invoice_number, "INV-CHECKOUT-1")
        self.assertEqual(invoice.stripe_checkout_session_id, "cs_sub_123")
        self.assertEqual(invoice.total_amount, 1500)

    @patch("main.billing.stripe.Subscription.retrieve")
    def test_webhook_processing_is_idempotent(self, mock_retrieve):
        mock_retrieve.return_value = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": 1735689600,
            "current_period_end": 1738368000,
            "trial_end": None,
            "items": {"data": [{"price": {"id": "price_monthly_123", "product": "prod_123", "recurring": {"interval": "month"}}}]},
        }
        event = {
            "id": "evt_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_123",
                    "customer": "cus_123",
                    "subscription": "sub_123",
                    "client_reference_id": str(self.user.id),
                    "metadata": {"user_id": str(self.user.id)},
                }
            },
        }

        self.assertTrue(process_webhook_event(event))
        self.assertFalse(process_webhook_event(event))
        self.assertEqual(ProcessedStripeEvent.objects.filter(stripe_event_id="evt_123").count(), 1)

    @patch("main.billing.stripe.Subscription.retrieve")
    def test_invoice_webhook_creates_invoice_record(self, mock_retrieve):
        UserSubscription.objects.create(
            user=self.user,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            status="active",
        )
        mock_retrieve.return_value = {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_start": 1735689600,
            "current_period_end": 1738368000,
            "trial_end": None,
            "items": {"data": [{"price": {"id": "price_monthly_123", "product": "prod_123", "recurring": {"interval": "month"}}}]},
        }

        created = process_webhook_event(
            {
                "id": "evt_invoice_123",
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": "in_123",
                        "subscription": "sub_123",
                        "customer": "cus_123",
                        "number": "INV-STRIPE-1",
                        "status": "paid",
                        "currency": "gbp",
                        "created": 1735689600,
                        "subtotal": 1500,
                        "tax": 0,
                        "total": 1500,
                        "amount_paid": 1500,
                        "customer_details": {
                            "name": "Test Buyer",
                            "email": "tester@example.com",
                            "address": {"line1": "1 Test Street", "country": "GB"},
                        },
                        "lines": {"data": [{"description": "Monthly subscription"}]},
                    }
                },
            }
        )

        self.assertTrue(created)
        invoice = BillingInvoice.objects.get(user=self.user, stripe_invoice_id="in_123")
        self.assertEqual(invoice.invoice_number, "INV-STRIPE-1")
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.total_amount, 1500)
        self.assertEqual(invoice.description, "Monthly subscription")

    def test_sync_subscription_record_accepts_dict_shaped_items_data(self):
        record = sync_subscription_record(
            user=self.user,
            subscription={
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "cancel_at_period_end": False,
                "current_period_start": 1735689600,
                "current_period_end": 1738368000,
                "trial_end": None,
                "items": {
                    "data": {
                        "primary": {
                            "price": {
                                "id": "price_monthly_123",
                                "product": "prod_123",
                                "recurring": {"interval": "month"},
                            }
                        }
                    }
                },
            },
            checkout_session_id="cs_123",
        )

        self.assertEqual(record.stripe_customer_id, "cus_123")
        self.assertEqual(record.stripe_subscription_id, "sub_123")
        self.assertEqual(record.stripe_price_id, "price_monthly_123")
        self.assertEqual(record.billing_interval, "month")

    def test_sync_subscription_record_accepts_stripe_object(self):
        subscription = StripeObject.construct_from(
            {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "cancel_at_period_end": False,
                "current_period_start": 1735689600,
                "current_period_end": 1738368000,
                "trial_end": None,
                "items": {
                    "data": [
                        {
                            "price": {
                                "id": "price_monthly_123",
                                "product": "prod_123",
                                "recurring": {"interval": "month"},
                            }
                        }
                    ]
                },
            },
            "sk_test_123",
        )

        record = sync_subscription_record(
            user=self.user,
            subscription=subscription,
            checkout_session_id="cs_123",
        )

        self.assertEqual(record.stripe_customer_id, "cus_123")
        self.assertEqual(record.stripe_subscription_id, "sub_123")
        self.assertEqual(record.stripe_price_id, "price_monthly_123")
        self.assertEqual(record.billing_interval, "month")

    def test_payment_checkout_webhook_grants_timeboxed_access(self):
        created = process_webhook_event(
            {
                "id": "evt_payment_123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_payment_123",
                        "created": 1735689600,
                        "customer": "cus_123",
                        "payment_status": "paid",
                        "client_reference_id": str(self.user.id),
                        "metadata": {
                            "user_id": str(self.user.id),
                            "plan_key": "single_month",
                            "price_id": "price_single_month_123",
                            "checkout_mode": "payment",
                            "access_days": "30",
                        },
                    }
                },
            }
        )

        self.assertTrue(created)
        record = UserSubscription.objects.get(user=self.user)
        self.assertEqual(record.stripe_customer_id, "cus_123")
        self.assertEqual(record.stripe_subscription_id, "")
        self.assertEqual(record.stripe_price_id, "price_single_month_123")
        self.assertEqual(record.billing_interval, "month")
        self.assertEqual(record.status, "active")
        self.assertTrue(record.cancel_at_period_end)
        self.assertEqual(record.current_period_end - record.current_period_start, timedelta(days=30))
        self.assertTrue(record.is_active)
        invoice = BillingInvoice.objects.get(user=self.user, stripe_checkout_session_id="cs_payment_123")
        self.assertEqual(invoice.status, "paid")
        self.assertEqual(invoice.total_amount, 0)
        self.assertEqual(invoice.description, "One month pass")


class StoryBibleUploadTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Bible Project",
            slug="bible-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.bible = StoryBible.objects.create(
            project=self.project,
            summary_md="Core canon summary.",
        )
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

    @patch("main.views._extract_story_bible_pdf", return_value=("Canon import text.", 3))
    def test_upload_pdf_creates_story_bible_document(self, mock_extract):
        upload = SimpleUploadedFile("canon-reference.pdf", b"%PDF-1.4\n%stub", content_type="application/pdf")

        response = self.client.post(
            reverse("bible-edit", kwargs={"slug": self.project.slug}),
            data={"action": "upload_pdf", "pdf_file": upload},
        )

        self.assertEqual(response.status_code, 302)
        document = StoryBibleDocument.objects.get(story_bible=self.bible)
        self.assertEqual(document.original_name, "canon-reference.pdf")
        self.assertEqual(document.page_count, 3)
        self.assertEqual(document.extracted_text, "Canon import text.")
        self.assertEqual(document.extracted_text_chars, len("Canon import text."))
        self.assertTrue(document.file.name.endswith(".pdf"))
        mock_extract.assert_called_once()

    def test_story_bible_context_includes_uploaded_pdf_excerpt(self):
        StoryBibleDocument.objects.create(
            story_bible=self.bible,
            original_name="appendix.pdf",
            file_size=128,
            page_count=4,
            extracted_text="Important canon appendix text.",
            extracted_text_chars=len("Important canon appendix text."),
            file=SimpleUploadedFile("appendix.pdf", b"%PDF-1.4\n%stub", content_type="application/pdf"),
        )

        context = "\n".join(_get_story_bible_context(self.project))

        self.assertIn("Story bible summary: Core canon summary.", context)
        self.assertIn("Story bible PDF reference: appendix.pdf (4 pages)", context)
        self.assertIn("PDF excerpt: Important canon appendix text.", context)

    def test_story_bible_edit_page_can_delete_uploaded_pdf(self):
        document = StoryBibleDocument.objects.create(
            story_bible=self.bible,
            original_name="appendix.pdf",
            file_size=128,
            page_count=4,
            extracted_text="Important canon appendix text.",
            extracted_text_chars=len("Important canon appendix text."),
            file=SimpleUploadedFile("appendix.pdf", b"%PDF-1.4\n%stub", content_type="application/pdf"),
        )

        response = self.client.post(
            reverse("bible-document-delete", kwargs={"slug": self.project.slug, "pk": document.id}),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(StoryBibleDocument.objects.filter(id=document.id).exists())

    def test_story_bible_edit_page_can_read_uploaded_pdf(self):
        document = StoryBibleDocument.objects.create(
            story_bible=self.bible,
            original_name="appendix.pdf",
            file_size=128,
            page_count=4,
            extracted_text="Important canon appendix text.",
            extracted_text_chars=len("Important canon appendix text."),
            file=SimpleUploadedFile("appendix.pdf", b"%PDF-1.4\n%stub", content_type="application/pdf"),
        )

        response = self.client.get(
            reverse("bible-document-detail", kwargs={"slug": self.project.slug, "pk": document.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "appendix.pdf")
        self.assertContains(response, "Important canon appendix text.")

    def test_story_bible_edit_page_shows_download_link_for_uploaded_pdf(self):
        document = StoryBibleDocument.objects.create(
            story_bible=self.bible,
            original_name="appendix.pdf",
            file_size=128,
            page_count=4,
            extracted_text="Important canon appendix text.",
            extracted_text_chars=len("Important canon appendix text."),
            file=SimpleUploadedFile("appendix.pdf", b"%PDF-1.4\n%stub", content_type="application/pdf"),
        )

        response = self.client.get(reverse("bible-edit", kwargs={"slug": self.project.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download")
        self.assertContains(response, document.file.url)


class HomePageTests(TestCase):
    def test_home_page_displays_updates_board(self):
        HomeUpdate.objects.create(
            date="2026-03-20",
            title="Targeted scene regeneration",
            body="Added !{...}! markers and post-regenerate highlight support.",
        )

        resp = self.client.get(reverse("home"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Updates")
        self.assertContains(resp, "2026-03-20")
        self.assertContains(resp, "Targeted scene regeneration")
        self.assertContains(resp, "Added !{...}! markers and post-regenerate highlight support.")


class HomeUpdateCreateViewTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.regular_user = get_user_model().objects.create_user(
            username="writer",
            email="writer@example.com",
            password="password123",
        )

    def test_superuser_can_open_create_page_and_post_update(self):
        self.client.force_login(self.superuser)

        resp = self.client.post(
            reverse("home-update-create"),
            data={
                "title": "Added AI model selector",
                "date": "2026-03-20",
                "body": "Users can now switch between text generation models from the token usage page.",
            },
        )

        self.assertEqual(resp.status_code, 302)
        update = HomeUpdate.objects.get(title="Added AI model selector")
        self.assertEqual(update.body, "Users can now switch between text generation models from the token usage page.")

    def test_superuser_create_page_shows_title_date_and_body_text_fields(self):
        self.client.force_login(self.superuser)

        resp = self.client.get(reverse("home-update-create"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="title"', html=False)
        self.assertContains(resp, 'name="date"', html=False)
        self.assertContains(resp, 'name="body"', html=False)
        self.assertContains(resp, "Generate with AI")
        self.assertContains(resp, "AI can generate this from the body text")
        self.assertContains(resp, "Paste raw git or technical change notes here, then use Generate with AI.")

    def test_regular_user_cannot_open_create_page(self):
        self.client.force_login(self.regular_user)

        resp = self.client.get(reverse("home-update-create"))

        self.assertEqual(resp.status_code, 403)

    def test_superuser_can_generate_title_and_body_from_git_text(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"title":"Improved update composer","body":"The update composer now turns pasted git notes into a cleaner user-facing summary and short title."}',
                usage={"ok": True},
            ),
        ) as mock_call:
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "rework update composer to turn git text into user friendly explanation and summarized title",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Improved update composer",
                "body": "The update composer now turns pasted git notes into a cleaner user-facing summary and short title.",
            },
        )
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("turn raw git or technical change notes into a short user-facing update", prompt)
        self.assertIn("rework update composer to turn git text into user friendly explanation and summarized title", prompt)
        self.assertIn('Return STRICT JSON only in the form: {"title":"...","body":"..."}', prompt)
        self.assertIn("Never answer with placeholder words", prompt)

    def test_superuser_generate_home_update_accepts_body_only_model_response(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text="Users can now switch between text generation models from the token usage page, and the active model is shown in the navbar for quick reference.",
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "add model selector to token usage page and show active model in navbar",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Added AI model selector",
                "body": "Users can now switch between text generation models from the token usage page, and the active model is shown in the navbar for quick reference.",
            },
        )

    def test_superuser_generate_home_update_falls_back_when_model_output_is_unusable(self):
        self.client.force_login(self.superuser)

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"title":"Rework the Post Update composer to auto-generate and preview titles from the body","body":"Rework the Post Update composer to auto-generate and preview titles from the body."}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "rework the Post Update composer to auto-generate and preview titles from the body",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Reworked Post Update composer",
                "body": "Reworked Post Update composer to improve clarity and day-to-day usability.",
                "warning": "Model returned unusable output; used fallback generation.",
            },
        )

    def test_superuser_generate_home_update_falls_back_when_model_fails(self):
        self.client.force_login(self.superuser)

        with patch("main.views.call_llm", side_effect=ValueError("Model response was empty.")):
            resp = self.client.post(
                reverse("home-update-regenerate"),
                data={
                    "body": "fix dashboard overflow",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "title": "Fixed dashboard overflow",
                "body": "Fixed issues around dashboard overflow so the workflow behaves more reliably.",
                "warning": "Model response was empty.",
            },
        )

    def test_regular_user_cannot_regenerate_home_update_copy(self):
        self.client.force_login(self.regular_user)

        resp = self.client.post(
            reverse("home-update-regenerate"),
            data={"body": "technical commit text"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(resp.status_code, 403)


class MoveSceneTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter_a = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Chapter 1",
        )
        self.chapter_b = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="Chapter 2",
        )
        self.scene_1 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_a,
            order=1,
            title="Scene 1",
        )
        self.scene_2 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_a,
            order=2,
            title="Scene 2",
        )
        self.scene_b1 = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_b,
            order=1,
            title="Scene 3",
        )

    def test_move_scene_to_other_chapter_appends(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_1.id),
                "target_chapter_id": str(self.chapter_b.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_1.refresh_from_db()
        self.scene_2.refresh_from_db()
        self.scene_b1.refresh_from_db()

        self.assertEqual(self.scene_1.parent_id, self.chapter_b.id)
        self.assertEqual(self.scene_b1.order, 1)
        self.assertEqual(self.scene_1.order, 2)
        self.assertEqual(self.scene_2.order, 1)

    def test_move_scene_before_other_scene(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_2.id),
                "target_chapter_id": str(self.chapter_b.id),
                "before_scene_id": str(self.scene_b1.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_2.refresh_from_db()
        self.scene_b1.refresh_from_db()

        self.assertEqual(self.scene_2.parent_id, self.chapter_b.id)
        self.assertEqual(self.scene_2.order, 1)
        self.assertEqual(self.scene_b1.order, 2)

    def test_reorder_scene_within_chapter(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_2.id),
                "target_chapter_id": str(self.chapter_a.id),
                "before_scene_id": str(self.scene_1.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.scene_1.refresh_from_db()
        self.scene_2.refresh_from_db()

        self.assertEqual(self.scene_2.parent_id, self.chapter_a.id)
        self.assertEqual(self.scene_2.order, 1)
        self.assertEqual(self.scene_1.order, 2)

    def test_ajax_move_returns_json(self):
        url = reverse("scene-move", kwargs={"slug": self.project.slug})
        resp = self.client.post(
            url,
            data={
                "scene_id": str(self.scene_1.id),
                "target_chapter_id": str(self.chapter_b.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(resp.json(), {"ok": True})


class SceneStructurizeRenderTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Chapter 1",
            summary="Chapter summary.",
        )
        self.scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 1",
            summary="A tense meeting sets the stakes. A secret surfaces.",
            pov="Ava",
            location="Docking bay",
        )

    def test_edit_scene_uses_scene_outline_label(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Scene Outline")
        self.assertContains(resp, "Draft from Scene Outline")
        self.assertNotContains(resp, ">Summary<", html=False)
        self.assertContains(
            resp,
            'data-placeholder="Draft the scene in prose. For stronger generation results, make sure all relevant locations and characters have been created and added first."',
            html=False,
        )
        self.assertContains(
            resp,
            'placeholder="Refine or paste the final scene prose here once the draft is ready."',
            html=False,
        )

    def test_structurize_fills_structure_json(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.post(
            url,
            data={
                "order": 1,
                "title": self.scene.title,
                "summary": self.scene.summary,
                "pov": self.scene.pov,
                "location": self.scene.location,
                "action": "structurize",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertTrue(self.scene.structure_json.strip())

    def test_structurize_includes_selected_character_details_in_prompt(self):
        selected = Character.objects.create(
            project=self.project,
            name="Ava",
            role="Protagonist",
            age=22,
            gender="Female",
            personality="Driven and guarded.",
            appearance="Tall, watchful, practical.",
            background="Raised in cargo fleets.",
            goals="Expose the conspiracy.",
            voice_notes="Clipped, precise sentences.",
            description="Keeps emotional distance until pressured.",
            extra_fields={"secret": "Smuggling evidence in her jacket lining"},
        )
        Character.objects.create(
            project=self.project,
            name="Zed",
            role="Rival",
            personality="Provocative.",
        )

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Draft text.", usage={"ok": True})) as mock_call:
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "characters": [str(selected.id)],
                    "action": "structurize",
                },
            )

        self.assertEqual(resp.status_code, 302)
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Selected scene characters:", prompt)
        self.assertIn("- Ava: role=Protagonist; age=22; gender=Female", prompt)
        self.assertIn("personality=Driven and guarded.", prompt)
        self.assertIn("appearance=Tall, watchful, practical.", prompt)
        self.assertIn("background=Raised in cargo fleets.", prompt)
        self.assertIn("goals=Expose the conspiracy.", prompt)
        self.assertIn("voice_notes=Clipped, precise sentences.", prompt)
        self.assertIn("description=Keeps emotional distance until pressured.", prompt)
        self.assertIn("secret=Smuggling evidence in her jacket lining", prompt)
        self.assertNotIn("- Zed:", prompt)

    def test_structurize_includes_previous_scene_from_same_chapter_only(self):
        Location.objects.create(
            project=self.project,
            name="Docking bay",
            description="A noisy cargo threshold lit by warning strips and maintenance sparks.",
        )
        previous_scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 0",
            summary="A quiet argument reveals the central lie.",
            pov="Mira",
            location="Observation deck",
            rendered_text="Mira corners Ava on the observation deck and forces the first crack in the cover story.",
        )
        self.scene.order = 2
        self.scene.save(update_fields=["order"])

        other_chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="Chapter 2",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=other_chapter,
            order=1,
            title="Other chapter scene",
            summary="Should not leak into the prompt.",
        )

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Draft text.", usage={"ok": True})) as mock_call:
            resp = self.client.post(
                url,
                data={
                    "order": 2,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "action": "structurize",
                },
            )

        self.assertEqual(resp.status_code, 302)
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Selected location: Docking bay", prompt)
        self.assertIn(
            "Location description: A noisy cargo threshold lit by warning strips and maintenance sparks.",
            prompt,
        )
        self.assertIn("Previous scene in this chapter:", prompt)
        self.assertIn("Title: Scene 0", prompt)
        self.assertIn("POV: Mira", prompt)
        self.assertIn("Location: Observation deck", prompt)
        self.assertIn("Summary: A quiet argument reveals the central lie.", prompt)
        self.assertIn("Text for continuity: Mira corners Ava on the observation deck", prompt)
        self.assertNotIn("Other chapter scene", prompt)
        self.assertNotIn("Should not leak into the prompt.", prompt)

    def test_edit_scene_shows_current_chapter_scene_dropdown_links(self):
        sibling_scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=2,
            title="Scene 2",
            summary="A second scene in the same chapter.",
        )
        other_chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="Chapter 2",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=other_chapter,
            order=1,
            title="Other chapter scene",
            summary="Should not appear in the dropdown.",
        )

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Chapter scenes")
        self.assertContains(resp, reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id}))
        self.assertContains(resp, reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": sibling_scene.id}))
        self.assertContains(resp, "1. Scene 1")
        self.assertContains(resp, "2. Scene 2")
        self.assertNotContains(resp, "Other chapter scene")

    def test_render_uses_llm_when_available(self):
        self.scene.structure_json = (
            '{\n  "schema_version": 1,\n  "title": "Scene 1",\n  "summary": "x",\n  "pov": "Ava",\n  "location": "Docking bay",\n  "beats": []\n}'
        )
        self.scene.save(update_fields=["structure_json"])

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch("main.views.call_llm", return_value=LLMResult(text="Prose text.", usage={"ok": True})):
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "structure_json": self.scene.structure_json,
                    "rendered_text": "",
                    "action": "render",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertIn("Prose text.", self.scene.rendered_text)

    def test_regenerate_targeted_text_uses_full_draft_context(self):
        self.scene.structure_json = "Opening beat. !{Old line.}! Closing beat."
        self.scene.save(update_fields=["structure_json"])

        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"segments": ["New line."]}', usage={"ok": True}),
        ) as mock_call:
            resp = self.client.post(
                url,
                data={
                    "order": 1,
                    "title": self.scene.title,
                    "summary": self.scene.summary,
                    "pov": self.scene.pov,
                    "location": self.scene.location,
                    "structure_json": self.scene.structure_json,
                    "rendered_text": "",
                    "action": "reshuffle",
                },
            )

        self.assertEqual(resp.status_code, 302)
        self.scene.refresh_from_db()
        self.assertEqual(self.scene.structure_json, "Opening beat. New line. Closing beat.")
        self.assertIn("hl=", resp["Location"])
        prompt = mock_call.call_args.kwargs["prompt"]
        self.assertIn("Full draft with marked target sections:", prompt)
        self.assertIn("Opening beat. !{Old line.}! Closing beat.", prompt)

    def test_edit_scene_shows_regenerate_marker_buttons(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="draft-target-btn"')
        self.assertContains(resp, "!{...}!")
        self.assertContains(resp, 'id="draft-unbrace-btn"')
        self.assertContains(resp, "Remove {}")

    def test_edit_scene_shows_synonym_button_and_lookup_url(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="draft-synonym-btn"')
        self.assertContains(resp, 'aria-pressed="false"')
        self.assertContains(resp, reverse("scene-synonyms", kwargs={"slug": self.project.slug}))

    @patch("main.views.urlopen")
    def test_scene_synonyms_endpoint_filters_duplicate_and_identical_results(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            [
                {"word": "still"},
                {"word": "calm"},
                {"word": "Quiet"},
                {"word": "calm"},
                {"word": "peaceful"},
            ]
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        url = reverse("scene-synonyms", kwargs={"slug": self.project.slug})
        resp = self.client.get(url, {"word": "quiet"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "word": "quiet", "synonyms": ["still", "calm", "peaceful"]},
        )

    def test_scene_synonyms_endpoint_requires_word(self):
        url = reverse("scene-synonyms", kwargs={"slug": self.project.slug})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ok"], False)


class SceneLocationDropdownTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Test Project",
            slug="test-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Chapter 1",
        )
        self.scene = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter,
            order=1,
            title="Scene 1",
            summary="",
            location="Docking Bay",
        )
        self.root_location = Location.objects.create(project=self.project, name="Ship", description="", is_root=True)
        Location.objects.create(project=self.project, parent=self.root_location, name="Docking Bay", description="")
        Location.objects.create(project=self.project, parent=self.root_location, name="Garden", description="")

    def test_edit_scene_renders_location_select_with_create_option(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="location"')
        self.assertContains(resp, 'value="Docking Bay"')
        self.assertContains(resp, 'value="Garden"')
        self.assertContains(resp, 'value="__create__"')

    def test_posting_create_sentinel_redirects_to_location_creator(self):
        url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        resp = self.client.post(
            url,
            data={
                "order": 1,
                "title": "Scene 1",
                "summary": "",
                "pov": "",
                "location": "__create__",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("location-create", kwargs={"slug": self.project.slug}), resp["Location"])
        self.assertIn("next=", resp["Location"])

    def test_location_create_with_next_returns_to_scene_with_prefill(self):
        scene_url = reverse("outline-node-edit", kwargs={"slug": self.project.slug, "pk": self.scene.id})
        create_url = reverse("location-create", kwargs={"slug": self.project.slug}) + "?next=" + quote(scene_url, safe="")
        resp = self.client.post(
            create_url,
            data={"parent": str(self.root_location.id), "name": "Engine Room", "description": ""},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(scene_url))
        self.assertIn("prefill_location=Engine+Room", resp["Location"])


class CharacterViewsTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project_a = NovelProject.objects.create(
            title="Project A",
            slug="project-a",
            target_word_count=1000,
            owner=self.user,
        )
        self.project_b = NovelProject.objects.create(
            title="Project B",
            slug="project-b",
            target_word_count=1000,
            owner=self.user,
        )
        self.char_a1 = Character.objects.create(project=self.project_a, name="Ava", role="Protagonist", age=22, gender="Female")
        self.char_a2 = Character.objects.create(project=self.project_a, name="Zed", role="Antagonist")
        self.char_b1 = Character.objects.create(project=self.project_b, name="Bryn", role="Sidekick")

    def test_list_scoped_to_project(self):
        url = reverse("character-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Ava")
        self.assertContains(resp, "Zed")
        self.assertNotContains(resp, "Bryn")

    def test_search_filters(self):
        url = reverse("character-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url, {"q": "ava"})
        self.assertContains(resp, "Ava")
        self.assertNotContains(resp, "Zed")

    def test_edit_is_project_scoped(self):
        url = reverse("character-edit", kwargs={"slug": self.project_a.slug, "pk": self.char_b1.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    def test_brainstorm_returns_suggestions_for_empty_fields_only(self):
        url = reverse("character-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"age": 30, "gender": "Male", "name": "SHOULD_NOT_OVERWRITE"}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Ava",
                    "role": "",
                    "age": "",
                    "gender": "",
                    "personality": "",
                    "appearance": "",
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"age": 30, "gender": "Male"}})

    def test_add_details_does_not_return_name_and_can_enhance_fields(self):
        url = reverse("character-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"name": "NOPE", "personality": "Adds a subtle tell: taps her ring when lying."}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Ava",
                    "role": "Protagonist",
                    "age": "22",
                    "gender": "Female",
                    "personality": "Driven and guarded.",
                    "appearance": "",
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"personality": "Adds a subtle tell: taps her ring when lying."}})

    def test_add_details_strips_repeated_prefix_from_existing_field(self):
        url = reverse("character-add-details", kwargs={"slug": self.project_a.slug})
        existing = "tall and lean, with rugged features; short-cropped dark hair and deep-set blue eyes"
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text=json.dumps(
                    {
                        "appearance": (
                            existing
                            + "; often wears practical, worn work attire that reflects his hands-on job"
                        )
                    }
                ),
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Ava",
                    "role": "Protagonist",
                    "age": "22",
                    "gender": "Female",
                    "personality": "",
                    "appearance": existing,
                    "background": "",
                    "goals": "",
                    "voice_notes": "",
                    "description": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "suggestions": {
                    "appearance": "often wears practical, worn work attire that reflects his hands-on job"
                },
            },
        )


class ProjectSharedAccessTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.other_user = get_user_model().objects.create_user(
            username="project-owner",
            email="project-owner@example.com",
            password="password123",
        )
        self.project = NovelProject.objects.create(
            title="Shared Project",
            slug="shared-project",
            target_word_count=1000,
            owner=self.other_user,
        )

    def test_project_list_shows_projects_owned_by_other_users(self):
        url = reverse("project-list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shared Project")

    def test_project_detail_allows_shared_access(self):
        url = reverse("project-detail", kwargs={"slug": self.project.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shared Project")


class FullNovelViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project = NovelProject.objects.create(
            title="Full Novel Project",
            slug="full-novel-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.act = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.ACT,
            parent=None,
            order=1,
            title="Act I",
        )
        self.chapter_one = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=1,
            title="Arrival at Blackwater",
        )
        self.chapter_two = OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.CHAPTER,
            parent=self.act,
            order=2,
            title="The Terms of Escape",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_one,
            order=1,
            title="Scene 1",
            rendered_text="First chapter opening.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_one,
            order=2,
            title="Scene 2",
            rendered_text="First chapter closing.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_two,
            order=1,
            title="Scene 3",
            rendered_text="Second chapter opening.",
        )
        OutlineNode.objects.create(
            project=self.project,
            node_type=OutlineNode.NodeType.SCENE,
            parent=self.chapter_two,
            order=2,
            title="Scene 4",
            rendered_text="",
        )

    def test_full_novel_view_groups_rendered_text_under_chapter_titles(self):
        resp = self.client.get(reverse("full-novel", kwargs={"slug": self.project.slug}))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context["outline_tree"],
            [
                {
                    "act": {
                        "title": "Act I",
                        "anchor": f"act-{self.act.id}",
                    },
                    "chapters": [
                        {
                            "title": "Arrival at Blackwater",
                            "anchor": f"chapter-{self.chapter_one.id}",
                            "scenes": [
                                {
                                    "title": "Scene 1",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][0]["scenes"][0]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                                {
                                    "title": "Scene 2",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][0]["scenes"][1]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                            ],
                        },
                        {
                            "title": "The Terms of Escape",
                            "anchor": f"chapter-{self.chapter_two.id}",
                            "scenes": [
                                {
                                    "title": "Scene 3",
                                    "anchor": resp.context["outline_tree"][0]["chapters"][1]["scenes"][0]["anchor"],
                                    "pov": "",
                                    "location": "",
                                },
                                {
                                    "title": "Scene 4",
                                    "anchor": "",
                                    "pov": "",
                                    "location": "",
                                },
                            ],
                        },
                    ],
                }
            ],
        )
        self.assertEqual(
            resp.context["chapter_sections"],
            [
                {
                    "title": "Arrival at Blackwater",
                    "anchor": f"chapter-{self.chapter_one.id}",
                    "text": "First chapter opening.\n\nFirst chapter closing.",
                },
                {
                    "title": "The Terms of Escape",
                    "anchor": f"chapter-{self.chapter_two.id}",
                    "text": "Second chapter opening.",
                },
            ],
        )
        self.assertContains(resp, "Table of contents")
        self.assertContains(resp, "Act I")
        self.assertContains(resp, 'href="#act-', html=False)
        self.assertContains(resp, f'href="#chapter-{self.chapter_one.id}"', html=False)
        self.assertContains(resp, f'href="#chapter-{self.chapter_two.id}"', html=False)
        self.assertContains(resp, "Scene 1")
        self.assertContains(resp, "Scene 2")
        self.assertContains(resp, "Scene 3")
        self.assertContains(resp, "Scene 4")
        self.assertContains(resp, f'id="act-{self.act.id}"', html=False)
        self.assertContains(resp, f'id="chapter-{self.chapter_one.id}"', html=False)
        self.assertContains(resp, f'id="chapter-{self.chapter_two.id}"', html=False)
        self.assertContains(resp, 'id="scene-', html=False)
        self.assertContains(resp, "Arrival at Blackwater")
        self.assertContains(resp, "The Terms of Escape")
        self.assertContains(resp, "First chapter opening.")
        self.assertContains(resp, "First chapter closing.")
        self.assertContains(resp, "Second chapter opening.")


class ProjectArchiveTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.active_project = NovelProject.objects.create(
            title="Active Project",
            slug="active-project",
            target_word_count=1000,
            owner=self.user,
        )
        self.archived_project = NovelProject.objects.create(
            title="Archived Project",
            slug="archived-project",
            target_word_count=2000,
            owner=self.user,
            is_archived=True,
        )

    def test_project_list_excludes_archived_projects(self):
        resp = self.client.get(reverse("project-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Active Project")
        self.assertNotContains(resp, "Archived Project")
        self.assertContains(resp, "Archive")

    def test_archive_page_shows_only_archived_projects(self):
        resp = self.client.get(reverse("project-archive-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Archived Project")
        self.assertNotContains(resp, "Active Project")
        self.assertContains(resp, "Restore")

    def test_archive_project_marks_project_as_archived(self):
        resp = self.client.post(
            reverse("project-archive", kwargs={"slug": self.active_project.slug}),
            data={"next": reverse("project-list")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("project-list"))
        self.active_project.refresh_from_db()
        self.assertTrue(self.active_project.is_archived)

    def test_restore_project_marks_project_as_active(self):
        resp = self.client.post(
            reverse("project-restore", kwargs={"slug": self.archived_project.slug}),
            data={"next": reverse("project-archive-list")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("project-archive-list"))
        self.archived_project.refresh_from_db()
        self.assertFalse(self.archived_project.is_archived)


class TokenUsageViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.project_a = NovelProject.objects.create(
            title="Project A",
            slug="project-a",
            target_word_count=1000,
            owner=self.user,
        )
        self.project_b = NovelProject.objects.create(
            title="Project B",
            slug="project-b",
            target_word_count=1000,
            owner=self.user,
        )

    def _create_run(self, *, project, run_type, created_at, usage):
        run = GenerationRun.objects.create(
            project=project,
            run_type=run_type,
            status=GenerationRun.Status.SUCCEEDED,
            usage=usage,
        )
        GenerationRun.objects.filter(pk=run.pk).update(created_at=created_at, updated_at=created_at)
        return GenerationRun.objects.get(pk=run.pk)

    def test_token_usage_view_groups_daily_totals_and_project_totals(self):
        self._create_run(
            project=self.project_a,
            run_type=GenerationRun.RunType.BIBLE,
            created_at=timezone.make_aware(datetime(2026, 3, 24, 9, 0)),
            usage={"total_tokens": 120},
        )
        self._create_run(
            project=self.project_a,
            run_type=GenerationRun.RunType.SCENE,
            created_at=timezone.make_aware(datetime(2026, 3, 24, 10, 0)),
            usage={"prompt_tokens": 30, "completion_tokens": 45},
        )
        self._create_run(
            project=self.project_b,
            run_type=GenerationRun.RunType.BIBLE,
            created_at=timezone.make_aware(datetime(2026, 3, 25, 11, 30)),
            usage={"total_tokens": 200},
        )
        self._create_run(
            project=self.project_b,
            run_type=GenerationRun.RunType.OUTLINE,
            created_at=timezone.make_aware(datetime(2026, 3, 25, 13, 15)),
            usage={"generator": "local-template"},
        )

        resp = self.client.get(reverse("token-usage"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Token usage")
        self.assertContains(resp, "395")
        self.assertContains(resp, "Generate Bible")
        self.assertContains(resp, "Generate All Scenes")
        self.assertContains(resp, "Project A")
        self.assertContains(resp, "Project B")
        self.assertContains(resp, "195")
        self.assertContains(resp, "200")

    def test_project_brainstorm_records_token_usage_for_report(self):
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"genre": "Speculative mystery"}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ):
            resp = self.client.post(
                reverse("project-brainstorm", kwargs={"slug": self.project_a.slug}),
                data={
                    "seed_idea": "",
                    "genre": "",
                    "tone": "",
                    "style_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        usage_resp = self.client.get(reverse("token-usage"))
        self.assertEqual(usage_resp.status_code, 200)
        self.assertContains(usage_resp, "Project Brainstorm")
        self.assertContains(usage_resp, "55")
        self.assertContains(usage_resp, "Project A")

    def test_token_usage_page_saves_per_user_text_model_selection(self):
        resp = self.client.post(
            reverse("token-usage"),
            data={"text_model_name": "gpt-5-mini"},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        preference = UserTextModelPreference.objects.get(user=self.user)
        self.assertEqual(preference.text_model_name, "gpt-5-mini")
        self.assertContains(resp, "Current selection:")
        self.assertContains(resp, "gpt-5-mini")

    def test_project_brainstorm_uses_selected_user_text_model(self):
        UserTextModelPreference.objects.create(user=self.user, text_model_name="gpt-5-mini")

        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"genre": "Speculative mystery"}',
                usage={"prompt_tokens": 20, "completion_tokens": 35, "total_tokens": 55},
            ),
        ) as mock_call:
            resp = self.client.post(
                reverse("project-brainstorm", kwargs={"slug": self.project_a.slug}),
                data={
                    "seed_idea": "",
                    "genre": "",
                    "tone": "",
                    "style_notes": "",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_call.call_args.kwargs["model_name"], "gpt-5-mini")

    def test_navbar_shows_active_text_model_badge(self):
        UserTextModelPreference.objects.create(user=self.user, text_model_name="gpt-5-mini")

        resp = self.client.get(reverse("project-list"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Model")
        self.assertContains(resp, "gpt-5-mini")


class LocationViewsTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.other_user = get_user_model().objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        self.project_a = NovelProject.objects.create(title="Project A", slug="project-a", target_word_count=1000, owner=self.user)
        self.project_b = NovelProject.objects.create(title="Project B", slug="project-b", target_word_count=1000, owner=self.other_user)
        self.root_a = Location.objects.create(project=self.project_a, name="Ship", objects_map={}, is_root=True)
        self.loc_a = Location.objects.create(
            project=self.project_a,
            parent=self.root_a,
            name="Docking Bay",
            objects_map={"crate": "sealed"},
        )
        self.root_b = Location.objects.create(project=self.project_b, name="Estate", objects_map={}, is_root=True)
        self.loc_b = Location.objects.create(project=self.project_b, parent=self.root_b, name="Garden", objects_map={})

    def test_list_scoped_to_project(self):
        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Docking Bay")
        self.assertNotContains(resp, "Garden")

    def test_shared_access_allows_opening_other_users_location_pages(self):
        url = reverse("location-list", kwargs={"slug": self.project_b.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Garden")

    def test_create_parses_object_pairs(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "parent": str(self.root_a.id),
                "name": "Market",
                "description": "Busy and loud.",
                "object_key": ["stall", "lamp"],
                "object_value": ["fruit vendor", "flickering neon"],
            },
        )
        self.assertEqual(resp.status_code, 302)
        loc = Location.objects.get(project=self.project_a, name="Market")
        self.assertEqual(loc.parent_id, self.root_a.id)
        self.assertEqual(loc.objects_map, {"stall": "fruit vendor", "lamp": "flickering neon"})

    def test_create_form_defaults_parent_to_root(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'value="{self.root_a.id}" selected')

    def test_create_defaults_to_root_when_parent_is_not_selected(self):
        url = reverse("location-create", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "name": "Market",
                "description": "Busy and loud.",
                "object_key": [],
                "object_value": [],
            },
        )
        self.assertEqual(resp.status_code, 302)
        loc = Location.objects.get(project=self.project_a, name="Market")
        self.assertEqual(loc.parent_id, self.root_a.id)

    def test_list_renders_nested_path(self):
        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)
        self.assertContains(resp, "Locations")
        self.assertContains(resp, "Ship / Docking Bay")

    def test_list_renders_locations_as_nested_tree(self):
        nested = Location.objects.create(
            project=self.project_a,
            parent=self.loc_a,
            name="Maintenance Tunnel",
            objects_map={},
        )

        url = reverse("location-list", kwargs={"slug": self.project_a.slug})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "class=\"location-tree-list")
        self.assertContains(resp, "class=\"location-tree-children")
        self.assertContains(resp, "Maintenance Tunnel")
        self.assertContains(resp, f'href="{reverse("location-edit", kwargs={"slug": self.project_a.slug, "pk": nested.id})}"')
        self.assertContains(resp, "data-location-move-url")
        self.assertContains(resp, "Drag this location into another location")
        self.assertContains(resp, f'data-location-id="{nested.id}"')
        self.assertContains(resp, 'draggable="true"')

    def test_move_location_reparents_under_new_parent(self):
        market = Location.objects.create(project=self.project_a, parent=self.root_a, name="Market", objects_map={})
        url = reverse("location-move", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "location_id": str(market.id),
                "target_parent_id": str(self.loc_a.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        market.refresh_from_db()
        self.assertEqual(market.parent_id, self.loc_a.id)

    def test_move_location_rejects_moving_root(self):
        url = reverse("location-move", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={
                "location_id": str(self.root_a.id),
                "target_parent_id": str(self.loc_a.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ok"], False)

    def test_root_cannot_be_deleted(self):
        url = reverse("location-delete", kwargs={"slug": self.project_a.slug, "pk": self.root_a.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Location.objects.filter(id=self.root_a.id).exists())

    def test_brainstorm_location_description_only_when_empty(self):
        url = reverse("location-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch("main.views.call_llm") as mocked:
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "Already here.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {}})
        mocked.assert_not_called()

    def test_brainstorm_location_description_returns_suggestion(self):
        url = reverse("location-brainstorm", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "A cavernous bay of cold steel."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "suggestions": {"description": "A cavernous bay of cold steel."}},
        )

    def test_add_location_details_returns_suggestion(self):
        url = reverse("location-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "Overhead, warning lights stutter red."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "A cavernous bay of cold steel.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {"description": "Overhead, warning lights stutter red."}})

    def test_add_location_details_noop_when_duplicate(self):
        url = reverse("location-add-details", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(text='{"description": "Overhead, warning lights stutter red."}', usage={"ok": True}),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "Overhead, warning lights stutter red.",
                    "object_key": [],
                    "object_value": [],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "suggestions": {}})

    def test_extract_location_objects_requires_description(self):
        url = reverse("location-extract-objects", kwargs={"slug": self.project_a.slug})
        resp = self.client.post(
            url,
            data={"name": "Docking Bay", "description": "", "object_key": [], "object_value": []},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["ok"], False)

    def test_extract_location_objects_returns_new_objects_only(self):
        url = reverse("location-extract-objects", kwargs={"slug": self.project_a.slug})
        with patch(
            "main.views.call_llm",
            return_value=LLMResult(
                text='{"objects": {"crate": "sealed", "forklift": "rust-stained, idling near the bulkhead"}}',
                usage={"ok": True},
            ),
        ):
            resp = self.client.post(
                url,
                data={
                    "name": "Docking Bay",
                    "description": "A cavernous bay of cold steel. A sealed crate sits by the door.",
                    "object_key": ["crate"],
                    "object_value": ["sealed"],
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"ok": True, "objects": {"forklift": "rust-stained, idling near the bulkhead"}},
        )
