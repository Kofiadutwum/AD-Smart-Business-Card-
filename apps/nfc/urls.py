from django.urls import path

from . import views

app_name = "nfc"

urlpatterns = [
    path("order", views.order, name="order"),
    path("callback", views.callback, name="callback"),  # same path as the Flask site
    path("orders/<str:number>", views.detail, name="detail"),
    path("orders/<str:number>/pay", views.pay, name="pay"),
    path("orders/<str:number>/cancel", views.cancel, name="cancel"),
    path("orders/<str:number>/proofs/<int:version>/approve", views.approve, name="approve"),
    path("orders/<str:number>/proofs/<int:version>/changes", views.request_changes, name="changes"),
    path("orders/<str:number>/proofs/<int:version>/file", views.proof_file, name="proof_file"),
    path("orders/<str:number>/approved.pdf", views.approved_pdf, name="approved_pdf"),
]
