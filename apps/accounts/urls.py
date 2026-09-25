from django.urls import path

from . import views

app_name = "accounts"

# The /auth/ prefix and the Google callback path match the Flask site, so
# the redirect URI registered with Google keeps working after the move.
urlpatterns = [
    path("register", views.register, name="register"),
    path("login", views.login_view, name="login"),
    path("logout", views.logout_view, name="logout"),
    path("verify", views.verify_pending, name="verify_pending"),
    path("verify/<str:token>", views.verify_email, name="verify_email"),
    path("forgot-password", views.forgot_password, name="forgot_password"),
    path("forgot-password/verify", views.reset_password, name="reset_password"),
    path("password", views.change_password, name="change_password"),
    path("google", views.google_start, name="google"),
    path("google/callback", views.google_callback, name="google_callback"),
]
