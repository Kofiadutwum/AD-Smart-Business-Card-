#!/usr/bin/env bash
# Render build step for the web service.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input

# Migrations, starting data and the first admin. start.sh runs this again on
# every start, so redeploys that skip the build (such as "Save and deploy"
# after changing DATABASE_URL) are covered too. It skips work already done.
python manage.py prepare_site
