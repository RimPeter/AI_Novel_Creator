import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from main.billing import billing_enabled, construct_webhook_event, process_webhook_event

from .forms import IssueContactForm, RequestContactForm

logger = logging.getLogger(__name__)


def _log_exception(message: str, *args) -> None:
    logger.error(message, *args, exc_info=not getattr(settings, "RUNNING_TESTS", False))


def _increment_counter(key: str, window_seconds: int) -> int:
    current_count = cache.get(key)
    if current_count is None:
        cache.set(key, 1, timeout=window_seconds)
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        updated = int(current_count) + 1
        cache.set(key, updated, timeout=window_seconds)
        return updated


class ContactView(TemplateView):
    template_name = "security/contact.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["issue_form"] = kwargs.get("issue_form") or IssueContactForm()
        ctx["request_form"] = kwargs.get("request_form") or RequestContactForm()
        return ctx

    def _get_contact_header_lines(self):
        user = self.request.user
        name = str(user.get_username() or "").strip() if getattr(user, "is_authenticated", False) else "anonymous"
        email = str(getattr(user, "email", "") or "").strip() if getattr(user, "is_authenticated", False) else ""

        body_lines = [
            f"Name: {name}",
            f"Email: {email}",
        ]
        if getattr(user, "is_authenticated", False):
            body_lines.extend(
                [
                    f"Username: {user.get_username()}",
                    f"User ID: {user.pk}",
                ]
            )
        else:
            body_lines.append("Username: anonymous")
        return body_lines, email

    def _send_issue_email(self, form):
        issue_subject = form.cleaned_data["issue_subject"]
        issue_message = form.cleaned_data["issue_message"]
        body_lines, email = self._get_contact_header_lines()
        body_lines.extend(
            [
                "",
                "Message:",
                f"Subject: {issue_subject or '(none)'}",
                issue_message or "(none)",
            ]
        )

        EmailMessage(
            subject=f"[{settings.SITE_NAME}] Contact: {issue_subject[:120]}",
            body="\n".join(body_lines).strip(),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.CONTACT_EMAIL],
            reply_to=[email] if email else [],
        ).send(fail_silently=False)

    def _send_request_email(self, form):
        request_want = form.cleaned_data["request_want"]
        request_benefit = form.cleaned_data["request_benefit"]
        additional_notes = form.cleaned_data["additional_notes"]
        body_lines, email = self._get_contact_header_lines()
        body_lines.extend(
            [
                "",
                "Request:",
                f"As a user I want: {request_want or '(none)'}",
                f"So I can: {request_benefit or '(none)'}",
                "",
                "Additional notes:",
                additional_notes or "(none)",
            ]
        )

        EmailMessage(
            subject=f"[{settings.SITE_NAME}] Contact: {request_want[:120]}",
            body="\n".join(body_lines).strip(),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.CONTACT_EMAIL],
            reply_to=[email] if email else [],
        ).send(fail_silently=False)

    def post(self, request, *args, **kwargs):
        form_type = str(request.POST.get("form_type") or "").strip().lower()

        if form_type == "issue":
            issue_form = IssueContactForm(request.POST)
            request_form = RequestContactForm()
            if issue_form.is_valid():
                self._send_issue_email(issue_form)
                messages.success(request, "Your message was sent to the admin.")
                return HttpResponseRedirect(self.get_success_url())
            return self.render_to_response(self.get_context_data(issue_form=issue_form, request_form=request_form))

        if form_type == "request":
            issue_form = IssueContactForm()
            request_form = RequestContactForm(request.POST)
            if request_form.is_valid():
                self._send_request_email(request_form)
                messages.success(request, "Your request was sent to the admin.")
                return HttpResponseRedirect(self.get_success_url())
            return self.render_to_response(self.get_context_data(issue_form=issue_form, request_form=request_form))

        messages.error(request, "Choose a message or request form before sending.")
        return self.render_to_response(self.get_context_data())

    def get_success_url(self):
        return reverse("contact")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not billing_enabled():
        return JsonResponse({"ok": False, "error": "Stripe billing is not configured."}, status=404)

    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = construct_webhook_event(payload=request.body, signature=signature)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)
    except stripe.error.SignatureVerificationError:
        counter = _increment_counter("security:webhook-signature-failed", 600)
        threshold = int(getattr(settings, "WEBHOOK_SIGNATURE_ALERT_THRESHOLD", 20))
        if counter == threshold:
            logger.error("ALERT: Repeated Stripe webhook signature failures.")
        return JsonResponse({"ok": False, "error": "Invalid signature."}, status=400)

    try:
        process_webhook_event(event)
    except Exception:
        _log_exception("Failed to process Stripe webhook event.")
        return JsonResponse({"ok": False, "error": "Webhook processing failed."}, status=500)
    return JsonResponse({"ok": True})
