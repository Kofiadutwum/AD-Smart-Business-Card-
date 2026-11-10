from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "marketing"

urlpatterns = [
    path("", views.home, name="home"),
    path("shops", views.shops, name="shops"),
    path("shops/", RedirectView.as_view(url="/shops", permanent=True, query_string=True)),
    path("pricing", views.pricing, name="pricing"),
    path("nfc-cards", views.nfc_cards, name="nfc"),
    path("delivery", RedirectView.as_view(url="/nfc-cards#delivery", permanent=False), name="delivery"),
    path("faq", views.faq_page, name="faq"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap"),
    path("demo/kofi-adutwum.vcf", views.demo_vcf, name="demo_vcf"),
    path("demo/business", views.demo_business_page, name="demo_business"),
    path("terms", views.legal, {"page": "terms"}, name="terms"),
    path("privacy", views.legal, {"page": "privacy"}, name="privacy"),
    path("refunds", views.legal, {"page": "refunds"}, name="refunds"),
]
