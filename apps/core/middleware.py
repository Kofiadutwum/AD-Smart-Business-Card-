from django.http import HttpResponse


class HealthCheckMiddleware:
    """Answer Render's health check before host validation and the HTTPS
    redirect, which would otherwise turn the probe into a 400 or a 301."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Headers Django does not set by itself."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(self), payment=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        return response


class PreferencesMiddleware:
    """Work out the visitor's display currency once per request (cookie, then account, then dollars)."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def choose_language(request):
        """Staff pages are English. A signed-in customer gets the language they saved, unless this browser
        has just been told otherwise; everyone else follows the browser (LocaleMiddleware, which ran before)."""
        from django.conf import settings
        from django.utils import translation

        language = None
        if request.path.startswith("/staff"):
            language = "en"
        else:
            user = getattr(request, "user", None)
            if (
                user is not None and user.is_authenticated and not user.is_staff
                and settings.LANGUAGE_COOKIE_NAME not in request.COOKIES
            ):
                language = getattr(user, "language", "") or None
        if language and language in dict(settings.LANGUAGES):
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()

    def __call__(self, request):
        from . import currency

        request.currency = currency.choice_for(request)
        currency.activate(request.currency)
        self.choose_language(request)
        try:
            return self.get_response(request)
        finally:
            currency.activate(currency.DEFAULT)  # never leak one visitor's choice into other code
