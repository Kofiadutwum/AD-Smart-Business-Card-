from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "AD Smart — database admin"
admin.site.site_title = "AD Smart admin"
admin.site.index_title = "Superuser tools"

urlpatterns = [
    path("", include("apps.marketing.urls")),
    path("", include("apps.cards.urls")),
    path("", include("apps.support.urls")),
    path("auth/", include("apps.accounts.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("billing/", include("apps.billing.urls")),
    path("nfc/", include("apps.nfc.urls")),
    path("staff/db/", admin.site.urls),
    path("staff/", include("apps.staff.urls")),
]

handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"

if settings.DEBUG:
    # Local uploads only; production media is on Cloudinary.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
