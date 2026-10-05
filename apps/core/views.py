import hmac
import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)


@csrf_exempt
@require_POST
def daily_jobs(request):
    """Run the daily job for an outside scheduler (GitHub Actions) while the
    site is on a Render plan without cron. Every step is safe to re-run."""
    if not settings.CRON_SECRET:
        raise Http404
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), settings.CRON_SECRET.encode()):
        return HttpResponseForbidden("Forbidden\n", content_type="text/plain")
    out, err = io.StringIO(), io.StringIO()
    call_command("run_daily_jobs", stdout=out, stderr=err)
    failed = bool(err.getvalue())
    return HttpResponse(out.getvalue() + err.getvalue(), content_type="text/plain", status=500 if failed else 200)


@require_POST
def preferences(request):
    """Remember the visitor's display currency (a cookie, and their account when signed in)."""
    from . import currency
    from .utils import safe_next

    response = redirect(safe_next(request.POST.get("next")) or "/")
    language = request.POST.get("language")
    if language in dict(settings.LANGUAGES):
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, language, max_age=settings.LANGUAGE_COOKIE_AGE, path=settings.LANGUAGE_COOKIE_PATH,
            samesite=settings.LANGUAGE_COOKIE_SAMESITE, secure=request.is_secure(),
        )
        user = request.user
        if user.is_authenticated and not user.is_staff:
            get_user_model().objects.filter(pk=user.pk).update(language=language)
    code = currency.valid(request.POST.get("currency"))
    if code:
        response.set_cookie(
            currency.COOKIE_NAME, code, max_age=currency.COOKIE_MAX_AGE, samesite="Lax",
            secure=request.is_secure(), httponly=True,
        )
        user = request.user
        if user.is_authenticated and not user.is_staff:
            get_user_model().objects.filter(pk=user.pk).update(currency=code)
    return response
