"""Get the Gmail refresh token the site sends email with (one-off, on your PC).

    python manage.py connect_gmail

Before running it, add this Authorized redirect URI to the Google OAuth
client (Google Cloud > Clients > your web client):

    http://localhost:8765/

It opens Google in the browser. Sign in as the Gmail account the site should
send from and allow "Send email on your behalf". The command then prints the
refresh token to put in Render as GMAIL_REFRESH_TOKEN. The token is shown
here only; it is not saved anywhere.
"""

import getpass
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.gmail import SCOPE, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def clean(value):
    """Drop spaces and control characters some terminals add on paste (e.g. ^V)."""
    return "".join(ch for ch in value if ch.isprintable() and not ch.isspace())


class Command(BaseCommand):
    help = "Connect the Gmail account the site sends email from and print its refresh token."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8765)

    def handle(self, *args, **options):
        client_id = settings.GOOGLE_CLIENT_ID or clean(input("Google Client ID: "))
        client_secret = settings.GOOGLE_CLIENT_SECRET or clean(
            getpass.getpass("Google Client secret (hidden: paste it, then press Enter): ")
        )
        if not client_id or not client_secret:
            raise CommandError("The Client ID and Client secret are both needed.")

        redirect_uri = f"http://localhost:{options['port']}/"
        state = secrets.token_urlsafe(24)
        url = AUTH_URL + "?" + urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",  # always return a refresh token
            "state": state,
        })

        result = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                if "code" not in query and "error" not in query:
                    self.send_response(404)
                    self.end_headers()
                    return
                result.update({key: values[0] for key, values in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<p style='font:16px system-ui;margin:3rem'>Done. You can close this tab "
                    b"and go back to the terminal.</p>"
                )

            def log_message(self, *args):
                pass

        server = HTTPServer(("localhost", options["port"]), Handler)
        # Wake up every second so Ctrl+C works on Windows while waiting.
        server.timeout = 1
        self.stdout.write("\nOpening Google in your browser. If it does not open, visit:\n\n" + url + "\n")
        self.stdout.write("\nSign in as the Gmail account the site should send from, then allow access.")
        self.stdout.write("If Google says the app is not verified: Advanced > Go to ... (unsafe). It is your own app.")
        self.stdout.write("Waiting for Google (up to 10 minutes; Ctrl+C to cancel)...\n")
        webbrowser.open(url)
        deadline = time.monotonic() + 600
        try:
            while not result and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if not result:
            raise CommandError("No reply from Google within 10 minutes. Run the command again.")
        if result.get("error"):
            raise CommandError(f"Google said: {result['error']}")
        if result.get("state") != state:
            raise CommandError("The reply did not match this request. Run the command again.")

        response = requests.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=20)
        data = response.json()
        if response.status_code != 200 or "refresh_token" not in data:
            raise CommandError(f"Google did not return a refresh token: {data}")

        self.stdout.write(self.style.SUCCESS("\nConnected. Put this in Render > adsmart-web > Environment:\n"))
        self.stdout.write("GMAIL_REFRESH_TOKEN")
        self.stdout.write(data["refresh_token"] + "\n")
        self.stdout.write("Keep it private, like a password. Do not paste it in chat or commit it.")
