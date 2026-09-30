"""ID-token verification behind a small interface so the provider can be swapped.

Firebase tokens are verified exactly as the Firebase docs specify for third-party JWT
libraries (https://firebase.google.com/docs/auth/admin/verify-id-tokens):
RS256, `kid` from Google's x509 certs (cached per Cache-Control max-age), `aud` = project
ID, `iss` = https://securetoken.google.com/<project ID>, valid `exp`/`iat`/`auth_time`,
non-empty `sub`.
"""

import json
import logging
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import jwt
from cryptography.x509 import load_pem_x509_certificate

from app.config import Settings

logger = logging.getLogger(__name__)

FIREBASE_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
# Tolerated clock skew between our host and Google's.
CLOCK_SKEW_SECONDS = 30


class AuthError(Exception):
    """Token missing, malformed, expired or otherwise not acceptable."""


@dataclass(frozen=True)
class Claims:
    uid: str
    email: str | None = None
    name: str | None = None


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Claims: ...


class CertCache:
    """Fetches Google's public certs and caches them for the advertised max-age."""

    def __init__(self, url: str = FIREBASE_CERTS_URL, fetch: Callable | None = None):
        self._url = url
        self._fetch = fetch or self._http_fetch
        self._keys: dict[str, object] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _http_fetch(url: str) -> tuple[dict[str, str], int]:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read())
            match = re.search(r"max-age=(\d+)", resp.headers.get("Cache-Control", ""))
        return body, int(match.group(1)) if match else 3600

    def get_key(self, kid: str):
        with self._lock:
            now = time.monotonic()
            if now >= self._expires_at or kid not in self._keys:
                # Refresh when expired, or when an unknown kid appears (key rotation).
                certs, max_age = self._fetch(self._url)
                self._keys = {
                    k: load_pem_x509_certificate(pem.encode()).public_key()
                    for k, pem in certs.items()
                }
                self._expires_at = now + max_age
            key = self._keys.get(kid)
        if key is None:
            raise AuthError("Unknown signing key")
        return key


class FirebaseVerifier:
    def __init__(self, project_id: str, certs: CertCache | None = None):
        self._project_id = project_id
        self._issuer = f"https://securetoken.google.com/{project_id}"
        self._certs = certs or CertCache()

    def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("Malformed token") from exc
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AuthError("Unexpected token header")

        try:
            key = self._certs.get_key(header["kid"])
            payload = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._project_id,
                issuer=self._issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "aud", "iss", "sub", "auth_time"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("Invalid token") from exc
        except AuthError:
            raise
        except Exception as exc:  # cert fetch failures etc.
            logger.warning("Token verification unavailable: %s", type(exc).__name__)
            raise AuthError("Token verification unavailable") from exc

        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 128:
            raise AuthError("Invalid subject")
        if payload["auth_time"] > time.time() + CLOCK_SKEW_SECONDS:
            raise AuthError("Invalid auth_time")
        return Claims(uid=sub, email=payload.get("email"), name=payload.get("name"))


_FAKE_TOKEN = re.compile(r"^test-[a-z0-9-]{1,50}$")


class FakeVerifier:
    """Dev/test only: accepts tokens like `test-user-a` and uses them as the uid."""

    def verify(self, token: str) -> Claims:
        if not _FAKE_TOKEN.match(token):
            raise AuthError("Invalid token")
        return Claims(uid=token, email=f"{token}@example.test", name=token)


def build_verifier(settings: Settings) -> TokenVerifier:
    if settings.AUTH_PROVIDER == "fake":
        # Settings already refuses fake auth in production; double-check here.
        if settings.APP_ENV not in ("dev", "test"):
            raise RuntimeError("Fake auth is only allowed when APP_ENV is dev or test")
        logger.warning("Using FAKE authentication (APP_ENV=%s)", settings.APP_ENV)
        return FakeVerifier()
    if not settings.FIREBASE_PROJECT_ID:
        raise RuntimeError("AUTH_PROVIDER=firebase requires FIREBASE_PROJECT_ID")
    return FirebaseVerifier(settings.FIREBASE_PROJECT_ID)
