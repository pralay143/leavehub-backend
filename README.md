# LeaveHub Backend

The backend for LeaveHub, an employee leave management system. The planned
stack is Python, Django REST Framework, and PostgreSQL. The frontend is in the
separate [leavehub-frontend](https://github.com/pralay143/leavehub-frontend)
repository.

## Status

Repository setup only. Django application code, dependency manifests, and
database migrations have not been created yet.

## Planned stack

- Python and Django REST Framework for the API
- PostgreSQL for persistent data

## Local development

1. Install Python and PostgreSQL.
2. Create a local PostgreSQL database and user.
3. Create and activate a virtual environment (`py -m venv .venv` on Windows,
   or `python3 -m venv .venv` on macOS/Linux).
4. Copy `.env.example` to `.env` and set local configuration values.
5. Once the Django project and dependency manifest have been added, install
   its dependencies, apply migrations, and start the development server using
   the documented project commands.

Keep real secrets and local database files out of Git. The repository
`.gitignore` excludes environment files, virtual environments, and local
database artifacts.
