# AI Novel Creator

## Dev Services

Create `.env` from `.env.example`. Install dependencies with `python -m pip install -r requirements.txt`.

## Bring your own API key

Users connect OpenAI (GPT), Anthropic (Claude), or both at `/ai-settings/`.
The selected writing model determines the text provider. Image generation and editing
use the user's OpenAI key. Reading, editing, and image uploads need no API key.
Provider usage is billed directly to the user's provider account; app plans are retired.

Before starting the app:

1. Generate an encryption secret with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set `AI_CREDENTIAL_ENCRYPTION_KEY` in the deployment secret store (or local `.env`).
   Keep it separate from database backups and stable across all web/worker processes.
   Losing or changing it makes existing keys unreadable; users must reconnect.
3. Run `python manage.py migrate`.
4. Sign in, open AI Settings, and verify/save a provider key. Select a writing model.

Keys are encrypted at rest, masked in the UI, and resolved for the authenticated user
at request time. The app never falls back to a server `OPENAI_API_KEY`.
Connection tests list provider models without generating content; successful authentication
does not guarantee model access or available credit. Network errors and quota errors are
shown without exposing provider response bodies. Removal takes effect for subsequent
calls; a request already sent to a provider cannot be recalled.

API calls outside HTTP requests must run inside `main.ai_credentials.ai_user_context(user)`.
Never pass plaintext keys through Celery arguments. Existing Celery bible/outline/scene
tasks produce local templates and do not call providers; this behavior is unchanged.

Integration references: [OpenAI authentication](https://developers.openai.com/api/reference/overview)
and [Claude Messages](https://platform.claude.com/docs/en/api/messages/create).

## Legacy Stripe transition

New checkout and plan selection are disabled. `/billing/` redirects to AI Settings.
`/billing/history/` retains invoices and subscription management. Webhooks remain active
for existing payments and subscriptions. Keep the existing Stripe configuration during
the transition. Removing app plans does **not** cancel subscriptions in Stripe.

Before production rollout, review active recurring subscriptions and prepaid access,
communicate the transition, decide cancellation/refund handling, and reconcile Stripe.
Do not remove legacy invoice tables or historical migrations. After reconciliation,
retire unused Stripe secrets and integrations separately.

For account emails, set `SITE_DOMAIN` and `SITE_NAME` so confirmation and recovery
messages use the correct branding and host.

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

The Recent updates board does not read Git history. For each user-facing release,
add an entry to `main/data/home_updates.json` with a unique `source_key`, date,
title, and description, and include that file in the commit. Run the command below
to refresh the local board. Heroku runs the same sync during release after the
updated file is deployed. Running `migrate` or `runserver` alone does not sync it.

```powershell
python manage.py sync_home_updates
```

This sync is non-destructive by default. To remove JSON-managed DB rows that are no longer in the file:

```powershell
python manage.py sync_home_updates --prune-missing
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
