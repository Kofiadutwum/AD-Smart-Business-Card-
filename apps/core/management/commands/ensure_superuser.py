"""Create the first Main Admin from environment variables, once.

For hosts without a shell (Render's free plan), set these on the service:

    DJANGO_SUPERUSER_EMAIL     the admin's email
    DJANGO_SUPERUSER_PASSWORD  a strong password (remove it after the first deploy)

Runs on every build and does nothing when the variables are missing or the
account already exists. It never changes an existing account's password.
"""

import os

from django.core.management.base import BaseCommand

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create the first superuser from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD, if missing."

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip().lower()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        if not email or not password:
            self.stdout.write("ensure_superuser: no DJANGO_SUPERUSER_EMAIL/PASSWORD set; skipped.")
            return
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(f"ensure_superuser: {email} already exists; left unchanged.")
            return
        if len(password) < 12:
            self.stderr.write("ensure_superuser: password must be at least 12 characters; admin not created.")
            return
        User.objects.create_superuser(email=email, password=password, full_name="Main Admin")
        self.stdout.write(self.style.SUCCESS(f"ensure_superuser: created Main Admin {email}."))
