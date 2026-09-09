# Deploying to Railway

This replaces the Vercel setup. Railway can run the two things Vercel
couldn't: a persistent worker process (`worker.py`) and, if you ever want
it, a straight Postgres add-on (though you can also keep Supabase — the
`DATABASE_URL` env var is all that matters, Railway doesn't care where
Postgres lives).

## 1. Push this repo to GitHub (if it isn't already)

Railway deploys from a GitHub repo, so make sure everything in this
message's file list is committed and pushed.

## 2. Create the Railway project

1. https://railway.app → New Project → Deploy from GitHub repo → pick this repo.
2. Railway auto-detects Python via `nixpacks.toml` / `requirements.txt` and
   the `Procfile`. It will create one service by default — that's your
   `web` process (gunicorn).
3. Add a **second service** from the same repo for the worker: New →
   GitHub Repo → same repo again → in that service's Settings → Deploy,
   set **Custom Start Command** to `python worker.py` (Railway can only
   auto-pick the first Procfile entry per service, so the second service
   needs this set explicitly).

## 3. Environment variables

Set these on **both** services (web and worker) — Railway lets you share
a variable group across services, which is worth doing since most of
these are identical:

| Variable | Example | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | (long random string) | generate with `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `False` | |
| `ALLOWED_HOSTS` | `your-app.up.railway.app` | comma-separated if you add a custom domain |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.up.railway.app` | must include `https://` |
| `DATABASE_URL` | `postgres://...` | your Supabase (or Railway Postgres) connection string |
| `DEFAULT_TIMEZONE` | `Africa/Nairobi` | |
| `CORS_ALLOWED_ORIGINS` | (blank unless you have a separate frontend) | |
| `MPESA_ENV` | `production` when you go live, `sandbox` until then | |
| `MPESA_CONSUMER_KEY` / `MPESA_CONSUMER_SECRET` | from your Daraja app | |
| `MPESA_SHORTCODE` | your Paybill/Till HO number | |
| `MPESA_TILL_NUMBER` | leave blank if Paybill, set if Buy Goods Till | |
| `MPESA_PASSKEY` | from Daraja | |
| `MPESA_CALLBACK_URL` | `https://your-app.up.railway.app/api/mpesa/callback/` | Safaricom posts here |
| `MPESA_ACCOUNT_TYPE` | `TILL` or `PAYBILL` | |
| `INTERNAL_TASK_TOKEN` | (long random string) | only needed if you later expose the `/api/internal/...` HTTP variant instead of the worker |
| `MIKROTIK_AGENT_API_KEY` | (long random string) | must match the on-site agent's `.env` — see `agent/README.md` |

`PORT` is set automatically by Railway — don't set it yourself.

## 4. First deploy

Railway runs the `release:` line in the Procfile (`migrate --noinput`)
before every deploy, and `nixpacks.toml`'s build phase runs
`collectstatic` during the build. Once both services show "Active":

```bash
# from your local machine, pointed at the same DATABASE_URL, or via
# Railway's own shell (railway run ...):
python manage.py createsuperuser
python manage.py loaddata apps/packages/fixtures/initial_packages.json
```

Visit `https://your-app.up.railway.app/` for the customer portal,
`/admin-portal/login/` for staff, `/django-admin/` for Django admin.

## 5. Set the M-Pesa callback in Daraja

In your Daraja app config, set the callback URL to
`https://your-app.up.railway.app/api/mpesa/callback/`.

## 6. Connect the RB941

The web/worker services above are the cloud side. The router itself is on
your local network and needs the on-site agent — that's a **separate**
setup, on a **separate** always-on machine near the router. Full
instructions: `agent/README.md`.

## 7. Vercel cleanup

`vercel.json` is now unused — safe to delete, or leave it (Railway
ignores it). If the Vercel project is still live, remove/disable it so
you don't end up running the same app on both platforms with two
different databases pointed at by mistake.
