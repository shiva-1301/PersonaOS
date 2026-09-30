from sqlalchemy import func, select

from app.auth.jwt_verify import CertCache, FirebaseVerifier
from app.db.models import User
from tests.conftest import auth
from tests.firebase_helpers import KID, PROJECT_ID, make_keypair, mint


def _user_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(User))


def test_no_token_401(db_client):
    resp = db_client.get("/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_garbage_token_401(db_client):
    resp = db_client.get("/me", headers=auth("garbage"))
    assert resp.status_code == 401


def test_non_bearer_scheme_401(db_client):
    resp = db_client.get("/me", headers={"Authorization": "Basic dGVzdDp0ZXN0"})
    assert resp.status_code == 401


def test_valid_token_creates_one_user(db_client, db_session):
    resp = db_client.get("/me", headers=auth("test-user-a"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "test-user-a@example.test"
    assert body["timezone"] == "UTC"
    assert set(body) == {"id", "email", "display_name", "timezone", "created_at"}
    assert _user_count(db_session) == 1


def test_repeat_calls_do_not_duplicate(db_client, db_session):
    first = db_client.get("/me", headers=auth("test-user-a")).json()
    second = db_client.get("/me", headers=auth("test-user-a")).json()
    assert first["id"] == second["id"]
    assert _user_count(db_session) == 1


def test_two_tokens_two_users(db_client, db_session):
    a = db_client.get("/me", headers=auth("test-user-a")).json()
    b = db_client.get("/me", headers=auth("test-user-b")).json()
    assert a["id"] != b["id"]
    assert a["email"] != b["email"]
    assert _user_count(db_session) == 2


def test_firebase_verifier_end_to_end(db_app, db_session):
    """Real verification logic through the API, with locally minted Firebase-style tokens."""
    from fastapi.testclient import TestClient

    key, cert = make_keypair()
    db_app.state.verifier = FirebaseVerifier(
        PROJECT_ID, CertCache(fetch=lambda url: ({KID: cert}, 3600))
    )
    client = TestClient(db_app)

    ok = client.get("/me", headers=auth(mint(key, uid="fb-uid-1")))
    assert ok.status_code == 200

    expired = mint(key, uid="fb-uid-1", exp_in=-120, iat_offset=-3700, auth_time_offset=-3700)
    resp = client.get("/me", headers=auth(expired))
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Token expired"

    # Fake-style tokens are not accepted by the real verifier.
    assert client.get("/me", headers=auth("test-user-a")).status_code == 401
    assert _user_count(db_session) == 1
