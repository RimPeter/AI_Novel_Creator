from django.core.cache import cache
from django.http import HttpResponse
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import ResolverMatch

from .middleware import SecurityRateLimitMiddleware


@override_settings(
    RUNNING_TESTS=False,
    SECURITY_RATE_LIMIT_RULES={"account_request_login_code": (8, 900)},
    ACCOUNT_LOGIN_CODE_EMAIL_LIMIT=3,
    ACCOUNT_LOGIN_CODE_EMAIL_WINDOW_SECONDS=900,
)
class LoginCodeRateLimitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.middleware = SecurityRateLimitMiddleware(lambda request: HttpResponse())

    def tearDown(self):
        cache.clear()

    def request_login_code(self, email, ip_address):
        request = self.factory.post(
            "/accounts/login/code/",
            {"email": email},
            REMOTE_ADDR=ip_address,
        )
        request.user = AnonymousUser()
        request.resolver_match = ResolverMatch(
            func=lambda req: None,
            args=(),
            kwargs={},
            url_name="account_request_login_code",
        )
        return self.middleware.process_view(request, lambda req: None, (), {})

    def test_login_code_requests_are_limited_per_email_across_ips(self):
        for index in range(3):
            response = self.request_login_code(
                "writer@example.com",
                f"192.0.2.{index + 1}",
            )
            self.assertIsNone(response)

        response = self.request_login_code("writer@example.com", "192.0.2.4")

        self.assertEqual(response.status_code, 429)

    def test_email_limit_ignores_case_and_surrounding_whitespace(self):
        for index, email in enumerate(
            ["writer@example.com", "WRITER@example.com ", " writer@example.com"]
        ):
            response = self.request_login_code(email, f"192.0.2.{index + 1}")
            self.assertIsNone(response)

        response = self.request_login_code("Writer@example.com", "192.0.2.4")

        self.assertEqual(response.status_code, 429)

    def test_limit_for_one_email_does_not_block_another(self):
        for index in range(3):
            self.request_login_code("writer@example.com", f"192.0.2.{index + 1}")

        response = self.request_login_code("another@example.com", "192.0.2.4")

        self.assertIsNone(response)
