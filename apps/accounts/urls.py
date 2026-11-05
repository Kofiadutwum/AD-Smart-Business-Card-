from django.urls import path, reverse_lazy
from django.views.generic import RedirectView

from . import views

app_name = "accounts"

# The /auth/ prefix and the Google callback path match the Flask site, so
# the redirect URI registered with Google keeps working after the move.
urlpatterns = [
    path("register", views.register, name="register"),
    path("login", views.login_view, name="login"),
    path("logout", views.logout_view, name="logout"),
    # Links in confirmation emails sent before confirmation was removed still land somewhere useful.
    path("verify", RedirectView.as_view(url=reverse_lazy("dashboard:home"))),
    path("verify/<str:token>", RedirectView.as_view(url=reverse_lazy("dashboard:home"))),
    path("check-email", views.check_email, name="check_email"),
    path("email", views.change_email, name="change_email"),
    path("forgot-password", views.forgot_password, name="forgot_password"),
    path("forgot-password/verify", views.reset_password, name="reset_password"),
    path("password", views.change_password, name="change_password"),
    path("one-time-password", views.one_time_password, name="one_time_password"),
    path("google", views.google_start, name="google"),
    path("google/callback", views.google_callback, name="google_callback"),
]
