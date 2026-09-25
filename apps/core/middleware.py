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
