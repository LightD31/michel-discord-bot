"""The Satisfactory API token only goes to the server whose certificate is pinned (S08)."""

import datetime as dt
import hashlib
import http.server
import json
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pyfactorybridge.exceptions import ServerError

import extensions.satisfactory as satisfactory


def self_signed(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "satisfactory")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    return cert_path, key_path, hashlib.sha256(der).hexdigest()


@pytest.fixture
def tls_server(tmp_path):
    cert_path, key_path, fingerprint = self_signed(tmp_path)
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.headers.get("Authorization") or "")
            function = json.loads(body)["function"]
            data = {"health": "healthy"} if function == "HealthCheck" else {}
            payload = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}", fingerprint, seen
    server.shutdown()
    server.server_close()


def test_the_pinned_server_is_reached(tls_server):
    address, fingerprint, _ = tls_server
    api = satisfactory._build_api(address, "tok", fingerprint)
    try:
        assert api.query_server_state() == {}
    finally:
        api.session.close()


def test_another_certificate_gets_nothing(tls_server):
    address, _, seen = tls_server
    with pytest.raises(ServerError):
        satisfactory._build_api(address, "tok", "00" * 32)
    assert seen == []  # not even the health check reached the server


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AB:" * 31 + "AB", "ab" * 32),
        ("sha256 Fingerprint=" + "AB:" * 31 + "AB", "ab" * 32),
        ("ab" * 32, "ab" * 32),
        ("ab" * 31, None),
        ("zz" * 32, None),
        (None, None),
    ],
)
def test_fingerprints_are_normalised(raw, expected):
    assert satisfactory.normalize_fingerprint(raw) == expected


async def test_an_unpinned_server_is_not_contacted(monkeypatch, caplog):
    built: list = []
    monkeypatch.setattr(satisfactory, "_build_api", lambda *a: built.append(a))
    ext = object.__new__(satisfactory.Satisfactory)
    ext._apis = {}
    ext._unpinned_warned = set()
    settings = {"satisfactoryServerIp": "a", "satisfactoryServerToken": "t"}
    for _ in range(2):
        await ext._refresh("1", settings)
    assert built == []
    assert sum("not read" in r.getMessage() for r in caplog.records) == 1
