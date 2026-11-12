#!/usr/bin/env bash
# Render start step: wait for the database, migrate, ensure base setup / admin, optionally seed demo data, serve.
set -o errexit

# The database can be briefly unavailable (e.g. while Render restarts or upgrades it). Retry instead of crashing.
for attempt in $(seq 1 30); do
  if python manage.py migrate --no-input; then
    break
  fi
  if [ "$attempt" = "30" ]; then
    echo "Database still unavailable after 30 attempts; giving up." >&2
    exit 1
  fi
  echo "Database not ready (attempt $attempt/30); retrying in 5s..." >&2
  sleep 5
done

if [ "${SEED_DEMO:-0}" = "1" ]; then
  python manage.py load_product_setup   # idempotent: sample item classes / lookups / HTS table
  python manage.py seed_demo --if-empty --admin "${ADMIN_USERNAME:-admin}" --password "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
else
  python manage.py setup_erp --admin "${ADMIN_USERNAME:-admin}" --password "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
fi
exec gunicorn synqaro.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 120 --access-logfile - --error-logfile -
