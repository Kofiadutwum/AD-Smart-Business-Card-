from django.urls import path

from . import views

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
]
