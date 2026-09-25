from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("contact", views.contact, name="contact"),
    path("support/", views.mine, name="mine"),
    path("support/<str:reference>/", views.ticket, name="ticket"),
    path("support/<str:reference>/reply", views.reply, name="reply"),
]
