"""Staff: the shop marketplace. Hide a shop or a product that breaks the rules, and keep the category list."""

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.cards import marketplace
from apps.cards.models import BusinessPage, BusinessProduct, ProductCategory
from apps.core.models import AuditLog

from ..forms import ReasonForm
from ..permissions import staff_required


class CategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name", "position", "is_active"]
        labels = {
            "position": "Order in the sidebar (smaller first)",
            "is_active": "In use (shown to shoppers and offered to shop owners)",
        }

    def save(self, commit=True):
        category = super().save(commit=False)
        if not category.slug:
            base = slugify(category.name)[:60] or "category"
            slug, number = base, 2
            # "other" is the sidebar's name for products with no category, so it can never be a real one.
            while slug == marketplace.OTHER or ProductCategory.objects.filter(slug=slug).exclude(pk=category.pk).exists():
                slug, number = f"{base}-{number}", number + 1
            category.slug = slug
        if commit:
            category.save()
        return category


def _status(page, card):
    if page.marketplace_hidden:
        return "Hidden by staff"
    if not page.in_marketplace:
        return "Owner opted out"
    if not page.is_on:
        return "Page is off"
    if card is None or not card.is_live:
        return "Card not live"
    return "Listed"


@staff_required("marketplace")
def shops(request):
    qs = (
        BusinessPage.objects.select_related("owner")
        .annotate(product_count=Count("products", distinct=True))
        .filter(product_count__gt=0)
        .order_by("-updated_at")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(owner__email__icontains=q) | Q(owner__full_name__icontains=q) | Q(tagline__icontains=q)
            | Q(owner__cards__business_name__icontains=q) | Q(owner__cards__slug__icontains=q)
        ).distinct()
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    rows = []
    for item in page:
        card = item.primary_card()
        rows.append({"page": item, "card": card, "name": (card.business_name or card.full_name) if card else item.owner.email,
                     "status": _status(item, card)})
    return render(request, "staff/marketplace.html", {"page": page, "rows": rows, "q": q})


@staff_required("marketplace")
def shop(request, pk):
    page = get_object_or_404(BusinessPage.objects.select_related("owner"), pk=pk)
    card = page.primary_card()
    stats = marketplace.shop_stats(page)
    products = list(page.products.select_related("category"))
    for product in products:
        seen = stats["products"].get(product.pk, {})
        product.views, product.buys = seen.get("product", 0), seen.get("buy", 0)
    context = {
        "page": page, "card": card, "status": _status(page, card),
        "name": (card.business_name or card.full_name) if card else page.owner.email,
        "products": products, "totals": stats["totals"],
    }
    return render(request, "staff/marketplace_shop.html", context)


@staff_required("marketplace")
@require_POST
def hide_shop(request, pk):
    page = get_object_or_404(BusinessPage, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required.")
        return redirect("staff:marketplace_shop", pk=pk)
    page.marketplace_hidden = not page.marketplace_hidden
    page.save(update_fields=["marketplace_hidden"])  # saving refreshes the marketplace straight away
    AuditLog.record(
        request.user, "shop_hidden" if page.marketplace_hidden else "shop_unhidden", page, reason=form.cleaned_data["reason"]
    )
    messages.success(request, "Shop hidden from the marketplace." if page.marketplace_hidden else "Shop is listed again.")
    return redirect("staff:marketplace_shop", pk=pk)


@staff_required("marketplace")
@require_POST
def hide_product(request, pk):
    product = get_object_or_404(BusinessProduct, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required.")
        return redirect("staff:marketplace_shop", pk=product.page_id)
    product.is_hidden = not product.is_hidden
    product.save(update_fields=["is_hidden"])
    AuditLog.record(
        request.user, "product_hidden" if product.is_hidden else "product_unhidden", product,
        reason=form.cleaned_data["reason"], shop=product.page_id,
    )
    messages.success(request, "Product hidden from the public." if product.is_hidden else "Product is visible again.")
    return redirect("staff:marketplace_shop", pk=product.page_id)


@staff_required("marketplace")
def categories(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save()
        AuditLog.record(request.user, "shop_category_created", category)
        messages.success(request, f"Category “{category.name}” added.")
        return redirect("staff:marketplace_categories")
    return render(request, "staff/marketplace_categories.html", {
        "form": form, "categories": ProductCategory.objects.annotate(product_count=Count("products")),
    })


@staff_required("marketplace")
def category_edit(request, pk):
    category = get_object_or_404(ProductCategory, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, "shop_category_updated", category)
        messages.success(request, "Category updated.")
        return redirect("staff:marketplace_categories")
    return render(request, "staff/form_page.html", {
        "form": form, "title": f"Edit {category.name}", "back": "staff:marketplace_categories",
    })
