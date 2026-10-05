from django.urls import path

from . import claim, views

app_name = "cards"

# No trailing slashes: these URLs are printed in QR codes and written to NFC
# tags exactly as /c/<slug>, the same shape as on the Flask site.
urlpatterns = [
    path("c/<str:slug>", views.public_card, name="public"),
    path("c/<str:slug>/card.vcf", views.vcf, name="vcf"),
    path("c/<str:slug>/qr.<str:fmt>", views.qr, name="qr"),
    path("c/<str:slug>/connect", views.connect, name="connect"),
    path("c/<str:slug>/report", views.report, name="report"),
    path("c/<str:slug>/e", views.event, name="event"),
    # Pre-made cards: the buyer sets the card up with the PIN on its back.
    path("c/<str:slug>/claim", claim.claim, name="claim"),
    path("c/<str:slug>/claim/setup", claim.claim_setup, name="claim_setup"),
    path("c/<str:slug>/claim/attach", claim.claim_attach, name="claim_attach"),
]
