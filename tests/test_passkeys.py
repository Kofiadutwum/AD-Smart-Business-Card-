"""Face ID / fingerprint sign-in (passkeys), driven by a software authenticator that signs the way a phone does."""

import hashlib
import json
import os
import struct
import sys
from unittest import mock

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import override_settings
from webauthn.helpers import bytes_to_base64url as b64

from apps.accounts.models import Passkey, User

from .helpers import BaseTest

ORIGIN, RP_ID = "https://testserver", "testserver"


class Phone:
    """Answers the browser's passkey calls like Face ID: the person is present and verified."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)

    @staticmethod
    def _authenticator_data(flags, extra=b""):
        return hashlib.sha256(RP_ID.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + extra

    @staticmethod
    def _client_data(kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()

    def create(self, options):
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._authenticator_data(0x45, attested)})
        return {
            "id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
            "response": {
                "clientDataJSON": b64(self._client_data("webauthn.create", options["challenge"], ORIGIN)),
                "attestationObject": b64(attestation), "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options, origin=ORIGIN):
        data = self._authenticator_data(0x05)
        client = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "authenticatorData": b64(data), "signature": b64(signature)},
            "clientExtensionResults": {},
        }


class PasskeyTests(BaseTest):
    def post_json(self, url, body=None):
        return self.client.post(url, json.dumps(body or {}), content_type="application/json")

    def add_passkey(self, phone):
        options = self.post_json("/auth/passkey/add/start").json()
        return self.post_json("/auth/passkey/add", {"credential": phone.create(options)})

    def sign_in(self, phone, origin=ORIGIN, **extra):
        options = self.post_json("/auth/passkey/sign-in/start").json()
        return self.post_json("/auth/passkey/sign-in", {"credential": phone.get(options, origin), **extra})

    def customer_with_passkey(self):
        user = self.make_user()
        self.make_card(user)
        self.client.force_login(user)
        phone = Phone()
        self.assertEqual(self.add_passkey(phone).status_code, 200)
        self.client.logout()
        return user, phone

    def test_both_sign_in_pages_offer_it(self):
        for url in ("/auth/login", "/staff/login"):
            self.assertContains(self.client.get(url), "data-passkey-signin")

    def test_the_dashboard_offers_to_turn_it_on(self):
        user = self.make_user()
        self.make_card(user)
        self.client.force_login(user)
        self.assertContains(self.client.get("/dashboard/"), "data-passkey-offer")

    def test_a_customer_turns_it_on_then_signs_in_with_it(self):
        user, phone = self.customer_with_passkey()
        self.assertEqual(user.passkeys.get().name, "Phone or computer")
        response = self.sign_in(phone)
        self.assertEqual(response.json()["redirect"], "/dashboard/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertContains(self.client.get("/dashboard/settings/"), "Phone or computer")

    def test_forged_replayed_and_foreign_answers_are_refused(self):
        user, phone = self.customer_with_passkey()
        thief = Phone()
        thief.credential_id = phone.credential_id  # right id, wrong key
        self.assertEqual(self.sign_in(thief).status_code, 400)
        self.assertEqual(self.sign_in(phone, origin="https://evil.example").status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)
        options = self.post_json("/auth/passkey/sign-in/start").json()
        answer = phone.get(options)
        self.assertEqual(self.post_json("/auth/passkey/sign-in", {"credential": answer}).status_code, 200)
        self.client.logout()
        self.assertEqual(self.post_json("/auth/passkey/sign-in", {"credential": answer}).status_code, 400)
        self.assertTrue(self.sign_in(Phone()).json()["forget"])  # a device the site does not know

    def test_a_suspended_account_cannot_sign_in_with_it(self):
        user, phone = self.customer_with_passkey()
        user.is_suspended = True
        user.save(update_fields=["is_suspended"])
        self.assertEqual(self.sign_in(phone).status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(STAFF_TWO_FACTOR_REQUIRED=True)
    def test_staff_sign_in_with_it_instead_of_the_emailed_code(self):
        staff = User.objects.create_user(email="ops@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.client.post("/staff/login", {"email": staff.email, "password": "Staff-pass-2026"})
        self.assertEqual(self.post_json("/auth/passkey/add/start").status_code, 403)  # the password alone is not enough
        session = self.client.session
        session["staff_2fa"] = staff.pk  # as if the emailed code was typed
        session.save()
        self.assertContains(self.client.get("/staff/"), "Face ID and fingerprint sign-in")
        phone = Phone()
        self.assertEqual(self.add_passkey(phone).status_code, 200)
        self.client.logout()
        response = self.sign_in(phone, next="/staff/subscribers/")
        self.assertEqual(response.json()["redirect"], "/staff/subscribers/")
        self.assertEqual(self.client.get("/staff/subscribers/").status_code, 200)

    def test_promoting_a_customer_drops_their_customer_passkeys(self):
        user, _phone = self.customer_with_passkey()
        boss = User.objects.create_user(email="boss@example.com", password="Staff-pass-2026", is_staff=True, staff_role="super")
        self.client.force_login(boss)
        self.client.post("/staff/admins/", {"email": user.email, "role": "support"})
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertFalse(user.passkeys.exists())

    def test_people_remove_only_their_own_devices(self):
        user, _phone = self.customer_with_passkey()
        passkey = user.passkeys.get()
        other = self.make_user("ama@example.com")
        self.client.force_login(other)
        self.assertEqual(self.client.post(f"/auth/passkey/{passkey.pk}/remove").status_code, 404)
        self.client.force_login(user)
        self.client.post(f"/auth/passkey/{passkey.pk}/remove", {"next": "/dashboard/settings/"})
        self.assertFalse(Passkey.objects.exists())

    def test_a_damaged_library_turns_it_off_not_the_site(self):
        with mock.patch.dict(sys.modules, {"webauthn": None}), self.assertLogs("apps.accounts.passkeys", "ERROR"):
            self.assertEqual(self.post_json("/auth/passkey/sign-in/start").status_code, 503)
            self.assertEqual(self.client.get("/auth/login").status_code, 200)
