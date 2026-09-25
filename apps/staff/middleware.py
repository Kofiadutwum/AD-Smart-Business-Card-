"""Guards for the staff area and the Django admin (SEC-03, SEC-05, SEC-08).

* Only staff reach /staff/ and /staff/db/ (everyone else gets a 404).
* Staff must pass two-factor authentication in this session.
* Staff sessions end after 30 minutes without activity.
"""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import redirect

OPEN_PATHS = ("/staff/login", "/staff/2fa", "/staff/logout")


class StaffAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith("/staff") and not path.startswith(OPEN_PATHS):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"/staff/login?next={path}")
            if not user.is_staff:
                raise Http404
            now = time.time()
            last = request.session.get("staff_seen")
            if last and now - last > settings.STAFF_IDLE_TIMEOUT_SECONDS:
                logout(request)
                messages.info(request, "You were signed out after 30 minutes without activity.")
                return redirect(f"/staff/login?next={path}")
            if request.session.get("staff_2fa") != user.pk:
                return redirect(f"/staff/2fa?next={path}")
            request.session["staff_seen"] = now
            if path.startswith("/staff/db") and not user.is_superuser:
                raise Http404
        return self.get_response(request)
