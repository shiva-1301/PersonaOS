"""frontend/firebase_auth.py against a fake Firebase Auth REST API."""

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

from frontend.firebase_auth import AuthError, FirebaseAuth, FirebaseSession

NOW = 1_800_000_000.0


def fake_jwt(**claims) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def auth_with(handler, clock=lambda: NOW) -> FirebaseAuth:
    return FirebaseAuth("web-key", transport=httpx.MockTransport(handler), clock=clock)


def firebase_error(reason: str) -> httpx.Response:
    return httpx.Response(400, json={"error": {"code": 400, "message": reason}})


def signed_in_body(token="ID-1", refresh="REFRESH-1") -> dict:
    return {
        "localId": "uid-1",
        "email": "a@example.com",
        "idToken": token,
        "refreshToken": refresh,
        "expiresIn": "3600",
    }


def test_sign_in_returns_a_session_and_sends_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/accounts:signInWithPassword"
        assert request.url.params["key"] == "web-key"
        assert json.loads(request.content) == {
            "email": "a@example.com",
            "password": "pw",
            "returnSecureToken": True,
        }
        return httpx.Response(200, json=signed_in_body())

    session = auth_with(handler).sign_in(" a@example.com ", "pw")
    assert (session.uid, session.email, session.id_token) == ("uid-1", "a@example.com", "ID-1")
    assert session.expires_at == NOW + 3600


def test_sign_up_uses_the_sign_up_endpoint():
    def handler(request):
        assert request.url.path == "/v1/accounts:signUp"
        return httpx.Response(200, json=signed_in_body())

    assert auth_with(handler).sign_up("a@example.com", "secret1").uid == "uid-1"


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("INVALID_LOGIN_CREDENTIALS", "Wrong email or password."),
        ("EMAIL_EXISTS", "already exists"),
        ("WEAK_PASSWORD : Password should be at least 6 characters", "at least 6 characters"),
        ("TOO_MANY_ATTEMPTS_TRY_LATER : Access blocked", "Too many attempts"),
        ("SOMETHING_NEW", "Sign-in failed"),
    ],
)
def test_firebase_errors_become_friendly_messages(reason, message):
    with pytest.raises(AuthError) as caught:
        auth_with(lambda request: firebase_error(reason)).sign_in("a@example.com", "pw")
    assert message in caught.value.message
    assert caught.value.reason == reason.split(" ")[0]


def test_fresh_keeps_a_valid_token_and_refreshes_one_about_to_expire():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "securetoken.googleapis.com"
        form = parse_qs(request.content.decode())
        assert form == {"grant_type": ["refresh_token"], "refresh_token": ["REFRESH-1"]}
        return httpx.Response(
            200,
            json={
                "id_token": "ID-2",
                "refresh_token": "REFRESH-2",
                "expires_in": "3600",
                "user_id": "uid-1",
            },
        )

    auth = auth_with(handler)
    valid = FirebaseSession("uid-1", "a@example.com", "ID-1", "REFRESH-1", NOW + 1800)
    assert auth.fresh(valid) is valid and calls == []

    expiring = FirebaseSession("uid-1", "a@example.com", "ID-1", "REFRESH-1", NOW + 60)
    renewed = auth.fresh(expiring)
    assert (renewed.id_token, renewed.refresh_token) == ("ID-2", "REFRESH-2")
    assert renewed.expires_at == NOW + 3600 and renewed.email == "a@example.com"
    assert len(calls) == 1


def test_a_revoked_refresh_token_asks_to_sign_in_again():
    auth = auth_with(lambda request: firebase_error("TOKEN_EXPIRED"))
    old = FirebaseSession("uid-1", "a@example.com", "ID-1", "REFRESH-1", NOW - 10)
    with pytest.raises(AuthError, match="sign in again"):
        auth.fresh(old)


def test_network_failure_and_missing_key():
    def down(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(AuthError, match="Can't reach Firebase"):
        auth_with(down).sign_in("a@example.com", "pw")
    with pytest.raises(AuthError, match="FIREBASE_WEB_API_KEY"):
        FirebaseAuth("")


def test_tokens_never_appear_in_repr():
    session = FirebaseSession("uid-1", "a@example.com", "SECRET-ID", "SECRET-REFRESH", NOW)
    assert "SECRET" not in repr(session) and "SECRET" not in str(session)


def test_recent_sign_in_check_reads_auth_time():
    auth = auth_with(lambda request: httpx.Response(500))
    recent = FirebaseSession("u", "e", fake_jwt(auth_time=int(NOW) - 60), "r", NOW)
    old = FirebaseSession("u", "e", fake_jwt(auth_time=int(NOW) - 3600), "r", NOW)
    assert auth.signed_in_recently(recent) is True
    assert auth.signed_in_recently(old) is False
    assert auth.signed_in_recently(FirebaseSession("u", "e", "not-a-jwt", "r", NOW)) is False


def test_delete_account_and_password_reset_requests():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    auth = auth_with(handler)
    auth.delete_account(FirebaseSession("u", "e", "ID-1", "r", NOW))
    auth.send_password_reset("a@example.com")
    assert seen == [
        ("/v1/accounts:delete", {"idToken": "ID-1"}),
        ("/v1/accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": "a@example.com"}),
    ]
