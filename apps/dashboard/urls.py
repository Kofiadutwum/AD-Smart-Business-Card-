from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("card/", views.editor, name="editor"),
    path("cards/", views.cards_list, name="cards"),
    path("cards/<int:pk>/edit/", views.editor, name="editor_card"),
    path("cards/<int:pk>/slug/", views.change_slug, name="change_slug"),
    path("cards/<int:pk>/toggle/", views.card_toggle, name="card_toggle"),
    path("cards/<int:pk>/delete/", views.card_delete, name="card_delete"),
    path("links/", views.links, name="links"),
    path("cards/<int:pk>/links/", views.links, name="links_card"),
    path("links/<int:link_id>/<str:action>/", views.link_action, name="link_action"),
    path("share/", views.share, name="share"),
    path("cards/<int:pk>/share/", views.share, name="share_card"),
    path("analytics/", views.analytics, name="analytics"),
    path("cards/<int:pk>/analytics/", views.analytics, name="analytics_card"),
    path("contacts/", views.leads, name="leads"),
    path("contacts/export.csv", views.leads_csv, name="leads_csv"),
    path("contacts/<int:pk>/delete/", views.lead_delete, name="lead_delete"),
    path("business/", views.business, name="business"),
    path("business/<str:kind>/add/", views.business_item, name="business_add"),
    path("business/<str:kind>/<int:pk>/edit/", views.business_item, name="business_edit"),
    path("business/<str:kind>/<int:pk>/delete/", views.business_item_delete, name="business_delete"),
    path("nfc/", views.nfc_orders, name="nfc"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/delete-account/", views.request_deletion, name="request_deletion"),
]
