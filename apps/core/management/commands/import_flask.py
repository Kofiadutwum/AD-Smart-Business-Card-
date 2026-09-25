"""One-off import from the live Flask site (cardhub) into this Django site.

    # 1. Rehearse against a copy of the Flask database. Nothing is saved.
    python manage.py import_flask --source "postgresql://…flask…" --media-base https://old-site.onrender.com

    # 2. When the report looks right, run it for real.
    python manage.py import_flask --source "…" --media-base "…" --commit

What it guarantees:

* Every card keeps its exact slug, so printed QR codes and NFC tags keep
  opening the right card. Tags written by the Flask site use ?s=nfc, which
  the new site still reads.
* Passwords carry over. Werkzeug hashes are stored with a ``werkzeug$``
  prefix and upgraded to Argon2 at each customer's next login.
* Re-running is safe: rows already imported (matched on legacy_id or the
  payment reference) are skipped.
* Flask administrators are NOT given staff access here. Create staff with
  ``createsuperuser`` and Staff › Administrators instead, because the Flask
  seed script created a well-known demo admin password.
"""

import io
import sqlite3
from collections import Counter
from datetime import datetime, timezone as dt_timezone

import dj_database_url
import requests
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import Payment, Plan, Subscription
from apps.cards.models import ACCENT_PRESETS, Card, CardEvent, CardPhone, CardSlug, Lead, SocialLink
from apps.core.utils import normalise_phone, process_image
from apps.marketing.models import GalleryImage
from apps.nfc.models import NFCOrder, NFCOrderItem, OrderEvent

PLAN_MAP = {"starter": "basic", "basic": "basic", "professional": "professional", "business": "business"}
NFC_STATUS_MAP = {
    "pending": NFCOrder.PENDING_PAYMENT,
    "paid": NFCOrder.PAYMENT_CONFIRMED,
    "design_pending": NFCOrder.DESIGN_PENDING,
    "designing": NFCOrder.DESIGN_IN_PROGRESS,
    "ready": NFCOrder.READY_FOR_DELIVERY,
    "delivered": NFCOrder.DELIVERED,
    "cancelled": NFCOrder.CANCELLED,
}
ACCENT_MAP = {
    "blue": ACCENT_PRESETS["blue"],
    "purple": ACCENT_PRESETS["purple"],
    "amber": ACCENT_PRESETS["amber"],
    "ink": ACCENT_PRESETS["ink"],
}


class DryRun(Exception):
    pass


