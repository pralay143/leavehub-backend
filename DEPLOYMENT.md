# Deploying LeaveHub

LeaveHub is two deployable parts:

- **Backend** (this repository): a Django API served by gunicorn, with a
  PostgreSQL database. Docker image built from `Dockerfile`.
- **Frontend** ([leavehub-frontend](https://github.com/pralay143/leavehub-frontend)):
  static files served by nginx. Docker image built from its `Dockerfile`.

Both images work on a single server with Docker, or on managed platforms
(for example a container host plus managed PostgreSQL for the backend, and a
static or container host for the frontend).

## 1. Prepare

- A PostgreSQL database and user (version 16 is what LeaveHub is developed on).
- An SMTP account for email. In production Django refuses the console email
  backend. For Gmail, create an app password.
- HTTPS in front of both parts: a platform's built-in TLS, or a reverse proxy
  such as Caddy or nginx.
- Domains, for example `leavehub.example.com` (frontend) and
  `api.leavehub.example.com` (backend), or one domain with `/api` routed to the
  backend.

## 2. Backend configuration

Set these as environment variables (or an env file that is never committed).

| Variable | Production value |
|---|---|
| `DEBUG` | `False` |
| `DJANGO_SECRET_KEY` | A new random value of 50+ characters, unique to production. Generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `ALLOWED_HOSTS` | The backend host names, e.g. `api.leavehub.example.com` |
| `DATABASE_URL` | `postgresql://user:password@host:5432/dbname` |
| `CORS_ALLOWED_ORIGINS` | The frontend origin, e.g. `https://leavehub.example.com` |
| `CSRF_TRUSTED_ORIGINS` | The backend's own HTTPS origin, for the Django admin, e.g. `https://api.leavehub.example.com` |
| `BEHIND_HTTPS_PROXY` | `True` when a proxy or platform terminates HTTPS and sets `X-Forwarded-Proto`. Leave unset otherwise |
| `EMAIL_MODE` | `smtp` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | Your SMTP account |
| `DEFAULT_FROM_EMAIL` | e.g. `LeaveHub <leave@example.com>` |
| `FRONTEND_URL` | e.g. `https://leavehub.example.com` (used in email links) |

Optional:

| Variable | Default | Notes |
|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` | Redirect HTTP to HTTPS. `/api/health/` is exempt |
| `SECURE_HSTS_SECONDS` | `3600` | Raise to `31536000` (one year) once HTTPS works everywhere |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD` | `False` | Affect every subdomain and browser preload lists; enable only deliberately |
| `JWT_ACCESS_MINUTES` | `15` | Keep short in production |
| `WEB_CONCURRENCY` | `3` | gunicorn worker processes |
| `PORT` | `8000` | Port gunicorn listens on |
| `LOG_LEVEL` | `INFO` | Logs go to standard output |

Check the configuration before going live:

```sh
docker run --rm --env-file .env.production leavehub-backend python manage.py check --deploy
```

It should report no issues. (Two HSTS checks are silenced on purpose; see the
table above.)

## 3. Build and release the backend

```sh
docker build -t leavehub-backend .

# Once per deploy: apply migrations and create the cache table.
docker run --rm --env-file .env.production leavehub-backend release

# First deploy only:
docker run --rm -it --env-file .env.production leavehub-backend python manage.py createsuperuser
docker run --rm --env-file .env.production leavehub-backend python manage.py allocate_leave_balances --year 2026

# Serve:
docker run -d -p 8000:8000 --env-file .env.production leavehub-backend
```

The image runs as a non-root user, serves static files (admin and API docs)
itself with WhiteNoise, and exposes `GET /api/health/`, which returns
`{"status": "ok"}`, or HTTP 503 if the database is unreachable. Use it as the
platform's health check.

## 4. Build and serve the frontend

The API URL is fixed at build time, so build once per environment:

```sh
docker build -t leavehub-frontend \
  --build-arg VITE_API_BASE_URL=https://api.leavehub.example.com .
docker run -d -p 8080:8080 -e API_ORIGIN=https://api.leavehub.example.com leavehub-frontend
```

`API_ORIGIN` is allowed in the page's Content-Security-Policy so the app can
call the API. See the frontend README for details.

## 5. After going live

- Log in to the web app and the Django admin over HTTPS.
- Each year, create balances for the new year:
  `python manage.py allocate_leave_balances --year <year>` (safe to re-run).
- Back up PostgreSQL regularly (for example `pg_dump`), and test restoring.
- Emails are sent during the request after the database commit, with a
  10-second SMTP timeout. If sending ever becomes slow, move it to a
  background worker.

## Production checklist

- [ ] `DEBUG=False` and a new, unique `DJANGO_SECRET_KEY`
- [ ] `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` and `FRONTEND_URL` set to the real HTTPS addresses
- [ ] HTTPS in front of both parts; `BEHIND_HTTPS_PROXY=True` if a proxy terminates it
- [ ] `EMAIL_MODE=smtp` with working credentials (send yourself a test request)
- [ ] `manage.py check --deploy` reports no issues
- [ ] `release` run after every deploy
- [ ] Health check pointed at `/api/health/`
- [ ] Database backups scheduled
- [ ] Secrets stored only in the platform's secret settings or an uncommitted env file
