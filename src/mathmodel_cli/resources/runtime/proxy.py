"""Small fixed-upstream Responses relay. API credentials arrive once on stdin."""
from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import socket
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY = 16 * 1024**2


def public_addresses(host: str) -> list[str]:
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("upstream must resolve exclusively to public addresses")
    return addresses


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # Resolve once, then connect to that IP with TLS verification against the hostname.
        addresses = public_addresses(self.host)
        raw = socket.create_connection((addresses[0], self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def checked_request(path: str, body: bytes, model: str) -> tuple[str, bytes]:
    if path not in ("/v1/responses", "/v1/responses/compact"):
        raise ValueError("unsupported API route")
    value = json.loads(body)
    if not isinstance(value, dict) or value.get("model") != model:
        raise ValueError("requested model differs from the locked job")
    # Do not allow hosted web/code/computer tools to escape the local task policy.
    for tool in value.get("tools", []):
        if not isinstance(tool, dict) or tool.get("type") not in ("function", "custom"):
            raise ValueError("hosted tools are disabled")
    if path == "/v1/responses":
        for key in ("background", "store"):
            value[key] = False
    return path.removeprefix("/v1"), json.dumps(value, ensure_ascii=False).encode("utf-8")


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reject(self, code: int):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        self.reject(405)

    def do_POST(self):
        self.connection.settimeout(60)
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self.reject(401)
            return
        if not self.server.slots.acquire(blocking=False):
            self.reject(429)
            return
        connection = None
        started = False
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
                raise ValueError("a single content length is required")
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                raise ValueError("request too large")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete body")
            suffix, body = checked_request(self.path, body, self.server.model)
            connection = PublicHTTPSConnection(self.server.hostname, timeout=300,
                                               context=ssl.create_default_context())
            connection.request("POST", self.server.prefix + suffix, body=body,
                               headers={"Authorization": "Bearer " + self.server.api_key,
                                        "Content-Type": "application/json", "Accept": "text/event-stream",
                                        "User-Agent": "MathModel-CLI/0.1"})
            response = connection.getresponse()
            if response.status != 200:
                # Never forward upstream error bodies (which can echo credentials) or redirects.
                self.reject(response.status if 400 <= response.status <= 599 else 502)
                return
            self.send_response(200)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
            self.close_connection = True
        except (ValueError, TypeError, KeyError):
            if not started:
                self.reject(400)
        except Exception:
            if not started:
                self.reject(502)
        finally:
            self.close_connection = True
            if connection:
                connection.close()
            self.server.slots.release()


def main():
    config = json.load(sys.stdin)
    endpoint = urlsplit(config["base_url"])
    if endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("invalid endpoint")
    if endpoint.path not in ("", "/v1") or endpoint.port not in (None, 443):
        raise ValueError("unsupported endpoint")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Relay)
    server.daemon_threads = True
    server.api_key = config.pop("api_key")
    server.token = config["token"]
    server.model = config["model"]
    server.hostname = endpoint.hostname
    server.prefix = endpoint.path
    server.slots = threading.BoundedSemaphore(4)
    print("READY", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
