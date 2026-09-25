from django.urls import path

from .views import admin, auth, content, finance, nfc, overview, subscribers

app_name = "staff"

urlpatterns = [
    path("login", auth.staff_login, name="login"),
    path("2fa", auth.two_factor, name="two_factor"),
    path("logout", auth.staff_logout, name="logout"),
    path("", overview.home, name="home"),
    # subscribers
    path("subscribers/", subscribers.subscribers, name="subscribers"),
    path("subscribers/<int:pk>/", subscribers.subscriber, name="subscriber"),
    path("subscribers/<int:pk>/activate/", subscribers.activate, name="activate"),
    path("subscribers/<int:pk>/suspend/", subscribers.suspend, name="suspend"),
    path("cards/<int:pk>/", subscribers.card_preview, name="card_preview"),
    path("cards/<int:pk>/suspend/", subscribers.suspend_card, name="suspend_card"),
    # finance
    path("payments/", finance.payments, name="payments"),
    path("payments/export.<str:fmt>", finance.payments_export, name="payments_export"),
    path("payments/<int:pk>/", finance.payment_detail, name="payment"),
    path("payments/<int:pk>/refund/", finance.refund, name="refund"),
    path("reports/finance/", finance.report, name="finance_report"),
    path("promo-codes/", finance.promo_codes, name="promo_codes"),
    path("promo-codes/<int:pk>/", finance.promo_edit, name="promo_edit"),
    # nfc
    path("nfc/", nfc.orders, name="nfc_orders"),
    path("nfc/<int:pk>/", nfc.order_detail, name="nfc_detail"),
    path("nfc/<int:pk>/status/", nfc.order_status, name="nfc_status"),
    path("nfc/<int:pk>/proof/", nfc.order_proof, name="nfc_proof"),
    path("nfc/<int:pk>/quality/", nfc.order_quality, name="nfc_quality"),
    path("nfc/<int:pk>/note/", nfc.order_note, name="nfc_note"),
    path("nfc/<int:pk>/file/<str:which>/", nfc.order_file, name="nfc_file"),
    # support, reports, gallery, email
    path("support/", content.support_list, name="support"),
    path("support/<int:pk>/", content.support_detail, name="support_detail"),
    path("reports/", content.reports, name="reports"),
    path("reports/<int:pk>/", content.report_action, name="report_action"),
    path("gallery/", content.gallery, name="gallery"),
    path("gallery/<int:pk>/", content.gallery_edit, name="gallery_edit"),
    path("gallery/<int:pk>/action/", content.gallery_action, name="gallery_action"),
    path("email/", content.email_subscribers, name="email"),
    # main admin
    path("settings/", admin.settings_view, name="settings"),
    path("settings/plans/", admin.plans, name="plans"),
    path("settings/plans/<int:pk>/", admin.plan_edit, name="plan_edit"),
    path("settings/pricing/", admin.pricing, name="pricing"),
    path("admins/", admin.admins, name="admins"),
    path("admins/<int:pk>/", admin.admin_update, name="admin_update"),
    path("admins/<int:pk>/reset-2fa/", subscribers.reset_2fa, name="reset_2fa"),
    path("audit/", admin.audit, name="audit"),
]
