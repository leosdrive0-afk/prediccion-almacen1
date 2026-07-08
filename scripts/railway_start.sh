#!/usr/bin/env bash
set -euo pipefail

echo "=== Railway startup ==="

echo "=== Running migrations ==="
python manage.py migrate --noinput

if [ "${RUN_SEED:-0}" = "1" ]; then
  echo "=== Running seed_operaciones ==="
  python manage.py seed_operaciones
else
  echo "=== Skipping seed_operaciones ==="
fi

if [ "${DOWNLOAD_MODEL:-1}" = "1" ]; then
  echo "=== Downloading ML model ==="
  python scripts/ensure_model.py
else
  echo "=== Skipping ML model download ==="
fi

echo "=== Starting Gunicorn ==="
exec gunicorn desercion_escolar.wsgi:application \
  --bind 0.0.0.0:${PORT:-8000} \
  --workers 1 \
  --timeout 180