"""Verify password hashes carried over from the Flask site.

Flask stored Werkzeug hashes such as ``scrypt:32768:8:1$<salt>$<hex>`` or
``pbkdf2:sha256:600000$<salt>$<hex>``. The import prefixes them with
``werkzeug$`` so Django can route them here. On a subscriber's first
successful login Django re-hashes the password with Argon2 (``must_update``),
so nobody has to reset their password because of the move.
"""

import hashlib
import hmac

from django.contrib.auth.hashers import BasePasswordHasher
from django.utils.crypto import constant_time_compare


class WerkzeugPasswordHasher(BasePasswordHasher):
    algorithm = "werkzeug"

    def salt(self):  # pragma: no cover - never used to create hashes
        raise NotImplementedError("Werkzeug hashes are verify-only.")

    def encode(self, password, salt):  # pragma: no cover
        raise NotImplementedError("Werkzeug hashes are verify-only.")

    @staticmethod
    def _split(encoded):
        # werkzeug$<method>$<salt>$<hash>
        _, method, salt, digest = encoded.split("$", 3)
        return method, salt, digest

    @staticmethod
    def _compute(method, salt, password):
        password_b = password.encode("utf-8")
        salt_b = salt.encode("utf-8")
        if method.startswith("scrypt"):
            parts = method.split(":")
            n, r, p = (int(parts[1]), int(parts[2]), int(parts[3])) if len(parts) == 4 else (2**15, 8, 1)
            return hashlib.scrypt(
                password_b, salt=salt_b, n=n, r=r, p=p, maxmem=132 * n * r * p, dklen=64
            ).hex()
        if method.startswith("pbkdf2"):
            parts = method.split(":")
            hash_name = parts[1] if len(parts) > 1 else "sha256"
            iterations = int(parts[2]) if len(parts) > 2 else 600000
            return hashlib.pbkdf2_hmac(hash_name, password_b, salt_b, iterations).hex()
        # Very old Werkzeug "sha256$salt$hmac" style.
        return hmac.new(salt_b, password_b, method).hexdigest()

    def verify(self, password, encoded):
        try:
            method, salt, digest = self._split(encoded)
            return constant_time_compare(self._compute(method, salt, password), digest)
        except (ValueError, TypeError):
            return False

    def safe_summary(self, encoded):
        method, salt, digest = self._split(encoded)
        return {"algorithm": self.algorithm, "method": method, "salt": salt[:4] + "…"}

    def must_update(self, encoded):
        return True

    def harden_runtime(self, password, encoded):
        pass
