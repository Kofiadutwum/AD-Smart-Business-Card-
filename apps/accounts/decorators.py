from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def customer_required(view):
    """Signed in, email verified (REG-02), and not a staff-only account."""

    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if user.is_staff:
            return redirect("staff:home")
        if not user.is_email_verified:
            return redirect("accounts:verify_pending")
        return view(request, *args, **kwargs)

    return wrapped
