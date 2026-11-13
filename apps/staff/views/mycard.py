"""Staff cards: each administrator's own card, and the company card whose two phones the landing page shows."""

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.services import plan_for_limits
from apps.cards import services
from apps.cards.colors import palette
from apps.cards.models import MAX_SERVICES, BusinessProduct, BusinessService, Card, CardPhone, SocialLink
from apps.core.fields import PictureField
from apps.core.utils import normalise_phone, process_image
from apps.dashboard.forms import BusinessPageForm, PhoneForm, ProductForm, ServiceForm, SocialLinkForm

from ..permissions import can, staff_required

PHONE_MAX = 5


class StaffCardForm(forms.ModelForm):
    """Only what the card shows: no plans, colours or layouts to choose."""

    avatar_upload = PictureField(label="Photo", required=False, help_text="JPG, PNG, HEIC, WebP and more. It is resized for fast loading.")
    logo_upload = PictureField(label="Logo", required=False, help_text="Shown in the top band of the card.")

    class Meta:
        model = Card
        fields = ["full_name", "job_title", "business_name", "bio", "whatsapp", "email", "allow_indexing"]
        labels = {
            "full_name": "Name", "job_title": "Position", "business_name": "Business name", "bio": "Short bio",
            "whatsapp": "WhatsApp number", "email": "Email address", "allow_indexing": "Let search engines show this card",
        }
        help_texts = {
            "bio": "Up to 300 characters.",
            "allow_indexing": "Off by default, so the card only reaches people you share it with.",
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3, "maxlength": 300}),
            "whatsapp": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "024 412 3456"}),
        }

    def __init__(self, *args, company=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not company:
            del self.fields["logo_upload"]

    def clean_whatsapp(self):
        value = self.cleaned_data.get("whatsapp")
        return normalise_phone(value) if value else ""

    def clean(self):
        cleaned = super().clean()
        for name, folder, size in (("avatar_upload", "avatars", 640), ("logo_upload", "logos", 400)):
            upload = cleaned.get(name)
            if upload:
                try:
                    cleaned[name] = process_image(upload, folder=folder, max_px=size)
                except ValidationError as exc:
                    self.add_error(name, exc)
        return cleaned

    def save(self, commit=True):
        card = super().save(commit=False)
        for upload, field in (("avatar_upload", "avatar"), ("logo_upload", "logo")):
            new_file = self.cleaned_data.get(upload)
            if new_file:
                old = getattr(card, field)
                if old:
                    old.delete(save=False)
                getattr(card, field).save(new_file.name, new_file, save=False)
        if commit:
            card.save()
        return card


class LandingPageForm(BusinessPageForm):
    """The company's business page is always on, so there is no switch."""

    class Meta(BusinessPageForm.Meta):
        fields = ["tagline", "about", "hours"]


def _phones(card, data=None):
    extra = 1 if card.phones.count() < PHONE_MAX else 0
    Phones = inlineformset_factory(
        Card, CardPhone, form=PhoneForm, extra=extra, max_num=PHONE_MAX, validate_max=True, can_delete=True,
    )
    return Phones(data, instance=card, prefix="phones")


def _may_edit(user, card):
    return can(user, "landing") if card.is_company else card.owner_id == user.pk


def _card_screen(request, card, company):
    """The card form, phone numbers and social links shared by both pages: (redirect or None, context)."""
    posted = request.POST.get("form") if request.method == "POST" else ""
    form = StaffCardForm(
        request.POST if posted == "card" else None, request.FILES if posted == "card" else None,
        instance=card, company=company,
    )
    phones = _phones(card, request.POST if posted == "card" else None)
    link_form = SocialLinkForm(request.POST if posted == "social" else None)
    if posted == "card":
        if form.is_valid() and phones.is_valid():
            form.save()
            phones.save()
            for position, phone in enumerate(card.phones.all()):
                if phone.position != position:
                    CardPhone.objects.filter(pk=phone.pk).update(position=position)
            messages.success(request, "Card saved.")
            return redirect(request.path), None
        messages.error(request, "Some details need fixing. Look for the messages in red.")
    elif posted == "social" and link_form.is_valid():
        link = link_form.save(commit=False)
        link.card, link.position = card, card.social_links.count()
        link.save()
        messages.success(request, f"{link.label} added.")
        return redirect(request.path), None
    eff = card.effective()
    return None, {
        "card": card, "form": form, "phones": phones, "link_form": link_form, "links": card.social_links.all(),
        "company": company,
        "preview": {"card": card, "eff": eff, "pal": palette(eff["accent"], eff["card_colour"]), "card_url": card.public_url(),
                    "vcf_url": "#", "demo": True},
    }


@staff_required()
def my_card(request):
    card = services.ensure_staff_card(request.user)
    response, context = _card_screen(request, card, company=False)
    return response or render(request, "staff/my_card.html", context)


@staff_required("landing")
def landing(request):
    card = services.ensure_company_card()
    if card is None:
        raise Http404
    page = card.owner.business_page
    if request.method == "POST" and request.POST.get("form") == "page":
        page_form = LandingPageForm(request.POST, instance=page)
        if page_form.is_valid():
            page_form.save()
            messages.success(request, "Business page saved.")
            return redirect("staff:landing")
    else:
        page_form = LandingPageForm(instance=page)
    response, context = _card_screen(request, card, company=True)
    if response:
        return response
    context.update(
        page=page, page_form=page_form, products=page.products.all(), services=page.services.all(),
        max_products=plan_for_limits(card.owner).max_products, max_services=MAX_SERVICES,
        page_public_url=page.public_url(),
    )
    return render(request, "staff/landing.html", context)


LANDING_ITEMS = {"products": (BusinessProduct, ProductForm), "services": (BusinessService, ServiceForm)}


def _company_page():
    card = services.ensure_company_card()
    if card is None:
        raise Http404
    return card.owner.business_page


@staff_required("landing")
def landing_item(request, kind, pk=None):
    if kind not in LANDING_ITEMS:
        raise Http404
    page = _company_page()
    model, form_class = LANDING_ITEMS[kind]
    item = get_object_or_404(model, pk=pk, page=page) if pk else None
    limit = plan_for_limits(page.owner).max_products if kind == "products" else MAX_SERVICES
    if item is None and limit is not None and getattr(page, kind).count() >= limit:  # no limit = unlimited
        messages.warning(request, f"You have used all {limit} places here. Remove one to add another.")
        return redirect("staff:landing")
    old_picture = item.image.name if kind == "products" and item else ""  # the form swaps it before we can look
    form = form_class(request.POST or None, request.FILES or None, instance=item)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.page = page
        if item is None:
            saved.position = getattr(page, kind).count()
        saved.save()
        if old_picture and saved.image.name != old_picture:
            saved.image.storage.delete(old_picture)
        messages.success(request, "Saved.")
        return redirect("staff:landing")
    title = ("Edit " if item else "Add a ") + kind[:-1]
    return render(request, "staff/landing_item.html", {"form": form, "title": title})


@staff_required("landing")
@require_POST
def landing_item_delete(request, kind, pk):
    if kind not in LANDING_ITEMS:
        raise Http404
    item = get_object_or_404(LANDING_ITEMS[kind][0], pk=pk, page=_company_page())
    if kind == "products":
        item.image.delete(save=False)
    item.delete()
    messages.info(request, "Removed.")
    return redirect("staff:landing")


@staff_required()
@require_POST
def social_action(request, link_id, action):
    link = get_object_or_404(SocialLink, pk=link_id)
    card = link.card
    if not _may_edit(request.user, card):
        raise Http404
    if action == "delete":
        link.delete()
        messages.info(request, "Link removed.")
    elif action in ("up", "down"):
        ordered = list(card.social_links.all())
        index = ordered.index(link)
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(ordered):
            ordered[index], ordered[swap] = ordered[swap], ordered[index]
            for position, item in enumerate(ordered):
                SocialLink.objects.filter(pk=item.pk).update(position=position)
    return redirect("staff:landing" if card.is_company else "staff:my_card")
