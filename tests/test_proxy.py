import time

import httpx
import pytest

from chaoskit.app import Settings, create_app
from chaoskit.boundary import hop_headers
from chaoskit.proxy import Exchange
from conftest import live_server

TARGET = "/api/orders/1"
RULE = "/_chaoskit/rules/example"


def set_fault(client, fault, value, enabled=True):
    response = client.put(RULE, json={
        "id": "example", "path": TARGET, "fault": fault, "value": value, "enabled": enabled,
    })
    assert response.is_success


def test_pass_through_and_streaming(proxy, upstream):
    with httpx.Client(trust_env=False) as client:
        for path in (TARGET, "/api/plain", "/api/payload", "/api/payload?compressed=false"):
            via = client.get(proxy + path)
            direct = client.get(upstream + path)
            assert (via.status_code, via.content) == (direct.status_code, direct.content)
        with client.stream("GET", proxy + "/api/payload") as via:
            assert via.headers["content-encoding"] == "gzip"
            raw = b"".join(via.iter_raw())
            assert int(via.headers["content-length"]) == len(raw)
        with client.stream("GET", upstream + "/api/payload") as direct:
            assert b"".join(direct.iter_raw()) == raw
        assert client.get(proxy + "/api/redirect").headers["location"] == TARGET


def test_hop_by_hop_removes_connection_named_header():
    assert hop_headers([(b"Connection", b"Keep-Alive, X-Ephemeral"), (b"X-Ephemeral", b"only local"),
                        (b"x-end-to-end", b"keep")]) == [(b"x-end-to-end", b"keep")]


def test_faults_and_hot_rule_updates(proxy):
    with httpx.Client(base_url=proxy, trust_env=False) as client:
        set_fault(client, "status", 500)
        assert client.get(TARGET).status_code == 500
        set_fault(client, "delay", 200)
        start = time.monotonic()
        result = client.get(TARGET)
        assert result.status_code == 200
        assert result.headers["x-chaoskit-outcome"] == "delay"
        assert time.monotonic() - start >= .18
        set_fault(client, "abort", 0)
        with pytest.raises(httpx.RemoteProtocolError):
            client.get(TARGET)
        set_fault(client, "timeout", 250)
        with pytest.raises(httpx.ReadTimeout):
            client.get(TARGET, timeout=.09)
        assert client.get(TARGET).status_code == 504
        set_fault(client, "timeout", 250, enabled=False)
        assert client.get(TARGET).status_code == 200
        assert client.delete(RULE).status_code == 204


def test_local_boundary_and_no_credentials_in_log(proxy, tmp_path):
    secret = "a-private-value"
    with httpx.Client(base_url=proxy, trust_env=False) as client:
        assert client.get(TARGET, headers={"Host": "outside.example"}).status_code == 403
        assert client.post("/api/echo", content=secret).status_code == 405
        assert client.get(TARGET + f"?token={secret}", headers={
            "Authorization": f"Bearer {secret}", "Cookie": f"session={secret}",
        }).status_code == 200
        assert secret not in client.get("/").text
    for database in tmp_path.glob("test.sqlite3*"):
        assert secret.encode() not in database.read_bytes()


def test_disallowed_upstream_and_fail_open(proxy, tmp_path, monkeypatch):
    settings = Settings("http://203.0.113.5", str(tmp_path / "blocked.sqlite3"))
    with live_server(create_app(settings)) as blocked:
        assert httpx.get(blocked + TARGET, trust_env=False).status_code == 403

    async def malfunction(_):
        raise RuntimeError("Test engine error")

    monkeypatch.setattr(Exchange, "choose", malfunction)
    response = httpx.get(proxy + TARGET, trust_env=False)
    assert response.status_code == 200
    assert response.headers["x-chaoskit-outcome"] == "fail_open"
