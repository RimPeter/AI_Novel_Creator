from __future__ import annotations

from typing import Any

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import ProcessedStripeEvent, UserSubscription


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def billing_enabled() -> bool:
    return bool(getattr(settings, "STRIPE_BILLING_ENABLED", False))


def get_price_options() -> list[dict[str, str]]:
    return [
        {
            "key": "monthly",
            "label": "Monthly",
            "interval": "month",
            "price_id": getattr(settings, "STRIPE_PRICE_MONTHLY", ""),
        },
        {
            "key": "yearly",
            "label": "Yearly",
            "interval": "year",
            "price_id": getattr(settings, "STRIPE_PRICE_YEARLY", ""),
        },
    ]


def get_subscription_record(user) -> UserSubscription | None:
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.subscription_record
    except UserSubscription.DoesNotExist:
        return None


def get_or_create_subscription_record(user) -> UserSubscription:
    record = get_subscription_record(user)
    if record is not None:
        return record
    return UserSubscription.objects.create(user=user)


def user_has_active_subscription(user) -> bool:
    record = get_subscription_record(user)
    return bool(record and record.is_active)


def get_subscription_display(user) -> dict[str, Any]:
    record = get_subscription_record(user)
    if record is None:
        return {
            "has_subscription": False,
            "is_active": False,
            "status": "",
            "plan_label": "",
            "current_period_end": None,
            "cancel_at_period_end": False,
        }

    interval = (record.billing_interval or "").strip().lower()
    plan_label = "Monthly" if interval == "month" else "Yearly" if interval == "year" else ""
    return {
        "has_subscription": bool(record.stripe_subscription_id or record.stripe_customer_id),
        "is_active": record.is_active,
        "status": record.status,
        "plan_label": plan_label,
        "current_period_end": record.current_period_end,
        "cancel_at_period_end": record.cancel_at_period_end,
    }


def _set_stripe_api_key() -> None:
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")


def _as_dict(value) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _get_nested_value(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _get_first_price(subscription: dict[str, Any]) -> dict[str, Any]:
    items = (_get_nested_value(subscription, "items", {}) or {}).get("data", [])
    if not items:
        return {}
    price = _get_nested_value(items[0], "price", {}) or {}
    return _as_dict(price)


def _timestamp_to_datetime(value) -> timezone.datetime | None:
    if not value:
        return None
    try:
        return timezone.datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return None


def ensure_stripe_customer(user) -> tuple[UserSubscription, str]:
    _set_stripe_api_key()
    record = get_or_create_subscription_record(user)
    if record.stripe_customer_id:
        return record, record.stripe_customer_id

    customer = stripe.Customer.create(
        email=(getattr(user, "email", "") or "").strip() or None,
        name=(getattr(user, "get_username", lambda: "")() or "").strip() or None,
        metadata={"user_id": str(user.id)},
    )
    record.stripe_customer_id = customer.id
    record.save(update_fields=["stripe_customer_id", "updated_at"])
    return record, customer.id


def create_checkout_session(*, user, price_id: str, success_url: str, cancel_url: str):
    _set_stripe_api_key()
    record, customer_id = ensure_stripe_customer(user)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=str(user.id),
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user.id), "price_id": price_id},
        subscription_data={"metadata": {"user_id": str(user.id)}},
    )
    record.last_checkout_session_id = session.id
    record.save(update_fields=["last_checkout_session_id", "updated_at"])
    return session


def sync_checkout_session(*, user, session_id: str) -> UserSubscription | None:
    session_id = str(session_id or "").strip()
    if not session_id:
        return None

    _set_stripe_api_key()
    session = stripe.checkout.Session.retrieve(session_id)
    session_dict = _as_dict(session)
    customer_id = str(session_dict.get("customer") or "").strip()
    metadata = session_dict.get("metadata") or {}

    resolved_user = _resolve_user(customer_id=customer_id, metadata=metadata)
    if resolved_user is None:
        client_reference_id = str(session_dict.get("client_reference_id") or "").strip()
        if client_reference_id:
            try:
                resolved_user = get_user_model().objects.get(pk=client_reference_id)
            except Exception:
                resolved_user = None

    if resolved_user is None or resolved_user.pk != user.pk:
        return None

    record = sync_customer_only_record(
        user=user,
        customer_id=customer_id,
        checkout_session_id=str(session_dict.get("id") or "").strip(),
    )
    subscription_id = str(session_dict.get("subscription") or "").strip()
    if not subscription_id:
        return record

    subscription = stripe.Subscription.retrieve(subscription_id)
    return sync_subscription_record(
        user=user,
        subscription=subscription,
        checkout_session_id=str(session_dict.get("id") or "").strip(),
    )


def create_billing_portal_session(*, user, return_url: str):
    _set_stripe_api_key()
    record = get_or_create_subscription_record(user)
    if not record.stripe_customer_id:
        _, customer_id = ensure_stripe_customer(user)
        record.stripe_customer_id = customer_id
    session = stripe.billing_portal.Session.create(
        customer=record.stripe_customer_id,
        return_url=return_url,
    )
    return session


