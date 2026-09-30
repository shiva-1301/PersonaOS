def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_id_generated(client):
    resp = client.get("/health")
    assert len(resp.headers["X-Request-ID"]) == 32


def test_request_id_echoed_when_safe(client):
    resp = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"


def test_request_id_replaced_when_unsafe(client):
    resp = client.get("/health", headers={"X-Request-ID": "bad id\x7f<script>"})
    assert resp.headers["X-Request-ID"] != "bad id\x7f<script>"


def test_unknown_route_uses_error_envelope(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found"}}
