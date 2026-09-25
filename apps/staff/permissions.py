"""Role-based access for administrators (Section 14, AC-14).

The table in the SRS, as code. Each view names the permission it needs; a
role either has it or the view answers 403.
"""

from functools import wraps

from django.http import Http404
from django.shortcuts import redirect, render

SUPER, FINANCE, SUPPORT, DESIGN, OPS = "super", "finance", "support", "design", "ops"
ALL = {SUPER, FINANCE, SUPPORT, DESIGN, OPS}

PERMISSIONS = {
    "manage_admins": {SUPER},
    "settings": {SUPER},
    "view_subscribers": ALL,  # Design and Ops see a limited view (no payments)
    "view_payments": {SUPER, FINANCE, SUPPORT},
    "manual_activation": {SUPER, FINANCE, SUPPORT},
    "suspend": {SUPER, SUPPORT},
    "finance": {SUPER, FINANCE},
    "refunds": {SUPER, FINANCE},
    "promo_codes": {SUPER, FINANCE},
    "nfc_view": ALL,
    "nfc_design": {SUPER, DESIGN},
    "nfc_production": {SUPER, DESIGN, OPS},
    "support": {SUPER, SUPPORT},
    "reports": {SUPER, SUPPORT},
    "email_subscribers": {SUPER, SUPPORT},
    "gallery": {SUPER, SUPPORT, DESIGN},
    "audit": {SUPER},
}


def can(user, permission):
    return bool(
        user.is_authenticated and user.is_staff and user.role in PERMISSIONS.get(permission, set())
    )


def staff_required(permission=None):
    """404 for non-staff (it does not confirm the area exists), 403 for the wrong role."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"/staff/login?next={request.path}")
            if not user.is_staff:
                raise Http404
            if permission and not can(user, permission):
                return render(request, "staff/forbidden.html", {"permission": permission}, status=403)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


class Perms:
    """``perms.finance`` etc. in templates."""

    def __init__(self, user):
        self.user = user

    def __getattr__(self, name):
        if name in PERMISSIONS:
            return can(self.user, name)
        raise AttributeError(name)
