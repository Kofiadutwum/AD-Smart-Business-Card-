"""Bring the database up to date before the web server starts (start.sh).

Runs on every start, not only at build time, so a redeploy that skips the
build (for example "Save and deploy" after changing DATABASE_URL) still gets
its tables, starting data and first admin. Every step skips work already done.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Migrate, create the cache table, load starting data and the first admin."

    def handle(self, *args, **options):
        call_command("migrate", interactive=False, verbosity=1)
        call_command("createcachetable")
        # The starter gallery goes to Cloudinary only, never Render's temporary disk.
        call_command("seed_initial", gallery=bool(settings.CLOUDINARY_URL))
        call_command("ensure_superuser")
