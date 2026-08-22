#!/usr/bin/env bash
# start.sh — Render start command wrapper
# Runs Flask-Migrate upgrade before starting gunicorn.
# This ensures schema is always current without a separate job.
set -e

echo "Running database migrations..."
flask db upgrade

echo "Starting gunicorn..."
exec gunicorn wsgi:app --workers 2 --bind "0.0.0.0:$PORT" --timeout 120
