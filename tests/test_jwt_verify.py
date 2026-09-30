import jwt
import pytest

from app.auth.jwt_verify import AuthError, CertCache, FakeVerifier, FirebaseVerifier
from tests.firebase_helpers import KID, PROJECT_ID, make_keypair, mint

KEY, CERT = make_keypair()


class CountingFetch:
    def __init__(self, certs: dict[str, str], max_age: int = 3600):
        self.certs = certs
        self.max_age = max_age
        self.calls = 0

    def __call__(self, url: str):
        self.calls += 1
        return self.certs, self.max_age


@pytest.fixture
def fetch() -> CountingFetch:
    return CountingFetch({KID: CERT})


@pytest.fixture
def verifier(fetch) -> FirebaseVerifier:
    return FirebaseVerifier(PROJECT_ID, CertCache(fetch=fetch))


def test_valid_token(verifier):
    claims = verifier.verify(mint(KEY, uid="abc123"))
    assert claims.uid == "abc123"
    assert claims.email == "abc123@example.test"


def test_expired_token_rejected(verifier):
    with pytest.raises(AuthError, match="expired"):
        verifier.verify(mint(KEY, exp_in=-120, iat_offset=-3700, auth_time_offset=-3700))


def test_wrong_audience_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify(mint(KEY, project_id="someone-elses-project"))


def test_wrong_issuer_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify(mint(KEY, iss="https://evil.example.com/personaos-test"))


def test_future_iat_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify(mint(KEY, iat_offset=600))


def test_future_auth_time_rejected(verifier):
    with pytest.raises(AuthError, match="auth_time"):
        verifier.verify(mint(KEY, auth_time_offset=600))


@pytest.mark.parametrize("missing", ["sub", "auth_time", "exp", "iat"])
def test_missing_required_claim_rejected(verifier, missing):
    with pytest.raises(AuthError):
        verifier.verify(mint(KEY, **{missing: None}))


def test_empty_sub_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify(mint(KEY, uid=""))


def test_signed_by_other_key_rejected(verifier):
    other_key, _ = make_keypair()
    with pytest.raises(AuthError):
        verifier.verify(mint(other_key))


def test_unknown_kid_rejected(verifier):
    with pytest.raises(AuthError, match="Unknown signing key"):
        verifier.verify(mint(KEY, kid="not-a-real-kid"))


def test_hs256_token_rejected(verifier):
    forged = jwt.encode({"sub": "x"}, "s" * 32, algorithm="HS256", headers={"kid": KID})
    with pytest.raises(AuthError, match="header"):
        verifier.verify(forged)


def test_garbage_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify("not.a.jwt")


def test_certs_are_cached(verifier, fetch):
    for _ in range(3):
        verifier.verify(mint(KEY))
    assert fetch.calls == 1


def test_certs_refetched_after_max_age(fetch):
    fetch.max_age = 0
    v = FirebaseVerifier(PROJECT_ID, CertCache(fetch=fetch))
    v.verify(mint(KEY))
    v.verify(mint(KEY))
    assert fetch.calls == 2


def test_key_rotation_refetches_on_unknown_kid(fetch):
    v = FirebaseVerifier(PROJECT_ID, CertCache(fetch=fetch))
    v.verify(mint(KEY))
    new_key, new_cert = make_keypair()
    fetch.certs = {"rotated-kid": new_cert}
    assert v.verify(mint(new_key, kid="rotated-kid")).uid == "firebase-uid-a"
    assert fetch.calls == 2


def test_cert_fetch_failure_is_auth_error():
    def broken(url):
        raise OSError("network down")

    v = FirebaseVerifier(PROJECT_ID, CertCache(fetch=broken))
    with pytest.raises(AuthError, match="unavailable"):
        v.verify(mint(KEY))


@pytest.mark.parametrize("token", ["test-user-a", "test-b", "test-123-x"])
def test_fake_verifier_accepts_test_tokens(token):
    assert FakeVerifier().verify(token).uid == token


@pytest.mark.parametrize("token", ["", "user-a", "test-", "TEST-A", "test-a b", "x" * 80])
def test_fake_verifier_rejects_other_tokens(token):
    with pytest.raises(AuthError):
        FakeVerifier().verify(token)
