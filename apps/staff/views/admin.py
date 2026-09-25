"""Main Admin only: settings, prices, administrators, audit log (ADM-04, Section 14)."""

import secrets

from django.contrib import messages
from django.core.paginator import Paginator
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.billing.models import Plan
from apps.core.emails import send_email
from apps.core.models import AuditLog, SiteSettings
from apps.nfc import services as nfc_services
from apps.nfc.models import DeliveryZone, PriceTier

from ..forms import AdminCreateForm, PlanForm, RoleForm, SiteSettingsForm, TierForm, ZoneForm
from ..permissions import staff_required


@staff_required("settings")
def settings_view(request):
    site = SiteSettings.load()
    form = SiteSettingsForm(request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        changed = form.changed_data
        form.save()
        AuditLog.record(request.user, "settings_changed", site, fields=changed)
        messages.success(request, "Settings saved.")
        return redirect("staff:settings")
    return render(request, "staff/settings.html", {"form": form})


@staff_required("settings")
def plans(request):
    return render(request, "staff/plans.html", {"plans": Plan.objects.all()})


@staff_required("settings")
def plan_edit(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    before = plan.price_minor
    form = PlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, "plan_changed", plan, old_price=before, new_price=plan.price_minor, fields=form.changed_data)
        messages.success(request, f"{plan.name} saved. New prices apply to new purchases and renewals only (CUR-06).")
        return redirect("staff:plans")
    return render(request, "staff/form_page.html", {"form": form, "title": f"Edit the {plan.name} plan", "back": "staff:plans"})


@staff_required("settings")
def pricing(request):
    TierSet = modelformset_factory(PriceTier, form=TierForm, extra=1, can_delete=True)
    ZoneSet = modelformset_factory(DeliveryZone, form=ZoneForm, extra=1, can_delete=True)
    tiers = TierSet(request.POST or None, queryset=PriceTier.objects.all(), prefix="tiers")
    zones = ZoneSet(request.POST or None, queryset=DeliveryZone.objects.all(), prefix="zones")
    if request.method == "POST":
        target = request.POST.get("save")
        formset = tiers if target == "tiers" else zones
        if formset.is_valid():
            instances = formset.save(commit=False)
            if target == "tiers":
                # Check the new table before committing: no larger order may cost less (NFP-02).
                current = {t.pk: t for t in PriceTier.objects.all()}
                for deleted in formset.deleted_objects:
                    current.pop(deleted.pk, None)
                for tier in instances:
                    current[tier.pk or f"new-{id(tier)}"] = tier
                proposed = sorted(current.values(), key=lambda t: t.min_quantity)
                problems = nfc_services.pricing_problems(proposed)
                if problems:
                    shown = ", ".join(f"{q} cards ({why})" for q, why in problems[:5])
                    messages.error(request, f"Not saved: the new prices leave gaps or make larger orders cheaper at {shown}.")
                    return render(request, "staff/pricing.html", {"tiers": tiers, "zones": zones})
            for obj in formset.deleted_objects:
                obj.delete()
            for obj in instances:
                obj.save()
            AuditLog.record(request.user, f"{target}_changed", reason="Price table updated")
            messages.success(request, "Saved.")
            return redirect("staff:pricing")
        messages.error(request, "Some rows need fixing.")
    examples = []
    for quantity in (1, 4, 5, 9, 10, 20, 21, 50):
        try:
            examples.append((quantity, nfc_services.cards_price(quantity)))
        except ValueError:
            examples.append((quantity, None))
    return render(request, "staff/pricing.html", {"tiers": tiers, "zones": zones, "examples": examples})


# --------------------------------------------------------------------------
# Administrators (Section 14)
# --------------------------------------------------------------------------


@staff_required("manage_admins")
def admins(request):
    form = AdminCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        member = User.objects.create_user(
            email=data["email"], password=secrets.token_urlsafe(24), full_name=data["full_name"],
            is_staff=True, staff_role=data["role"],
        )
        member.email_verified_at = member.date_joined
        member.save(update_fields=["email_verified_at"])
        send_email(
            to=member.email, subject="You have been added to the AD Smart staff area", template="staff_invite",
            context={"user": member, "role": member.role_label}, user=member, kind="staff_invite",
        )
        AuditLog.record(request.user, "admin_created", member, role=data["role"])
        messages.success(request, f"{member.email} was invited. They set a password with the code we emailed, then enrol two-factor.")
        return redirect("staff:admins")
    staff = User.objects.filter(is_staff=True).order_by("staff_role", "email")
    return render(request, "staff/admins.html", {"form": form, "staff": staff, "role_form": RoleForm()})


@staff_required("manage_admins")
@require_POST
def admin_update(request, pk):
    member = get_object_or_404(User, pk=pk, is_staff=True)
    if member == request.user:
        messages.error(request, "You cannot change your own access.")
        return redirect("staff:admins")
    action = request.POST.get("action")
    if action == "role":
        form = RoleForm(request.POST)
        if form.is_valid():
            old = member.staff_role
            member.staff_role = form.cleaned_data["role"]
            member.save(update_fields=["staff_role"])
            AuditLog.record(request.user, "admin_role_changed", member, old=old, new=member.staff_role)
            messages.success(request, "Role updated.")
    elif action == "deactivate":
        member.is_active = not member.is_active
        member.save(update_fields=["is_active"])
        AuditLog.record(request.user, "admin_deactivated" if not member.is_active else "admin_reactivated", member)
        messages.success(request, f"{member.email} {'deactivated' if not member.is_active else 'reactivated'}.")
    return redirect("staff:admins")


@staff_required("audit")
def audit(request):
    qs = AuditLog.objects.select_related("actor")
    action = request.GET.get("action", "")
    who = request.GET.get("who", "")
    if action:
        qs = qs.filter(action=action)
    if who:
        qs = qs.filter(actor__email__icontains=who)
    actions = AuditLog.objects.values_list("action", flat=True).distinct().order_by("action")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "staff/audit.html", {"page": page, "actions": actions, "action": action, "who": who})
