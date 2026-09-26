#!/usr/bin/env bash
# Render start step: update the database, then serve the site.
set -o errexit

python manage.py prepare_site
exec gunicorn config.wsgi:application --workers 2 --timeout 60
