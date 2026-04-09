from django.contrib import admin

from .models import BillingCompanyProfile, BillingInvoice, HomeUpdate, ProcessedStripeEvent, UserSubscription


@admin.register(HomeUpdate)
class HomeUpdateAdmin(admin.ModelAdmin):
    list_display = ("date", "title", "updated_at")
    search_fields = ("title", "body")
    ordering = ("-date", "-updated_at")


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "status",
        "billing_interval",
        "cancel_at_period_end",
        "current_period_end",
        "stripe_customer_id",
    )
    search_fields = ("user__username", "user__email", "stripe_customer_id", "stripe_subscription_id", "stripe_price_id")
    list_filter = ("status", "billing_interval", "cancel_at_period_end")


@admin.register(ProcessedStripeEvent)
class ProcessedStripeEventAdmin(admin.ModelAdmin):
    list_display = ("stripe_event_id", "event_type", "created_at")
    search_fields = ("stripe_event_id", "event_type")
    readonly_fields = ("stripe_event_id", "event_type", "payload", "created_at", "updated_at")


@admin.register(BillingInvoice)
class BillingInvoiceAdmin(admin.ModelAdmin):
    list_display = ("public_number", "user", "status", "issue_date", "total_amount", "currency")
    search_fields = ("invoice_number", "stripe_invoice_id", "stripe_checkout_session_id", "user__username", "user__email")
    list_filter = ("status", "currency", "source_type")


@admin.register(BillingCompanyProfile)
class BillingCompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("company_name", "company_email", "updated_at")
