# NABISWA WIFI — Billing & Hotspot Management Platform

Phase 1 (Foundation) of a from-scratch Wi-Fi billing system: Django + DRF +
Supabase Postgres, built to eventually drive M-Pesa (Daraja) payments and a
MikroTik HotSpot captive portal.

## What's in Phase 1

- Project skeleton (`config/`), 7 apps under `apps/`, DRF `api/` router
- Full Phase-1 schema: `User`, `Role`, `Permission`, `StaffProfile`,
  `Customer`, `Device`, `InternetPackage`, `PackageProfile`, `Payment`,
  `Subscription`, `MikroTikRouter`, `MikroTikProfile`, `InternetSession`,
  `Voucher`, `VoucherBatch`, `SystemSettings`, `AuditLog`, `Notification`
- `MikroTikService` abstraction (`apps/mikrotik/services.py`) — currently
  backed by `NullMikroTikBackend`, which is honest about "MikroTik not
  connected" rather than faking data (Section 36)
- Billing engine core: `Subscription.activate_from_payment()` implements the
  Section 12 renewal rule (default: **EXTEND** — never lose purchased time)
- Light/dark theme system using the exact palettes supplied, CSS variables,
  mobile-first responsive tables
- Staff login + placeholder dashboard, customer captive-portal landing page
  pulling packages from the database (no hard-coded prices)
- Seed fixture for the 8 starter packages: `apps/packages/fixtures/initial_packages.json`

## Phase 2 additions (Billing)

- DRF viewsets wired into `/api/`: `packages/`, `customers/` (staff CRUD +
  `suspend`/`reactivate`/`disconnect_session` actions), `payments/` and
  `subscriptions/` (read-only — both are only ever mutated by the billing
  engine or the future Daraja callback, never by direct API writes)
- `apps/core/permissions.py` — `HasRolePermission` enforces the Section 24
  RBAC codenames (`manage_customers`, `manage_packages`, `view_payments`, …)
  against a staff user's `Role`
- `apps/core/audit.py` — one `log_action()` helper used by every mutating
  staff action, so `AuditLog` rows are consistent (Section 25)
- Real purchase flow: package card → phone number → `Payment(PENDING)` is
  created and the customer is dropped on a waiting page that polls
  `/billing/payment/<id>/status/` — `_trigger_stk_push()` in
  `apps/billing/views.py` is the single, clearly-marked spot where Phase 3
  wires in the actual Daraja call
- Admin dashboard (Section 20), Payments page (Section 21, with filters +
  CSV export), and Subscriptions page (Section 22) now run real queries
  against `Payment`/`Subscription`/`Customer`/`InternetSession`
- Customer dashboard (Section 27) shows real active subscription, session,
  devices, and payment history

## What's intentionally NOT in Phase 1

Per the phased plan: Daraja STK Push/callback wiring, real MikroTik
`RouterOSBackend`, vouchers UI flows, reports, and RBAC enforcement in views
all come in Phases 2-5. The models, service interfaces, and URL surface for
all of them already exist so nothing has to be re-architected later.

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL (Supabase), DJANGO_SECRET_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py loaddata apps/packages/fixtures/initial_packages.json
python manage.py runserver
```

Visit `/` for the customer portal, `/admin-portal/login/` for staff login,
`/django-admin/` for Django's built-in admin.

## Deploying to Vercel — and the one thing Vercel can't do

Vercel runs `config/wsgi.py` as a serverless function (see `vercel.json`).
That's fine for every request/response flow: STK push initiation, the M-Pesa
callback, the customer portal, the admin dashboard.

**It cannot run anything long-lived or scheduled**, and this system needs two
such things:

1. **Subscription expiry** — something has to flip `ACTIVE` subscriptions to
   `EXPIRED` (and call `MikroTikService.disable_user()`) the moment they lapse.
2. **MikroTik state sync / persistent connection** — polling active sessions,
   retrying activation if the router was offline when payment succeeded
   (Section 32).

**Recommended architecture:** a small always-on process — a $5-6/mo VPS,
Fly.io machine, or Railway worker — running a scheduler (cron, or a simple
`while True` loop with `time.sleep`) that either:
- runs `python manage.py expire_subscriptions` and `sync_mikrotik` directly
  against the same Supabase database, or
- calls two protected endpoints (`/api/internal/expire/`,
  `/api/internal/mikrotik-sync/`) on the Vercel app, authenticated with
  `INTERNAL_TASK_TOKEN`.

Either way this worker is the **only** process that needs a persistent
MikroTik API connection — Vercel functions stay stateless and short-lived,
exactly as required.

This is flagged now, in Phase 1, rather than discovered later.