def construct_webhook_event(*, payload: bytes, signature: str):
    return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)


def _resolve_user(*, customer_id: str = "", metadata: dict[str, Any] | None = None):
    metadata = metadata or {}
    user_model = get_user_model()
    user_id = str(metadata.get("user_id") or "").strip()
    if user_id:
        try:
            return user_model.objects.get(pk=user_id)
        except Exception:
            pass
    if customer_id:
        record = UserSubscription.objects.select_related("user").filter(stripe_customer_id=customer_id).first()
        if record is not None:
            return record.user
    return None


def sync_subscription_record(*, user, subscription: dict[str, Any], checkout_session_id: str = "") -> UserSubscription:
    record = get_or_create_subscription_record(user)
    subscription_dict = _as_dict(subscription)
    price = _get_first_price(subscription_dict)
    product_id = str(price.get("product") or "").strip()
    recurring = price.get("recurring") or {}
    record.stripe_customer_id = str(subscription_dict.get("customer") or record.stripe_customer_id or "").strip()
    record.stripe_subscription_id = str(subscription_dict.get("id") or "").strip()
    record.stripe_price_id = str(price.get("id") or "").strip()
    record.stripe_product_id = product_id
    record.billing_interval = str(recurring.get("interval") or "").strip()
    record.status = str(subscription_dict.get("status") or "").strip()
    record.cancel_at_period_end = bool(subscription_dict.get("cancel_at_period_end"))
    record.current_period_start = _timestamp_to_datetime(subscription_dict.get("current_period_start"))
    record.current_period_end = _timestamp_to_datetime(subscription_dict.get("current_period_end"))
    record.trial_end = _timestamp_to_datetime(subscription_dict.get("trial_end"))
    if checkout_session_id:
        record.last_checkout_session_id = checkout_session_id
    record.raw_data = subscription_dict
    record.save()
    return record


def sync_customer_only_record(*, user, customer_id: str, checkout_session_id: str = "") -> UserSubscription:
    record = get_or_create_subscription_record(user)
    record.stripe_customer_id = customer_id
    if checkout_session_id:
        record.last_checkout_session_id = checkout_session_id
    record.save(update_fields=["stripe_customer_id", "last_checkout_session_id", "updated_at"])
    return record


def handle_checkout_session_completed(session: dict[str, Any]) -> None:
    _set_stripe_api_key()
    session_dict = _as_dict(session)
    customer_id = str(session_dict.get("customer") or "").strip()
    metadata = session_dict.get("metadata") or {}
    user = _resolve_user(customer_id=customer_id, metadata=metadata)
    if user is None:
        client_reference_id = str(session_dict.get("client_reference_id") or "").strip()
        if client_reference_id:
            try:
                user = get_user_model().objects.get(pk=client_reference_id)
            except Exception:
                user = None
    if user is None:
        return

    sync_customer_only_record(
        user=user,
        customer_id=customer_id,
        checkout_session_id=str(session_dict.get("id") or "").strip(),
    )
    subscription_id = str(session_dict.get("subscription") or "").strip()
    if not subscription_id:
        return
    subscription = stripe.Subscription.retrieve(subscription_id)
    sync_subscription_record(user=user, subscription=subscription, checkout_session_id=str(session_dict.get("id") or "").strip())


def handle_subscription_event(subscription: dict[str, Any]) -> None:
    subscription_dict = _as_dict(subscription)
    customer_id = str(subscription_dict.get("customer") or "").strip()
    metadata = subscription_dict.get("metadata") or {}
    user = _resolve_user(customer_id=customer_id, metadata=metadata)
    if user is None:
        return
    sync_subscription_record(user=user, subscription=subscription_dict)


def handle_invoice_event(invoice: dict[str, Any]) -> None:
    _set_stripe_api_key()
    invoice_dict = _as_dict(invoice)
    subscription_id = str(invoice_dict.get("subscription") or "").strip()
    customer_id = str(invoice_dict.get("customer") or "").strip()
    if not subscription_id:
        return
    user = _resolve_user(customer_id=customer_id, metadata=invoice_dict.get("metadata") or {})
    if user is None:
        return
    subscription = stripe.Subscription.retrieve(subscription_id)
    sync_subscription_record(user=user, subscription=subscription)


def process_webhook_event(event) -> bool:
    event_dict = _as_dict(event)
    stripe_event_id = str(event_dict.get("id") or "").strip()
    if not stripe_event_id:
        return False
    if ProcessedStripeEvent.objects.filter(stripe_event_id=stripe_event_id).exists():
        return False

    event_type = str(event_dict.get("type") or "").strip()
    data_object = ((event_dict.get("data") or {}).get("object")) or {}

    if event_type == "checkout.session.completed":
        handle_checkout_session_completed(data_object)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        handle_subscription_event(data_object)
    elif event_type in {"invoice.paid", "invoice.payment_failed"}:
        handle_invoice_event(data_object)

    ProcessedStripeEvent.objects.create(
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        payload=event_dict,
    )
    return True
