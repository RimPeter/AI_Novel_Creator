# AI Novel Creator

## Dev Services

Create `.env` from `.env.example` and set `OPENAI_API_KEY`.

For account emails, set `SITE_DOMAIN` and `SITE_NAME` so confirmation and recovery messages use the correct branding and host.

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
