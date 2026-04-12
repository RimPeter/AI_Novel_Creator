from django.urls import path

from . import views

urlpatterns = [
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("billing/webhook/", views.stripe_webhook, name="billing-webhook"),
]
