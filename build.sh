#!/usr/bin/env bash
# Render build step for the web service.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py createcachetable

# Plans, NFC tiers, delivery zones and today's USD rate. The starter gallery
# is uploaded only when Cloudinary is configured, so pictures never land on
# Render's temporary disk. Both steps skip anything that already exists.
if [ -n "$CLOUDINARY_URL" ]; then
  python manage.py seed_initial --gallery
else
  python manage.py seed_initial
fi

# First Main Admin, from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD.
python manage.py ensure_superuser
