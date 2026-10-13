# LeaveHub Backend

REST API for LeaveHub, an employee leave management system. Employees request
leave, managers and HR approve or reject it, and HR manages users, departments,
leave types and yearly balances.

The web app is in the separate
[leavehub-frontend](https://github.com/pralay143/leavehub-frontend) repository.

## Stack

- Python 3.13, Django 6.1, Django REST Framework 3.18
- PostgreSQL (developed against 16) via psycopg 3
- JWT authentication with djangorestframework-simplejwt
- OpenAPI schema and Swagger UI with drf-spectacular

## Features

- **Email login** with JWTs. Refresh tokens are single-use (rotated and
  blacklisted), logout blacklists the refresh token, and changing a password
  invalidates existing tokens. Login is rate-limited to 10 attempts a minute.
- **Roles:** Employee, Manager and HR. Superusers have HR permissions.
  Each user can have a manager and a department.
- **Leave types** (Earned 18, Sick 12, Casual 6 and Unpaid by default) and
  **yearly balances** per employee and leave type.
- **Leave requests** with these rules:
  - only weekdays count; a half day is allowed for a single date;
  - a request must stay within one calendar year;
  - requests cannot overlap the employee's pending or approved leave;
  - available days are `allocated - used - pending`, and the balance is
    checked again when a request is approved;
  - balance changes run in a transaction with row locks.
- **Approval routing:** to the employee's manager; HR staff and employees
  without an active manager go to the other HR users, then to superusers.
  Nobody can approve their own request. Rejecting requires a comment.
  Employees can cancel pending leave, or approved leave before it starts
  (the days are returned).

## Local setup

The commands below are for Windows PowerShell. On macOS/Linux use
`python3 -m venv .venv`, `source .venv/bin/activate` and `python`.

### 1. PostgreSQL

Create a database user and database (run in `psql` as the `postgres` user):

```sql
CREATE ROLE leavehub_user WITH LOGIN PASSWORD 'choose-a-password' CREATEDB;
CREATE DATABASE leavehub_db OWNER leavehub_user ENCODING 'UTF8';
```

`CREATEDB` is needed so the test runner can create its temporary database.

### 2. Python environment

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Configuration

Copy `.env.example` to `.env` and fill it in. `.env` is ignored by Git.

| Variable | Description |
|---|---|
| `DJANGO_SECRET_KEY` | Required. Generate one, e.g. `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DEBUG` | `True` for local development (also makes `/api/docs/` public) |
| `ALLOWED_HOSTS` | Comma-separated host names |
| `DATABASE_URL` | `postgresql://leavehub_user:<password>@localhost:5432/leavehub_db` (URL-encode special characters in the password) |
| `CORS_ALLOWED_ORIGINS` | The frontend origin, `http://localhost:5173` for Vite |
| `JWT_ACCESS_MINUTES` | Access token lifetime, default 15 (1–1440). Raise it locally for manual API testing |

### 4. Database, admin user and balances

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py allocate_leave_balances --year 2026
```

`createsuperuser` asks for an email, a username and a password; you log in
with the email. The migrations create the default leave types.

### 5. Run

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

- API: http://127.0.0.1:8000/api/
- Swagger UI: http://127.0.0.1:8000/api/docs/ (log in with
  `POST /api/auth/login/`, then paste the `access` token into **Authorize**)
- OpenAPI schema: http://127.0.0.1:8000/api/schema/
- Django admin: http://127.0.0.1:8000/admin/

## API overview

All endpoints except login and refresh need `Authorization: Bearer <access token>`.
List endpoints return 20 results per page; pass `?page_size=` for up to 100.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/auth/login/`, `refresh/`, `logout/` | Anyone | Get, rotate and revoke tokens |
| `GET /api/auth/me/` | Logged in | Current user, role, manager and department |
| `/api/departments/`, `/api/leave-types/` | Read: everyone. Write: HR | Reference data (deactivate, never delete) |
| `/api/users/` | HR | Create users with an initial password, edit role, manager, department and status |
| `/api/leave-balances/` | Own balances; HR sees all | Allocated, used, pending and available days; HR edits `allocated` |
| `POST /api/leave-balances/allocate/` | HR | Create missing balances for a year (supports `dry_run`) |
| `/api/leave-requests/` | Logged in | Create and list requests: `?scope=mine` (default), `team`, `review` or `all` (HR) |
| `POST /api/leave-requests/{id}/approve/`, `reject/`, `cancel/` | Reviewer / employee | Workflow actions |

Rule violations return `400 {"detail": "..."}`; records outside your access
return `404`. The full reference is in Swagger UI.

## Project layout

```
accounts/   User (email login, role, manager, department), Department, auth and user endpoints
leaves/     LeaveType, LeaveBalance, LeaveRequest, the leave service and its endpoints
  services.py   Leave rules, approval routing and yearly allocation
  management/commands/allocate_leave_balances.py
config/     Settings, URLs, pagination, shared viewset helpers, error handler
```

## Common commands

| Command | Purpose |
|---|---|
| `manage.py test leaves` | Run the leave service tests |
| `manage.py allocate_leave_balances --year 2027 [--dry-run]` | Create next year's balances (safe to re-run) |
| `manage.py changepassword <email>` | Reset a user's password |
| `manage.py spectacular --validate --file schema.yaml` | Check the OpenAPI schema |

After changing the API, regenerate the frontend client with
`npm run api:generate` in the frontend repository while this server is running.

## Security notes

- Never commit `.env`, real secret keys or database dumps; `.gitignore`
  excludes them.
- `DEBUG=True` and the open API docs are for local development only.
