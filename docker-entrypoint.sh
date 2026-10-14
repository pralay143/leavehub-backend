#!/bin/sh
# Container commands:
#   web      serve the app with gunicorn (default)
#   release  apply migrations and create the cache table; run once per deploy
#   other    run as-is, e.g. "python manage.py createsuperuser"
set -e

case "$1" in
  web)
    exec gunicorn config.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-3}" \
      --timeout "${GUNICORN_TIMEOUT:-30}" \
      --access-logfile - \
      --error-logfile -
    ;;
  release)
    python manage.py migrate --noinput
    python manage.py createcachetable
    ;;
  *)
    exec "$@"
    ;;
esac
