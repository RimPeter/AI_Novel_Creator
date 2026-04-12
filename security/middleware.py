import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)


def _client_identifier(request) -> str:
    if getattr(request.user, "is_authenticated", False):
        return f"user:{request.user.pk}"
    forwarded_for = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "").strip()
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else str(request.META.get("REMOTE_ADDR", "") or "").strip()
    return f"ip:{ip_address or 'unknown'}"


def _limit_rule(url_name: str) -> tuple[int, int] | None:
    default_rules = {
        "account_login": (10, 900),
        "account_request_login_code": (8, 900),
        "account_reset_password": (8, 900),
        "contact": (20, 3600),
        "billing-checkout": (20, 3600),
        "billing-portal": (30, 3600),
        "billing-cancel-recurring": (12, 3600),
        "billing-clear-status": (12, 3600),
        "billing-webhook": (120, 60),
    }
    configured = getattr(settings, "SECURITY_RATE_LIMIT_RULES", None)
    rules = configured if isinstance(configured, dict) else default_rules
    rule = rules.get(url_name)
    if not rule or len(rule) != 2:
        return None
    return int(rule[0]), int(rule[1])


def _increment(key: str, window_seconds: int) -> int:
    current_count = cache.get(key)
    if current_count is None:
        cache.set(key, 1, timeout=window_seconds)
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        updated_count = int(current_count) + 1
        cache.set(key, updated_count, timeout=window_seconds)
        return updated_count


class SecurityRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self._monitor_404(request, response)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(settings, "RUNNING_TESTS", False):
            return None

        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", "") or ""
        rule = _limit_rule(url_name)
        if not rule:
            return None

        limit, window_seconds = rule
        actor = _client_identifier(request)
        counter_key = f"rate-limit:{url_name}:{actor}"
        count = _increment(counter_key, window_seconds)

        if url_name == "contact" and request.method.upper() == "POST":
            threshold = int(getattr(settings, "CONTACT_SPIKE_ALERT_THRESHOLD", 100))
            if count == threshold:
                logger.error("ALERT: Contact submission spike detected for %s", actor)

        if count <= limit:
            return None

        logger.warning("Rate limit exceeded for %s (%s): %s/%s", url_name, actor, count, limit)
        wants_json = "application/json" in str(request.headers.get("accept", "") or "") or request.headers.get("x-requested-with") == "XMLHttpRequest"
        if wants_json or url_name == "billing-webhook":
            return JsonResponse({"ok": False, "error": "Too many requests. Try again later."}, status=429)
        return HttpResponse("Too many requests. Try again later.", status=429)

    def _monitor_404(self, request, response: Any):
        if getattr(response, "status_code", 200) != 404:
            return response

        if not request.path.startswith("/projects/"):
            return response

        actor = _client_identifier(request)
        key = f"security:projects-404:{actor}"
        count = _increment(key, 600)
        threshold = int(getattr(settings, "PROJECT_404_ALERT_THRESHOLD", 25))
        if count == threshold:
            logger.error("ALERT: Repeated /projects/ 404 responses for %s", actor)

        return response
