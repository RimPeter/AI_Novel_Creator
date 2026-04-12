# AI Novel Creator

## Dev Services

Create `.env` from `.env.example` and set `OPENAI_API_KEY`.

For account emails, set `SITE_DOMAIN` and `SITE_NAME` so confirmation and recovery messages use the correct branding and host.

## Stripe Billing

The app already includes hosted Stripe Checkout, the Stripe customer portal, webhook processing, and billing gating for AI features.

Required environment variables:

- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_MONTHLY`
- `STRIPE_PRICE_YEARLY`
- `STRIPE_PRICE_SINGLE_MONTH`
- `STRIPE_PRICE_TRIAL_WEEK`

Use a real `whsec_...` value for `STRIPE_WEBHOOK_SECRET`. Placeholder strings such as `NONE`, `null`, or `undefined` are treated as unset and will keep billing disabled.

Configured plans:

- Monthly subscription: `£15`
- Yearly subscription: `£100`
- One month pass: `£20`
- One week trial: `£5`

Routes:

- Billing page: `/billing/`
- Checkout start: `/billing/checkout/`
- Billing portal: `/billing/portal/`
- Webhook endpoint: `/billing/webhook/`
- Home memo board source: `main/data/home_updates.json`

Local webhook forwarding with the Stripe CLI:

```powershell
stripe listen --forward-to http://127.0.0.1:8010/billing/webhook/
```

Copy the signing secret from the Stripe CLI output into `STRIPE_WEBHOOK_SECRET`.

### 1) Start Redis

If you have Docker Desktop:

```powershell
docker compose up -d redis
```

Or run any Redis server locally on `127.0.0.1:6379`.

Defaults:

- Broker: `redis://127.0.0.1:6379/0`
- Results: `redis://127.0.0.1:6379/1`

Override with `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`.

### 2) Email in development

By default, account emails are written to `.local_mail/` in the repo. This includes:

- Sign-up confirmation
- Email sign-in codes
- Password reset links
- Password change notifications

If you prefer a browser inbox, start Mailpit:

```powershell
docker compose up -d mailpit
```

Then set the SMTP values from `.env.example` and open `http://127.0.0.1:8025`.

### 3) Start the worker + web server

```powershell
.\venv05012026\Scripts\activate
python manage.py migrate
celery -A novel_creator worker -l info
```

In another terminal:

```powershell
.\venv05012026\Scripts\activate
python manage.py runserver 127.0.0.1:8010
```

### 4) Quick smoke test

```powershell
.\venv05012026\Scripts\activate
python manage.py celery_ping
```

On Windows, the project defaults Celery to the `solo` worker pool (required for Celery on Windows).

## Heroku

The repo now includes the two files Heroku expects at the app root:

- `.python-version`
- `Procfile`

Set these config vars before deploying:

- `OPENAI_API_KEY`
- `SECRET_KEY`
- `DEBUG=False`
- `ALLOWED_HOSTS=your-app.herokuapp.com`
- `CSRF_TRUSTED_ORIGINS=https://your-app.herokuapp.com`
- `SITE_DOMAIN=your-app.herokuapp.com`
- `SITE_NAME=AI Novel Creator`
- `SECURE_HSTS_SECONDS=3600`

Optional but recommended:

- `DATABASE_URL` for Heroku Postgres
- SMTP settings if you want real account emails instead of the file backend

Attach Heroku Postgres and verify:

```powershell
heroku addons:create heroku-postgresql:essential-0 -a ai-novel-manager
heroku config:get DATABASE_URL -a ai-novel-manager
heroku pg:info -a ai-novel-manager
```

Important for static files on Heroku:

```powershell
heroku config:unset DISABLE_COLLECTSTATIC -a ai-novel-manager
```

Run migrations on the app:

```powershell
heroku run -a ai-novel-manager -- python manage.py migrate
```

Sync home updates from the committed JSON source:

```powershell
python manage.py sync_home_updates
```

JSON schema for each entry in `main/data/home_updates.json`:

```json
{
  "source_key": "stable-unique-key",
  "date": "2026-04-12",
  "title": "Short user-facing title",
  "body": "Optional details shown on the home page."
}
```

Deployment notes:

- The `release` process runs `python manage.py migrate`.
- The `release` process then runs `python manage.py sync_home_updates`.
- The `web` process runs `gunicorn novel_creator.wsgi`.
- Static files are served with WhiteNoise, so `DISABLE_COLLECTSTATIC` should be unset on Heroku.
