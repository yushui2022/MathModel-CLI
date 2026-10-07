import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest
from conftest import runtime_module


@pytest.fixture
def proxy():
    return runtime_module("proxy")


@pytest.mark.parametrize("path", ["/v1/models", "/v1/files", "http://evil.test/v1/responses", "/v1/responses?x=1"])
def test_only_allowed_routes(proxy, path):
    with pytest.raises(ValueError):
        proxy.checked_request(path, b'{"model":"m"}', "m")


def test_model_pin_and_hosted_tools(proxy):
    with pytest.raises(ValueError):
        proxy.checked_request("/v1/responses", b'{"model":"other"}', "m")
    with pytest.raises(ValueError):
        proxy.checked_request("/v1/responses", b'{"model":"m","tools":[{"type":"web_search"}]}', "m")
    suffix, body = proxy.checked_request("/v1/responses", b'{"model":"m","store":true}', "m")
    assert suffix == "/responses"
    assert json.loads(body)["store"] is False


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
def test_private_dns_rejected(proxy, monkeypatch, ip):
    monkeypatch.setattr(proxy.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))])
    with pytest.raises(ValueError):
        proxy.public_addresses("example.test")


def test_relay_does_not_follow_redirect_or_forward_secret(proxy, monkeypatch):
    calls = []

    class Upstream:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return type("Response", (), {"status": 302})()

        def close(self):
            pass

    monkeypatch.setattr(proxy, "PublicHTTPSConnection", Upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Relay)
    server.token, server.api_key, server.hostname, server.prefix, server.model = "local-token", "dummy-key", "example.test", "/v1", "m"
    server.slots = threading.BoundedSemaphore(4)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for token, expected in (("wrong", 401), ("local-token", 502)):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.request("POST", "/v1/responses", body=b'{"model":"m"}',
                         headers={"Authorization": "Bearer " + token, "Cookie": "do-not-forward"})
            response = conn.getresponse()
            assert response.status == expected and response.read() == b""
            conn.close()
        assert len(calls) == 1
        assert calls[0][2]["Authorization"] == "Bearer dummy-key"
        assert "Cookie" not in calls[0][2]
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
