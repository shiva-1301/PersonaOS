"""Mint Firebase-shaped ID tokens signed by a local test key (no network)."""

import time
from datetime import UTC, datetime, timedelta

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PROJECT_ID = "personaos-test"
KID = "test-kid-1"


def make_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """Return (private key, PEM self-signed certificate) like Google's x509 endpoint serves."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken.test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


def mint(
    key: rsa.RSAPrivateKey,
    *,
    uid: str = "firebase-uid-a",
    kid: str = KID,
    project_id: str = PROJECT_ID,
    exp_in: int = 3600,
    iat_offset: int = -10,
    auth_time_offset: int = -10,
    **overrides,
) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://securetoken.google.com/{project_id}",
        "aud": project_id,
        "sub": uid,
        "user_id": uid,
        "iat": now + iat_offset,
        "exp": now + exp_in,
        "auth_time": now + auth_time_offset,
        "email": f"{uid}@example.test",
        "name": "Test User",
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})
