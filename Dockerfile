# LeaveHub backend: Django served by gunicorn.
#
# Build:   docker build -t leavehub-backend .
# Release: docker run --rm --env-file .env.production leavehub-backend release
# Run:     docker run -p 8000:8000 --env-file .env.production leavehub-backend

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Collect static files at build time. These dummy values are only used for
# this step; real settings come from the environment at runtime.
RUN DEBUG=False DJANGO_SECRET_KEY=build-only-not-secret ALLOWED_HOSTS=localhost \
    DATABASE_URL=sqlite:////tmp/build.sqlite3 EMAIL_MODE=console \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 app && chmod +x docker-entrypoint.sh
USER app

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
