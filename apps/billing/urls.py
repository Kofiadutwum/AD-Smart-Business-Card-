from django.urls import path

from . import views

app_name = "billing"

# /billing/webhook and /billing/callback match the Flask site, so the URLs
# already configured in the Paystack dashboard keep working.
urlpatterns = [
    path("", views.checkout, name="checkout"),
    path("callback", views.callback, name="callback"),
    path("webhook", views.webhook, name="webhook"),
    path("result/<str:reference>", views.result, name="result"),
    path("receipts", views.receipts, name="receipts"),
    path("receipts/<str:reference>.pdf", views.receipt_pdf, name="receipt_pdf"),
]
