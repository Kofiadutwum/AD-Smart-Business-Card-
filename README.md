# AD Smart Business Cards — Django

Digital business cards shared by permanent link, QR code or NFC tap, with
yearly plans paid through Paystack and customised NFC card orders. This is
the Django rebuild of the Flask site (`cardhub`), built to **SRS v2.2**.

## Run it locally

```bash
py -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # then set SECRET_KEY
python manage.py migrate
python manage.py createcachetable
python manage.py seed_initial --gallery   # plans, NFC tiers, delivery zones, gallery, today's USD rate
python manage.py createsuperuser          # your staff login (Main Admin)
python manage.py runserver
```

- Site: http://127.0.0.1:8000
- Staff area: http://127.0.0.1:8000/staff/login (you set up an authenticator app at first sign-in)
- Emails print to the console until `RESEND_API_KEY` or `GMAIL_REFRESH_TOKEN` is set.
- Payments run in **sandbox** (no money moves) until a Paystack secret key is set and `PAYMENT_SANDBOX=0`.

Tests (41, covering the SRS acceptance criteria):

```bash
python manage.py test tests
```

## What lives where

| Path | What |
|---|---|
| `apps/marketing` | Home (hero, About, How it works, gallery, pricing, NFC, FAQ), pricing, NFC, FAQ and policy pages |
| `apps/cards` | Public card `/c/<slug>`, vCard, QR, lead capture, reports, analytics beacon, slug rules |
| `apps/accounts` | Sign-up, email verification, login, 6-digit email recovery, Google sign-in, Werkzeug password import |
| `apps/billing` | Plans, subscriptions, Paystack hosted checkout, webhook, receipts (PDF), promo codes, USD rate |
| `apps/nfc` | NFC price tiers, delivery zones, orders, design proofs, approvals, production workflow |
| `apps/dashboard` | Customer dashboard, card editor with live preview, analytics, contacts, team cards |
| `apps/support` | Contact form and support tickets |
| `apps/staff` | Staff area with role permissions, 2FA, audit log, finance exports, gallery management |
| `apps/core` | Settings, audit log, email log, storage, daily job, seed and Flask import commands |
| `static/css`, `static/js` | Hand-written CSS/JS. Brand tokens in `tokens.css`. No build step. |

The hover button (`.ihb`, `components.css`) and the gallery's squeeze carousel
(`carousel.css`, `carousel-squeeze.js`) are vanilla ports of the supplied
React/shadcn components.

## Everyday admin (no developer needed)

Staff › Settings, Plans, and NFC prices & delivery cover the values the SRS
says the business must be able to change (ADM-04): grace period, reminder
schedule, revision rounds, tax, prices, NFC tiers and delivery fees. The
homepage gallery is managed in Staff › Homepage gallery.

## Running on Render's free plan

The free plan has no cron and blocks outgoing SMTP, so two stand-ins run
until the plans are upgraded:

- **Email from Gmail.** Add `http://localhost:8765/` as a redirect URI on the
  Google OAuth client, enable the Gmail API in the same Google Cloud project,
  then run `python manage.py connect_gmail` on your PC. Put the token it
  prints in Render as `GMAIL_REFRESH_TOKEN` and set `DEFAULT_FROM_EMAIL` to
  `AD Smart Business Cards <the-gmail-address>`. Gmail allows about 500
  emails a day. Once a domain is verified in Resend, set `RESEND_API_KEY`
  (it takes priority) and remove the Gmail token.
- **Daily job from GitHub.** `.github/workflows/daily-jobs.yml` calls
  `POST /internal/daily-jobs` at 06:00 with `CRON_SECRET`. Copy the value
  Render generated for `CRON_SECRET` into the repository's Actions secrets.
  Card states (active, grace, inactive) do not depend on it; reminders,
  notices, the USD rate and housekeeping do.

The free database is **deleted 30 days after it was created**. Upgrade it
(Render › adsmart-db › Upgrade) before then, or everything in it is lost.

## Going live — replacing the Flask site without breaking a single card

Printed QR codes and NFC tags contain the **old site's address** plus
`/c/<slug>`. The Django site keeps both exactly, so the cut-over must keep the
same hostname.

1. **Accounts.** Create (in the business's name): a Cloudinary account, a
   Resend account with the business domain verified (SPF/DKIM/DMARC), and keep
   the existing Paystack and Google OAuth apps.
2. **Staging.** Deploy this repo on Render with `render.yaml` (new Postgres
   `adsmart-db`, web service, daily cron — all in **Frankfurt**, Render's
   closest region to Ghana). Set the secrets in the dashboard.
   Set `SITE_URL` to the **final public address** (not the staging one).
3. **Rehearse the import** from the staging service's Shell, reading the Flask
   database (its *external* connection string) and the live Flask site for
   pictures:

   ```bash
   python manage.py import_flask --source "postgres://…flask…" --media-base https://<current-site>
   ```

   It prints what it would import and anything to check. Nothing is saved.
4. **Maintenance window.** Pause changes on the Flask site (or accept that
   anything changed after the import will need re-entering), then run the same
   command with `--commit`. It is safe to run again; existing rows are skipped.
   It confirms that every Flask slug resolves.
5. **Switch the address.**
   - *If the Flask site has a custom domain:* move the domain from the Flask
     service to `adsmart-web` in Render.
   - *If people only have the `…onrender.com` address:* in the **existing
     Flask service**, point it at this repo, set the build command to
     `bash build.sh`, the start command to
     `gunicorn config.wsgi:application --workers 3 --timeout 60`, and copy the
     environment variables from `adsmart-web` (including `DATABASE_URL` of
     `adsmart-db`). The address stays the same. Render cannot move an
     existing service to another region, so if the Flask service is not in
     Frankfurt, create `adsmart-db` in the Flask service's region instead —
     the database should sit next to the web service.
6. **Check** a few cards by scanning real QR codes and tapping real NFC cards
   (old tags use `?s=nfc`, which is still counted as an NFC visit), log in as
   a customer, and make one small live payment. Paystack's webhook
   (`/billing/webhook`) and Google's callback (`/auth/google/callback`) keep
   their paths.
7. **Keep the Flask database** for at least 90 days as a backup.

Passwords carry over: customers log in with the same password, which is
re-hashed with Argon2 on first login. Flask admin accounts are deliberately
not given staff access — create staff in Staff › Administrators.

## Delivery and international shipping

NFC orders can be picked up, delivered in Ghana, or shipped abroad. This
replaces SRS DLV-02 ("no international delivery in version 1") at the
business's request. International zones (West Africa, Rest of Africa, UK and
Europe, USA/Canada and rest of the world) charge the customer a shipping and
delivery fee per order; import duties are the recipient's. Orders abroad
collect country, city, postcode and an international phone number. The fees
seeded are **placeholders** — set real ones in Staff › NFC prices & delivery,
where a zone can also be added, renamed or switched off.

## Before launch (from SRS §32)

Confirm the proposed values (grace period, reminder schedule, revision rounds,
refund percentages, delivery fees, tax), have the Terms, Privacy and Refund
pages reviewed (they are marked *draft*), and set `PAYMENT_SANDBOX=0` with
live Paystack keys only in production. `auto_purge_enabled` (deleting data of
long-inactive cards) is off until the business switches it on.
