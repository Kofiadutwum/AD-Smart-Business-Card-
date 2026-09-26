"""Send email through the Gmail API.

Render's free plan blocks outbound SMTP, and a Gmail address cannot be
verified with a sending service like Resend, so until the business has its own
domain the site sends from its Gmail account over HTTPS instead. It uses the
same Google OAuth client as "Continue with Google" plus a refresh token for
the gmail.send permission (see ``manage.py connect_gmail``).
"""

import base64
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
CACHE_KEY = "gmail:access-token"


class GmailError(Exception):
    pass


def _access_token():
    token = cache.get(CACHE_KEY)
    if token:
        return token
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": settings.GMAIL_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=settings.EMAIL_TIMEOUT,
    )
    if response.status_code != 200:
        # "invalid_grant" means the permission was removed or the Google
        # password changed: run connect_gmail again.
        raise GmailError(f"Google refused the Gmail token ({response.status_code}): {response.text[:300]}")
    data = response.json()
    # Tokens last an hour; refresh a little early.
    cache.set(CACHE_KEY, data["access_token"], max(60, int(data.get("expires_in", 3600)) - 300))
    return data["access_token"]


class GmailAPIBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Gmail send failed")
        return sent

    def _send(self, message):
        raw = base64.urlsafe_b64encode(message.message().as_bytes()).decode()
        for attempt in range(2):
            response = requests.post(
                SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {_access_token()}"},
                timeout=settings.EMAIL_TIMEOUT,
            )
            if response.status_code == 401 and attempt == 0:
                cache.delete(CACHE_KEY)  # token revoked or expired early; fetch a new one
                continue
            if response.status_code >= 300:
                raise GmailError(f"Gmail rejected the email ({response.status_code}): {response.text[:300]}")
            return