def aware(value):
    """Flask stored naive UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timezone.is_naive(value):
        return value.replace(tzinfo=dt_timezone.utc)
    return value


def phone_or_blank(raw):
    if not raw:
        return ""
    text = str(raw).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits.startswith("233") and not text.startswith("+"):
        text = "+" + digits
    try:
        return normalise_phone(text)
    except Exception:
        return ""


class Command(BaseCommand):
    help = "Import users, cards, subscriptions, payments, analytics and NFC orders from the Flask site."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Flask DATABASE_URL (postgres://… or sqlite:///path).")
        parser.add_argument("--media-base", default="", help="Old site URL, to copy avatars, logos and gallery pictures.")
        parser.add_argument("--commit", action="store_true", help="Save the import. Without it, everything is rolled back.")
        parser.add_argument("--skip-media", action="store_true", help="Do not download pictures.")

    # ------------------------------------------------------------------ io

    def connect(self, url):
        if url.startswith("sqlite:"):
            path = url.split("sqlite:///", 1)[-1]
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn, "sqlite"
        config = dj_database_url.parse(url)
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            host=config["HOST"], port=config["PORT"] or 5432, dbname=config["NAME"],
            user=config["USER"], password=config["PASSWORD"], row_factory=dict_row,
        )
        return conn, "postgres"

    def rows(self, table, order="id"):
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT * FROM {table} ORDER BY {order}")
        except Exception as exc:
            self.problems.append(f"Could not read {table}: {exc}")
            if self.kind == "postgres":
                self.conn.rollback()
            return []
        return [dict(r) for r in cur.fetchall()]

    def fetch(self, paths):
        if self.skip_media or not self.media_base:
            return None
        for path in paths:
            url = self.media_base.rstrip("/") + path
            try:
                response = requests.get(url, timeout=20)
            except requests.RequestException:
                continue
            if response.ok and response.content:
                return response.content
        return None

    def image(self, paths, folder, max_px):
        data = self.fetch(paths)
        if not data:
            return None
        upload = ContentFile(data, name="upload")
        upload.size = len(data)
        try:
            return process_image(upload, folder=folder, max_px=max_px, max_bytes=20 * 1024 * 1024)
        except Exception as exc:
            self.problems.append(f"Picture {paths[0]} could not be processed: {exc}")
            return None

    # ---------------------------------------------------------------- main

    def handle(self, *args, **options):
        self.media_base = options["media_base"]
        self.skip_media = options["skip_media"]
        self.problems = []
        self.counts = Counter()
        self.conn, self.kind = self.connect(options["source"])
        plans = {p.code: p for p in Plan.objects.all()}
        if not {"basic", "professional", "business"} <= set(plans):
            raise CommandError("Run `python manage.py seed_initial` first so the plans exist.")
        self.plans = plans
        try:
            with transaction.atomic():
                self.import_users()
                self.import_cards()
                self.import_subscriptions()
                self.import_payments()
                self.import_views()
                self.import_leads()
                self.import_gallery()
                self.import_nfc()
                self.verify()
                if not options["commit"]:
                    raise DryRun()
        except DryRun:
            self.stdout.write(self.style.WARNING("\nDRY RUN — nothing was saved. Re-run with --commit to import."))
        finally:
            self.conn.close()
        self.report()

    def report(self):
        self.stdout.write("\nImported:")
        for key, value in sorted(self.counts.items()):
            self.stdout.write(f"  {key:<28} {value}")
        if self.problems:
            self.stdout.write(self.style.WARNING(f"\n{len(self.problems)} thing(s) to check:"))
            for problem in self.problems[:200]:
                self.stdout.write(f"  - {problem}")

    # --------------------------------------------------------------- users

    def import_users(self):
        self.user_map = {}
        self.admin_emails = []
        profiles = {r["user_id"] for r in self.rows("profiles")}
        for row in self.rows("users"):
            existing = User.objects.filter(legacy_id=row["id"]).first()
            if existing:
                self.user_map[row["id"]] = existing
                self.counts["users (already there)"] += 1
                continue
            if row.get("is_admin"):
                self.admin_emails.append(row["email"])
                if row["id"] not in profiles:
                    self.problems.append(f"Flask admin {row['email']} was not imported. Create staff accounts in the new staff area.")
                    continue
            email = (row["email"] or "").strip().lower()
            if User.objects.filter(email__iexact=email).exists():
                self.problems.append(f"{email} already exists here; its Flask data was linked to that account.")
                self.user_map[row["id"]] = User.objects.get(email__iexact=email)
                continue
            phone = phone_or_blank(row.get("phone")) or None
            if phone and User.objects.filter(phone=phone).exists():
                self.problems.append(f"Phone {phone} of {email} is used by another account; imported without a phone.")
                phone = None
            joined = aware(row.get("created_at")) or timezone.now()
            user = User(
                email=email,
                phone=phone,
                google_sub=row.get("google_sub") or None,
                is_suspended=bool(row.get("is_suspended")),
                date_joined=joined,
                last_login=aware(row.get("last_login_at")),
                email_changed_at=aware(row.get("email_changed_at")),
                email_verified_at=joined,  # existing customers
                accepted_terms_at=joined,
                legacy_id=row["id"],
            )
            hashed = row.get("password_hash") or ""
            if hashed.count("$") == 2:
                user.password = "werkzeug$" + hashed
            else:
                user.set_unusable_password()
                self.problems.append(f"{email}: password could not be carried over; they can use “Forgot password”.")
            user.save()
            self.user_map[row["id"]] = user
            self.counts["users"] += 1

    # --------------------------------------------------------------- cards

    def import_cards(self):
        self.card_map = {}
        paid_users = {r["user_id"] for r in self.rows("payments") if r.get("status") == "success"}
        subs_by_user = Counter(r["user_id"] for r in self.rows("subscriptions"))
        links = {}
        for r in self.rows("social_links", "position"):
            links.setdefault(r["profile_id"], []).append(r)
        for row in self.rows("profiles"):
            user = self.user_map.get(row["user_id"])
            if user is None:
                self.problems.append(f"Card {row['slug']} has no imported owner and was skipped.")
                continue
            existing = Card.objects.filter(legacy_id=row["id"]).first()
            if existing:
                self.card_map[row["id"]] = existing
                self.counts["cards (already there)"] += 1
                continue
            slug = (row["slug"] or "").strip().lower()
            if Card.objects.filter(slug=slug).exists() or CardSlug.objects.filter(slug=slug).exists():
                self.problems.append(f"Slug {slug} already exists here — card NOT imported. Resolve by hand.")
                continue
            activated = aware(row.get("created_at")) if (row["user_id"] in paid_users or subs_by_user[row["user_id"]]) else None
            card = Card(
                owner=user,
                slug=slug,
                full_name=row.get("full_name") or user.email,
                job_title=row.get("job_title") or "",
                business_name=row.get("organisation") or "",
                bio=row.get("bio") or "",
                whatsapp=phone_or_blank(row.get("whatsapp")),
                email=row.get("email") or "",
                website=row.get("website") or "",
                address=row.get("location") or "",
                accent=ACCENT_MAP.get(row.get("accent") or "blue", ACCENT_PRESETS["blue"]),
                is_published=bool(row.get("is_published")) if activated else True,
                activated_at=activated,
                legacy_id=row["id"],
            )
            if row.get("avatar_filename"):
                processed = self.image(
                    [f"/static/img/avatars/{row['avatar_filename']}"], "avatars", 640
                )
                if processed:
                    card.avatar.save(processed.name, processed, save=False)
                    self.counts["card photos copied"] += 1
                elif self.media_base and not self.skip_media:
                    self.problems.append(f"Photo for /c/{slug} could not be downloaded; the customer can re-upload it.")
            card.save()
            Card.objects.filter(pk=card.pk).update(
                created_at=aware(row.get("created_at")) or timezone.now(),
                updated_at=aware(row.get("updated_at")) or timezone.now(),
            )
            CardSlug.objects.create(slug=slug, card=card, is_current=True)
            number = phone_or_blank(row.get("phone"))
            if number:
                CardPhone.objects.create(card=card, number=number, label="Mobile", position=0)
            for position, link in enumerate(links.get(row["id"], [])):
                platform = link["platform"] if link["platform"] in dict(SocialLink.PLATFORMS) else "website"
                SocialLink.objects.create(card=card, platform=platform, url=link["url"][:500], position=position)
                self.counts["social links"] += 1
            if not user.full_name:
                User.objects.filter(pk=user.pk).update(full_name=card.full_name)
            self.card_map[row["id"]] = card
            self.counts["cards"] += 1

    # ------------------------------------------------------- subscriptions

    def import_subscriptions(self):
        best = {}
        for row in self.rows("subscriptions"):
            current = best.get(row["user_id"])
            expires = aware(row["expires_at"])
            if current is None or expires > aware(current["expires_at"]):
                best[row["user_id"]] = row
        for legacy_user, row in best.items():
            user = self.user_map.get(legacy_user)
            if user is None or Subscription.objects.filter(user=user).exists():
                continue
            plan = self.plans[PLAN_MAP.get(row["plan"], "basic")]
            Subscription.objects.create(
                user=user,
                plan=plan,
                started_at=aware(row["starts_at"]) or timezone.now(),
                expires_at=aware(row["expires_at"]),
                cancelled_at=aware(row.get("cancelled_at")),
            )
            self.counts["subscriptions"] += 1

    # ------------------------------------------------------------ payments

    def import_payments(self):
        for row in self.rows("payments"):
            user = self.user_map.get(row["user_id"])
            if user is None or Payment.objects.filter(reference=row["reference"]).exists():
                continue
            status = row["status"] if row["status"] in dict(Payment.STATUSES) else Payment.FAILED
            plan = self.plans.get(PLAN_MAP.get(row.get("plan") or "", ""))
            payment = Payment(
                user=user,
                reference=row["reference"],
                purpose=Payment.PURPOSE_SUBSCRIPTION,
                plan=plan,
                months=12,
                subtotal_minor=row["amount_minor"],
                amount_minor=row["amount_minor"],
                currency=row.get("currency") or "GHS",
                status=status,
                gateway=row.get("gateway") or "paystack",
                channel=row.get("channel") or "",
                gateway_fee_minor=row.get("gateway_fee_minor") or 0,
                paid_at=aware(row.get("paid_at")),
                line_items=[{"label": f"{plan.name if plan else 'Plan'} plan, 12 months", "amount_minor": row["amount_minor"]}],
                legacy_source="flask",
            )
            if status == Payment.SUCCESS:
                payment.receipt_number = f"ADR-F{row['id']:05d}"
                payment.receipt_emailed_at = payment.paid_at  # never re-send old receipts
            payment.save()
            Payment.objects.filter(pk=payment.pk).update(created_at=aware(row["created_at"]))
            self.counts["payments"] += 1

    # ----------------------------------------------------------- analytics

    def import_views(self):
        imported = set(CardEvent.objects.filter(card__legacy_id__isnull=False).values_list("card_id", flat=True).distinct())
        batch = []
        for row in self.rows("card_views"):
            card = self.card_map.get(row["profile_id"])
            if card is None or card.pk in imported:
                continue
            batch.append(
                CardEvent(
                    card=card,
                    kind=CardEvent.SAVE_CONTACT if row.get("action") == "vcf" else CardEvent.VIEW,
                    source=row.get("source") if row.get("source") in ("link", "qr", "nfc") else "link",
                    visitor_hash=(row.get("visitor_hash") or "")[:64],
                    occurred_at=aware(row["viewed_at"]),
                )
            )
            if len(batch) >= 2000:
                CardEvent.objects.bulk_create(batch)
                self.counts["analytics events"] += len(batch)
                batch = []
        if batch:
            CardEvent.objects.bulk_create(batch)
            self.counts["analytics events"] += len(batch)

    def import_leads(self):
        for row in self.rows("leads"):
            card = self.card_map.get(row["profile_id"])
            if card is None or Lead.objects.filter(card=card, created_at=aware(row["created_at"]), name=row["name"]).exists():
                continue
            Lead.objects.create(
                card=card, name=row["name"], phone=row.get("phone") or "", email=row.get("email") or "",
                organisation=row.get("organisation") or "", note=row.get("note") or "",
                source=row.get("source") or "link", visitor_hash=row.get("visitor_hash") or "",
                is_read=bool(row.get("is_read")), created_at=aware(row["created_at"]),
            )
            self.counts["leads"] += 1

    def import_gallery(self):
        for row in self.rows("gallery_images", "display_order"):
            if GalleryImage.objects.filter(legacy_id=row["id"]).exists():
                continue
            processed = self.image(
                [f"/admin/gallery/image/{row['filename']}", f"/static/img/avatars/gallery/{row['filename']}"],
                "gallery", 1600,
            )
            if processed is None:
                self.problems.append(f"Gallery picture {row['filename']} could not be copied; upload it again in Staff › Homepage gallery.")
                continue
            image = GalleryImage(
                title=row.get("title") or "AD Graphics design",
                description=row.get("description") or "",
                display_order=row.get("display_order") or 0,
                is_published=bool(row.get("is_published")),
                action_label="View design",
                legacy_id=row["id"],
            )
            image.image.save(processed.name, processed, save=False)
            image.save()
            self.counts["gallery slides"] += 1

    # ------------------------------------------------------------------ nfc

    def import_nfc(self):
        for row in self.rows("nfc_orders"):
            if NFCOrder.objects.filter(legacy_id=row["id"]).exists():
                continue
            user = self.user_map.get(row["user_id"])
            card = self.card_map.get(row["profile_id"])
            if user is None or card is None:
                self.problems.append(f"NFC order {row['order_number']} has no imported owner or card; skipped.")
                continue
            plan = self.plans.get(PLAN_MAP.get(row.get("digital_plan") or "", "")) if row.get("digital_plan") else None
            paid = row.get("payment_status") == "success"
            order = NFCOrder.objects.create(
                order_number=row["order_number"],
                user=user,
                status=NFC_STATUS_MAP.get(row["status"], NFCOrder.PAYMENT_CONFIRMED if paid else NFCOrder.PENDING_PAYMENT),
                quantity=row["quantity"],
                cards_minor=row["nfc_price_minor"],
                discount_minor=row.get("discount_minor") or 0,
                plan_minor=row.get("digital_plan_price_minor") or 0,
                total_minor=row["total_amount_minor"],
                currency=row.get("currency") or "GHS",
                bundled_plan=plan,
                full_name=row["full_name"],
                position=row.get("position") or "",
                email=row["email"],
                phone=phone_or_blank(row["phone"]) or row["phone"],
                delivery_address=row.get("delivery_location") or "",
                design_description=row.get("design_instructions") or "",
                paid_at=aware(row.get("paid_at")),
                admin_notified_at=aware(row.get("admin_notified_at")) or (timezone.now() if paid else None),
                legacy_order_type=row.get("order_type") or "",
                legacy_replacement_reason=row.get("replacement_reason") or "",
                legacy_id=row["id"],
                created_at=aware(row["created_at"]),
            )
            if row.get("logo_filename"):
                data = self.fetch([f"/static/img/avatars/nfc/{row['logo_filename']}"])
                if data:
                    ext = row["logo_filename"].rsplit(".", 1)[-1].lower()
                    order.logo.save(f"nfc/logos/legacy-{row['id']}.{ext}", ContentFile(data), save=True)
            NFCOrderItem.objects.create(order=order, card=card, quantity=row["quantity"])
            OrderEvent.objects.create(
                order=order, status=order.status, message="Imported from the previous AD Smart website.",
                visible_to_customer=True, created_at=order.created_at,
            )
            reference = row.get("payment_reference")
            if reference and not Payment.objects.filter(reference=reference).exists():
                status = row.get("payment_status") if row.get("payment_status") in dict(Payment.STATUSES) else Payment.FAILED
                payment = Payment.objects.create(
                    user=user, reference=reference, purpose=Payment.PURPOSE_NFC, nfc_order=order, plan=plan,
                    months=12 if plan else 0, subtotal_minor=row["nfc_price_minor"] + (row.get("digital_plan_price_minor") or 0),
                    discount_minor=row.get("discount_minor") or 0, amount_minor=row["total_amount_minor"],
                    currency=row.get("currency") or "GHS", status=status, paid_at=aware(row.get("paid_at")),
                    receipt_number=f"ADR-FN{row['id']:05d}" if status == Payment.SUCCESS else None,
                    receipt_emailed_at=aware(row.get("paid_at")) if status == Payment.SUCCESS else None,
                    line_items=[{"label": f"{row['quantity']} NFC cards", "amount_minor": row["total_amount_minor"]}],
                    legacy_source="flask",
                )
                Payment.objects.filter(pk=payment.pk).update(created_at=aware(row["created_at"]))
            self.counts["nfc orders"] += 1

    # --------------------------------------------------------------- checks

    def verify(self):
        """Every Flask slug must resolve to the same card here."""
        missing = [r["slug"] for r in self.rows("profiles") if not Card.objects.filter(slug=(r["slug"] or "").lower()).exists()]
        if missing:
            self.problems.append(f"{len(missing)} Flask slug(s) do not resolve here: {', '.join(missing[:20])}")
        else:
            self.counts["slugs verified"] = Card.objects.filter(legacy_id__isnull=False).count()
        if self.admin_emails:
            self.problems.append("Flask admin accounts not given staff access: " + ", ".join(self.admin_emails))
