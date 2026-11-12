#!/usr/bin/env bash
# Render start step: migrate, ensure base setup / admin, optionally load demo data, then serve.
set -o errexit
python manage.py migrate --no-input
if [ "${SEED_DEMO:-0}" = "1" ]; then
  python manage.py seed_demo --if-empty --admin "${ADMIN_USERNAME:-admin}" --password "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
else
  python manage.py setup_erp --admin "${ADMIN_USERNAME:-admin}" --password "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
fi
exec gunicorn synqaro.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --timeout 120
