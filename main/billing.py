from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import BillingCompanyProfile, BillingInvoice, ProcessedStripeEvent, UserSubscription


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
VAT_RATE_PERCENT = Decimal("20")
VAT_MULTIPLIER = Decimal("1.20")
BILLING_METADATA_PREFIX = "billing_"


def billing_enabled() -> bool:
    return bool(getattr(settings, "STRIPE_BILLING_ENABLED", False))


def _vat_breakdown_from_gross_minor(gross_minor: int) -> tuple[int, int]:
    gross = Decimal(int(gross_minor or 0))
    ex_vat = int((gross / VAT_MULTIPLIER).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    vat_amount = int(gross) - ex_vat
    return ex_vat, vat_amount


def get_price_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = [
        {
            "key": "monthly",
            "label": "Monthly subscription",
            "price_display": "£15 / month",
            "interval": "month",
            "checkout_mode": "subscription",
            "description": "Recurring access billed every month.",
            "gross_minor_amount": "1500",
            "price_id": getattr(settings, "STRIPE_PRICE_MONTHLY", ""),
        },
        {
            "key": "yearly",
            "label": "Yearly subscription",
            "price_display": "£100 / year",
            "interval": "year",
            "checkout_mode": "subscription",
            "description": "Recurring access billed once per year.",
            "gross_minor_amount": "10000",
            "price_id": getattr(settings, "STRIPE_PRICE_YEARLY", ""),
        },
        {
            "key": "single_month",
            "label": "One month pass",
            "price_display": "£20 one-off",
            "interval": "month",
            "checkout_mode": "payment",
            "description": "Single payment for 30 days of access.",
            "access_days": "30",
            "gross_minor_amount": "2000",
            "price_id": getattr(settings, "STRIPE_PRICE_SINGLE_MONTH", ""),
        },
        {
            "key": "trial_week",
            "label": "One week trial",
            "price_display": "£5 one-off",
            "interval": "week",
            "checkout_mode": "payment",
            "description": "Single payment for 7 days of access.",
            "access_days": "7",
            "gross_minor_amount": "500",
            "price_id": getattr(settings, "STRIPE_PRICE_TRIAL_WEEK", ""),
        },
    ]
    for option in options:
        gross_minor = int(option.get("gross_minor_amount") or "0")
        ex_vat_minor, vat_minor = _vat_breakdown_from_gross_minor(gross_minor)
        option["vat_badge"] = f"{int(VAT_RATE_PERCENT)}% VAT included"
        option["price_breakdown_display"] = (
            f"inc VAT {format_minor_amount(gross_minor)} "
            f"(ex VAT {format_minor_amount(ex_vat_minor)} + VAT {format_minor_amount(vat_minor)} ({int(VAT_RATE_PERCENT)}%))"
        )
    return options


def get_price_option(plan_key: str) -> dict[str, str]:
    plan_key = str(plan_key or "").strip().lower()
    for option in get_price_options():
        if option["key"] == plan_key:
            return option
    return {}


def get_price_option_by_price_id(price_id: str) -> dict[str, str]:
    price_id = str(price_id or "").strip()
    for option in get_price_options():
        if option["price_id"] == price_id:
            return option
    return {}


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


def clear_subscription_status(user) -> UserSubscription | None:
    record = get_subscription_record(user)
    if record is None:
        return None
    record.stripe_subscription_id = ""
    record.stripe_product_id = ""
    record.stripe_price_id = ""
    record.billing_interval = ""
    record.status = ""
    record.cancel_at_period_end = False
    record.current_period_start = None
    record.current_period_end = None
    record.trial_end = None
    record.last_checkout_session_id = ""
    record.raw_data = {}
    record.save()
    return record


def user_has_active_subscription(user) -> bool:
    record = get_subscription_record(user)
    return bool(record and record.is_active)


def user_has_active_plan(user) -> bool:
    record = get_subscription_record(user)
    if record is None:
        return False
    return bool(record.is_active)


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

    option = get_price_option_by_price_id(record.stripe_price_id)
    interval = (record.billing_interval or "").strip().lower()
    plan_label = option.get("label", "")
    if not plan_label:
        plan_label = "Monthly subscription" if interval == "month" else "Yearly subscription" if interval == "year" else ""
    return {
        "has_subscription": bool(record.stripe_subscription_id),
        "is_active": record.is_active,
        "status": record.status,
        "plan_label": plan_label,
        "current_period_end": record.current_period_end,
        "cancel_at_period_end": record.cancel_at_period_end,
    }


def format_minor_amount(amount: int, currency: str = "GBP") -> str:
    amount = int(amount or 0)
    currency_code = str(currency or "GBP").upper()
    sign = "-" if amount < 0 else ""
    major = abs(amount) / 100
    return f"{sign}{currency_code} {major:.2f}"


def get_billing_invoices(user):
    if not getattr(user, "is_authenticated", False):
        return BillingInvoice.objects.none()
    return BillingInvoice.objects.filter(user=user).select_related("subscription_record")


def get_billing_company_profile() -> BillingCompanyProfile:
    profile = BillingCompanyProfile.objects.first()
    if profile is not None:
        return profile
    return BillingCompanyProfile.objects.create()


def get_billing_invoice_displays(user) -> list[dict[str, Any]]:
    invoices = []
    for invoice in get_billing_invoices(user):
        invoices.append(
            {
                "object": invoice,
                "number": invoice.public_number,
                "status": invoice.status or "draft",
                "issue_date": invoice.issue_date,
                "total_display": format_minor_amount(invoice.total_amount, invoice.currency),
                "paid_display": format_minor_amount(invoice.amount_paid, invoice.currency),
                "due_display": format_minor_amount(invoice.amount_due, invoice.currency),
            }
        )
    return invoices


def _set_stripe_api_key() -> None:
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")


def _as_dict(value) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _json_safe(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def _get_nested_value(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _get_first_price(subscription: dict[str, Any]) -> dict[str, Any]:
    items_container = _get_nested_value(subscription, "items", {}) or {}
    items = _get_nested_value(items_container, "data", []) or []
    if isinstance(items, dict):
        items = items.get("data") or list(items.values())
    elif not isinstance(items, (list, tuple)):
        try:
            items = list(items)
        except TypeError:
            items = [items]
    if not items:
        return {}
    price = _get_nested_value(items[0], "price", {}) or {}
    return _as_dict(price)


def _get_first_subscription_item(subscription: dict[str, Any]) -> dict[str, Any]:
    items_container = _get_nested_value(subscription, "items", {}) or {}
    items = _get_nested_value(items_container, "data", []) or []
    if isinstance(items, dict):
        items = items.get("data") or list(items.values())
    elif not isinstance(items, (list, tuple)):
        try:
            items = list(items)
        except TypeError:
            items = [items]
    if not items:
        return {}
    return _as_dict(items[0])


def _timestamp_to_datetime(value) -> timezone.datetime | None:
    if not value:
        return None
    try:
        return timezone.datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except Exception:
        return None


def _timestamp_to_date(value):
    dt = _timestamp_to_datetime(value)
    if dt is None:
        return None
    return timezone.localtime(dt).date()


def _clean_invoice_number(value: str, *, fallback_prefix: str, fallback_key: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned:
        return cleaned
    fallback_key = str(fallback_key or "").strip()
    if not fallback_key:
        return ""
    compact = fallback_key.replace("cs_", "").replace("in_", "").replace("-", "").upper()[:12]
    return f"{fallback_prefix}-{compact}"


def _coerce_amount(value) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _collapse_invoice_lines(lines_value) -> str:
    lines_dict = _as_dict(lines_value)
    items = _get_nested_value(lines_dict, "data", []) or []
    if isinstance(items, dict):
        items = list(items.values())
    descriptions: list[str] = []
    for item in items if isinstance(items, (list, tuple)) else []:
        item_dict = _as_dict(item)
        description = str(item_dict.get("description") or "").strip()
        if description and description not in descriptions:
            descriptions.append(description)
    return "\n".join(descriptions).strip()


def _normalize_address(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip()).strip()
    if isinstance(value, dict):
        ordered = []
        for key in ("line1", "line2", "city", "state", "postal_code", "country"):
            part = str(value.get(key) or "").strip()
            if part:
                ordered.append(part)
        return "\n".join(ordered).strip()
    return ""


def _normalize_invoice_billing_details(details: dict[str, Any] | None) -> dict[str, str]:
    details = details or {}
    return {
        "first_name": str(details.get("first_name") or "").strip(),
        "last_name": str(details.get("last_name") or "").strip(),
        "company_name": str(details.get("company_name") or "").strip(),
        "email": str(details.get("email") or "").strip(),
        "address_line_1": str(details.get("address_line_1") or "").strip(),
        "address_line_2": str(details.get("address_line_2") or "").strip(),
        "city": str(details.get("city") or "").strip(),
        "state_region": str(details.get("state_region") or "").strip(),
        "postcode": str(details.get("postcode") or "").strip(),
        "country": str(details.get("country") or "").strip(),
        "tax_id": str(details.get("tax_id") or "").strip(),
        "is_business_purchase": (
            "1" if str(details.get("is_business_purchase") or "").strip().lower() in {"1", "true", "yes", "on"} else ""
        ),
    }


def _billing_details_to_metadata(details: dict[str, Any] | None) -> dict[str, str]:
    normalized = _normalize_invoice_billing_details(details)
    if not any(normalized.values()):
        return {}
    return {
        f"{BILLING_METADATA_PREFIX}{key}": value
        for key, value in normalized.items()
    }


def _billing_details_from_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    metadata = metadata or {}
    extracted: dict[str, str] = {}
    for key in (
        "first_name",
        "last_name",
        "company_name",
        "email",
        "address_line_1",
        "address_line_2",
        "city",
        "state_region",
        "postcode",
        "country",
        "tax_id",
        "is_business_purchase",
    ):
        extracted[key] = str(metadata.get(f"{BILLING_METADATA_PREFIX}{key}") or "").strip()
    return extracted


def _billing_address_from_details(details: dict[str, Any] | None) -> str:
    details = details or {}
    return "\n".join(
        part
        for part in [
            str(details.get("address_line_1") or "").strip(),
            str(details.get("address_line_2") or "").strip(),
            str(details.get("city") or "").strip(),
            str(details.get("state_region") or "").strip(),
            str(details.get("postcode") or "").strip(),
            str(details.get("country") or "").strip(),
        ]
        if part
    ).strip()


def _billing_contact_name(details: dict[str, Any] | None, *, fallback: str = "") -> str:
    details = details or {}
    full_name = " ".join(
        part for part in [str(details.get("first_name") or "").strip(), str(details.get("last_name") or "").strip()] if part
    ).strip()
    return full_name or str(fallback or "").strip()


def _upsert_invoice_record(
    *,
    user,
    source_type: str,
    stripe_invoice_id: str = "",
    stripe_checkout_session_id: str = "",
    payload: dict[str, Any],
    fallback_number_prefix: str,
    fallback_description: str = "",
    fallback_status: str = "",
    fallback_currency: str = "GBP",
    fallback_issue_date=None,
    fallback_paid_at=None,
    subscription_record: UserSubscription | None = None,
) -> BillingInvoice:
    invoice = None
    if stripe_invoice_id:
        invoice = BillingInvoice.objects.filter(stripe_invoice_id=stripe_invoice_id).first()
    if invoice is None and stripe_checkout_session_id:
        invoice = BillingInvoice.objects.filter(stripe_checkout_session_id=stripe_checkout_session_id).first()
    if invoice is None:
        invoice = BillingInvoice(user=user)

    payload = _json_safe(payload or {})
    customer_details = _as_dict(payload.get("customer_details"))
    metadata = _as_dict(payload.get("metadata"))
    billing_details = _billing_details_from_metadata(metadata)
    billing_reason = str(payload.get("billing_reason") or "").strip()
    created_issue_date = _timestamp_to_date(payload.get("created"))
    due_date = _timestamp_to_date(payload.get("due_date"))
    status_transitions = _as_dict(payload.get("status_transitions"))
    paid_at = _timestamp_to_datetime(status_transitions.get("paid_at")) or fallback_paid_at
    description = str(payload.get("description") or "").strip() or _collapse_invoice_lines(payload.get("lines"))
    if not description:
        description = fallback_description

    total_amount_value = payload.get("total") if "total" in payload else payload.get("amount_total")
    subtotal_amount_value = payload.get("subtotal") if "subtotal" in payload else payload.get("amount_subtotal")
    tax_amount_value = payload.get("tax")
    amount_paid_value = payload.get("amount_paid") if "amount_paid" in payload else payload.get("amount_total")
    total_amount = _coerce_amount(total_amount_value)
    subtotal_amount = _coerce_amount(subtotal_amount_value or total_amount)
    tax_amount = _coerce_amount(tax_amount_value)
    # Stripe can omit explicit tax values for VAT-inclusive prices on invoice payloads.
    if total_amount > 0 and tax_amount <= 0 and subtotal_amount >= total_amount:
        subtotal_amount, tax_amount = _vat_breakdown_from_gross_minor(total_amount)

    invoice.user = user
    invoice.subscription_record = subscription_record or get_subscription_record(user)
    invoice.stripe_invoice_id = stripe_invoice_id or invoice.stripe_invoice_id
    invoice.stripe_checkout_session_id = stripe_checkout_session_id or invoice.stripe_checkout_session_id
    invoice.source_type = source_type or invoice.source_type
    invoice.status = str(payload.get("status") or fallback_status or invoice.status or "").strip()
    invoice.currency = str(payload.get("currency") or fallback_currency or invoice.currency or "GBP").upper()
    invoice.issue_date = created_issue_date or fallback_issue_date or invoice.issue_date or timezone.localdate()
    invoice.due_date = due_date or invoice.due_date
    invoice.paid_at = paid_at or invoice.paid_at
    if not invoice.invoice_number:
        invoice.invoice_number = _clean_invoice_number(
            str(payload.get("number") or ""),
            fallback_prefix=fallback_number_prefix,
            fallback_key=stripe_invoice_id or stripe_checkout_session_id,
        )
    if not invoice.seller_name:
        invoice.seller_name = str(getattr(settings, "SITE_NAME", "") or "AI Novel Creator").strip() or "AI Novel Creator"
    if not invoice.seller_email:
        invoice.seller_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    buyer_name = _billing_contact_name(
        billing_details,
        fallback=str(customer_details.get("name") or getattr(user, "get_username", lambda: "")() or "").strip(),
    )
    buyer_company_name = str(billing_details.get("company_name") or "").strip()
    buyer_email = str(billing_details.get("email") or customer_details.get("email") or getattr(user, "email", "") or "").strip()
    buyer_address = _billing_address_from_details(billing_details) or _normalize_address(customer_details.get("address"))
    buyer_tax_id = str(billing_details.get("tax_id") or "").strip()
    if buyer_name:
        invoice.buyer_name = buyer_name
    if buyer_company_name:
        invoice.buyer_company_name = buyer_company_name
    if buyer_email:
        invoice.buyer_email = buyer_email
    if buyer_address:
        invoice.buyer_address = buyer_address
    if buyer_tax_id:
        invoice.buyer_tax_id = buyer_tax_id
    if not invoice.description:
        invoice.description = description
    invoice.subtotal_amount = subtotal_amount
    invoice.tax_amount = tax_amount
    invoice.total_amount = total_amount
    invoice.amount_paid = _coerce_amount(amount_paid_value)
    invoice.notes = invoice.notes or billing_reason
    invoice.raw_data = payload
    invoice.save()
    return invoice


def _sync_checkout_invoice(
    *,
    user,
    session: dict[str, Any],
    checkout_session_id: str,
    option: dict[str, str] | None = None,
) -> BillingInvoice | None:
    payment_status = str(session.get("payment_status") or "").strip().lower()
    if payment_status not in {"paid", "no_payment_required"}:
        return None
    if option is None:
        metadata = session.get("metadata") or {}
        option = get_price_option(str(metadata.get("plan_key") or "").strip())
        if not option:
            option = get_price_option_by_price_id(str(metadata.get("price_id") or "").strip())
    if not option:
        return None

    amount_total = _coerce_amount(session.get("amount_total"))
    amount_subtotal = _coerce_amount(session.get("amount_subtotal") or amount_total)
    tax_amount = _coerce_amount(
        session.get("total_details", {}).get("amount_tax") if isinstance(session.get("total_details"), dict) else 0
    )
    # Stripe can send amount_subtotal == amount_total with missing tax for inclusive prices.
    if amount_total > 0 and tax_amount <= 0 and amount_subtotal >= amount_total:
        amount_subtotal, tax_amount = _vat_breakdown_from_gross_minor(amount_total)
    payload = {
        "number": "",
        "status": "paid",
        "currency": str(session.get("currency") or "GBP").upper(),
        "created": session.get("created"),
        "amount_total": amount_total,
        "subtotal": amount_subtotal,
        "tax": tax_amount,
        "amount_paid": amount_total,
        "description": option.get("label") or option.get("description") or "Billing invoice",
        "customer_details": _as_dict(session.get("customer_details")),
        "metadata": _as_dict(session.get("metadata")),
    }
    return _upsert_invoice_record(
        user=user,
        source_type="checkout_session",
        stripe_checkout_session_id=checkout_session_id,
        payload=payload,
        fallback_number_prefix="CHK",
        fallback_description=option.get("description") or option.get("label") or "Billing invoice",
        fallback_status="paid",
        fallback_currency=str(session.get("currency") or "GBP"),
        fallback_issue_date=_timestamp_to_date(session.get("created")) or timezone.localdate(),
        fallback_paid_at=_timestamp_to_datetime(session.get("created")),
    )


def _sync_session_invoice(
    *,
    user,
    session: dict[str, Any],
    checkout_session_id: str,
    subscription_record: UserSubscription | None = None,
) -> BillingInvoice | None:
    invoice_id = str(session.get("invoice") or "").strip()
    if invoice_id:
        _set_stripe_api_key()
        invoice = stripe.Invoice.retrieve(invoice_id)
        invoice_dict = _as_dict(invoice)
        invoice_metadata = _as_dict(invoice_dict.get("metadata"))
        session_metadata = _as_dict(session.get("metadata"))
        if session_metadata:
            merged_metadata = dict(session_metadata)
            merged_metadata.update(invoice_metadata)
            invoice_dict["metadata"] = merged_metadata
        return _upsert_invoice_record(
            user=user,
            source_type="stripe_invoice",
            stripe_invoice_id=invoice_id,
            stripe_checkout_session_id=checkout_session_id,
            payload=invoice_dict,
            fallback_number_prefix="INV",
            fallback_description="Stripe invoice",
            fallback_status=str(invoice_dict.get("status") or "").strip(),
            fallback_currency=str(invoice_dict.get("currency") or session.get("currency") or "GBP"),
            fallback_issue_date=_timestamp_to_date(invoice_dict.get("created") or session.get("created")) or timezone.localdate(),
            subscription_record=subscription_record,
        )
    return _sync_checkout_invoice(user=user, session=session, checkout_session_id=checkout_session_id)


def ensure_stripe_customer(user) -> tuple[UserSubscription, str]:
    _set_stripe_api_key()
    record = get_or_create_subscription_record(user)
    if record.stripe_customer_id:
        try:
            customer = stripe.Customer.retrieve(record.stripe_customer_id)
            customer_id = str(getattr(customer, "id", "") or record.stripe_customer_id).strip()
            if customer_id:
                return record, customer_id
        except stripe.error.InvalidRequestError as exc:
            if "No such customer" not in str(exc):
                raise

    customer = stripe.Customer.create(
        email=(getattr(user, "email", "") or "").strip() or None,
        name=(getattr(user, "get_username", lambda: "")() or "").strip() or None,
        metadata={"user_id": str(user.id)},
    )
    record.stripe_customer_id = customer.id
    record.save(update_fields=["stripe_customer_id", "updated_at"])
    return record, customer.id


def _build_checkout_metadata(*, user, option: dict[str, str], billing_details: dict[str, Any] | None = None) -> dict[str, str]:
    metadata = {
        "user_id": str(user.id),
        "plan_key": option["key"],
        "price_id": option["price_id"],
        "checkout_mode": option["checkout_mode"],
        "access_days": str(option.get("access_days") or ""),
    }
    metadata.update(_billing_details_to_metadata(billing_details))
    return metadata


def create_checkout_session(*, user, option: dict[str, str], success_url: str, cancel_url: str, billing_details: dict[str, Any] | None = None):
    _set_stripe_api_key()
    record, customer_id = ensure_stripe_customer(user)
    metadata = _build_checkout_metadata(user=user, option=option, billing_details=billing_details)
    session_kwargs = dict(
        mode=option["checkout_mode"],
        customer=customer_id,
        client_reference_id=str(user.id),
        line_items=[{"price": option["price_id"], "quantity": 1}],
        allow_promotion_codes=True,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )
    if option["checkout_mode"] == "subscription":
        session_kwargs["subscription_data"] = {"metadata": metadata}
    else:
        session_kwargs["payment_intent_data"] = {"metadata": metadata}
    session = stripe.checkout.Session.create(**session_kwargs)
    record.last_checkout_session_id = session.id
    record.save(update_fields=["last_checkout_session_id", "updated_at"])
    return session


def _resolve_checkout_user(*, customer_id: str, metadata: dict[str, Any], client_reference_id: str = ""):
    resolved_user = _resolve_user(customer_id=customer_id, metadata=metadata)
    if resolved_user is not None:
        return resolved_user
    if client_reference_id:
        try:
            return get_user_model().objects.get(pk=client_reference_id)
        except Exception:
            return None
    return None


def _sync_timeboxed_access_record(
    *,
    user,
    session: dict[str, Any],
    customer_id: str,
    checkout_session_id: str,
) -> UserSubscription | None:
    metadata = session.get("metadata") or {}
    if str(session.get("payment_status") or "").strip().lower() not in {"paid", "no_payment_required"}:
        return sync_customer_only_record(
            user=user,
            customer_id=customer_id,
            checkout_session_id=checkout_session_id,
        )

    option = get_price_option(str(metadata.get("plan_key") or "").strip())
    if not option:
        option = get_price_option_by_price_id(str(metadata.get("price_id") or "").strip())
    if not option:
        return sync_customer_only_record(
            user=user,
            customer_id=customer_id,
            checkout_session_id=checkout_session_id,
        )

    access_days = int(str(metadata.get("access_days") or option.get("access_days") or "0") or "0")
    if access_days <= 0:
        return sync_customer_only_record(
            user=user,
            customer_id=customer_id,
            checkout_session_id=checkout_session_id,
        )

    record = get_or_create_subscription_record(user)
    period_start = timezone.now()
    period_end = period_start + timedelta(days=access_days)
    record.stripe_customer_id = customer_id or record.stripe_customer_id
    record.stripe_subscription_id = ""
    record.stripe_product_id = ""
    record.stripe_price_id = option["price_id"]
    record.billing_interval = option["interval"]
    record.status = "trialing" if option["key"] == "trial_week" else "active"
    record.cancel_at_period_end = True
    record.current_period_start = period_start
    record.current_period_end = period_end
    record.trial_end = period_end if option["key"] == "trial_week" else None
    record.last_checkout_session_id = checkout_session_id
    record.raw_data = _json_safe(session)
    record.save()
    _sync_checkout_invoice(
        user=user,
        session=session,
        checkout_session_id=checkout_session_id,
        option=option,
    )
    return record


def sync_checkout_session(*, user, session_id: str) -> UserSubscription | None:
    session_id = str(session_id or "").strip()
    if not session_id:
        return None

    _set_stripe_api_key()
    session = stripe.checkout.Session.retrieve(session_id)
    session_dict = _as_dict(session)
    customer_id = str(session_dict.get("customer") or "").strip()
    metadata = session_dict.get("metadata") or {}
    resolved_user = _resolve_checkout_user(
        customer_id=customer_id,
        metadata=metadata,
        client_reference_id=str(session_dict.get("client_reference_id") or "").strip(),
    )

    if resolved_user is None or resolved_user.pk != user.pk:
        return None

    checkout_session_id = str(session_dict.get("id") or "").strip()
    record = sync_customer_only_record(
        user=user,
        customer_id=customer_id,
        checkout_session_id=checkout_session_id,
    )
    subscription_id = str(session_dict.get("subscription") or "").strip()
    if not subscription_id:
        return _sync_timeboxed_access_record(
            user=user,
            session=session_dict,
            customer_id=customer_id,
            checkout_session_id=checkout_session_id,
        ) or record

    subscription = stripe.Subscription.retrieve(subscription_id)
    subscription_record = sync_subscription_record(
        user=user,
        subscription=subscription,
        checkout_session_id=checkout_session_id,
    )
    _sync_session_invoice(
        user=user,
        session=session_dict,
        checkout_session_id=checkout_session_id,
        subscription_record=subscription_record,
    )
    return subscription_record


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


def cancel_recurring_subscription(*, user) -> UserSubscription | None:
    record = get_subscription_record(user)
    if record is None:
        return None

    subscription_id = str(record.stripe_subscription_id or "").strip()
    if not subscription_id:
        return None

    _set_stripe_api_key()
    subscription = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
    return sync_subscription_record(user=user, subscription=subscription)


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
    first_item = _get_first_subscription_item(subscription_dict)
    product_id = str(price.get("product") or "").strip()
    recurring = price.get("recurring") or {}
    record.stripe_customer_id = str(subscription_dict.get("customer") or record.stripe_customer_id or "").strip()
    record.stripe_subscription_id = str(subscription_dict.get("id") or "").strip()
    record.stripe_price_id = str(price.get("id") or "").strip()
    record.stripe_product_id = product_id
    record.billing_interval = str(recurring.get("interval") or "").strip()
    record.status = str(subscription_dict.get("status") or "").strip()
    record.cancel_at_period_end = bool(subscription_dict.get("cancel_at_period_end"))
    record.current_period_start = _timestamp_to_datetime(
        subscription_dict.get("current_period_start") or first_item.get("current_period_start")
    )
    record.current_period_end = _timestamp_to_datetime(
        subscription_dict.get("current_period_end") or first_item.get("current_period_end")
    )
    record.trial_end = _timestamp_to_datetime(subscription_dict.get("trial_end"))
    if checkout_session_id:
        record.last_checkout_session_id = checkout_session_id
    record.raw_data = _json_safe(subscription_dict)
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
    checkout_session_id = str(session_dict.get("id") or "").strip()
    user = _resolve_checkout_user(
        customer_id=customer_id,
        metadata=metadata,
        client_reference_id=str(session_dict.get("client_reference_id") or "").strip(),
    )
    if user is None:
        return

    sync_customer_only_record(
        user=user,
        customer_id=customer_id,
        checkout_session_id=checkout_session_id,
    )
    subscription_id = str(session_dict.get("subscription") or "").strip()
    if not subscription_id:
        _sync_timeboxed_access_record(
            user=user,
            session=session_dict,
            customer_id=customer_id,
            checkout_session_id=checkout_session_id,
        )
        return
    subscription = stripe.Subscription.retrieve(subscription_id)
    subscription_record = sync_subscription_record(user=user, subscription=subscription, checkout_session_id=checkout_session_id)
    _sync_session_invoice(
        user=user,
        session=session_dict,
        checkout_session_id=checkout_session_id,
        subscription_record=subscription_record,
    )


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
    stripe_invoice_id = str(invoice_dict.get("id") or "").strip()
    subscription_id = str(invoice_dict.get("subscription") or "").strip()
    customer_id = str(invoice_dict.get("customer") or "").strip()
    user = _resolve_user(customer_id=customer_id, metadata=invoice_dict.get("metadata") or {})
    if user is None:
        return
    subscription_record = get_subscription_record(user)
    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        subscription_record = sync_subscription_record(user=user, subscription=subscription)
    _upsert_invoice_record(
        user=user,
        source_type="stripe_invoice",
        stripe_invoice_id=stripe_invoice_id,
        stripe_checkout_session_id=str(invoice_dict.get("parent", {}).get("invoice_item_details", {}).get("invoice") or ""),
        payload=invoice_dict,
        fallback_number_prefix="INV",
        fallback_description="Stripe invoice",
        fallback_status=str(invoice_dict.get("status") or "").strip(),
        fallback_currency=str(invoice_dict.get("currency") or "GBP"),
        fallback_issue_date=_timestamp_to_date(invoice_dict.get("created")) or timezone.localdate(),
        subscription_record=subscription_record,
    )


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
        payload=_json_safe(event_dict),
    )
    return True

