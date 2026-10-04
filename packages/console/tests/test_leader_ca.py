"""Leaders whose certificates come from a private CA (SWARMSCRIBE_CONSOLE_LEADER_CA_FILE).

The round-trip tests talk real TLS to a server on an ephemeral port of 127.0.0.1."""

import asyncio
import datetime
import ipaddress
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr, ValidationError
from swarmscribe_console.app import create_app
from swarmscribe_console.config import Settings
from swarmscribe_console.leader_client import (
    POLLER_ACTOR,
    LeaderClient,
    LeaderTarget,
    LeaderUnreachable,
    leader_tls_context,
)
from swarmscribe_console.oidc import CodeExchangeFailed, WebProvider, exchange_code

KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
CREDENTIAL = "k" * 21 + "_" + "Q" * 21
CA_NAME = "test leader CA"


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://u:p@127.0.0.1:1/none",
        "public_url": "https://console.example.org",
        "key": KEY,
        "entra_tenant_id": "0f0e0d0c-0b0a-4908-8706-050403020100",
        "entra_client_id": "entra-client",
        "entra_client_secret": "entra-secret-value",
    }
    values.update(overrides)
    return Settings(**values)


def write_ca_and_server(folder: Path, san: x509.GeneralName) -> tuple[Path, Path, Path]:
    """A CA and a server certificate it signed for `san`: (ca.pem, server.pem, server.key)."""
    now = datetime.datetime.now(datetime.UTC)
    start, end = now - datetime.timedelta(minutes=5), now + datetime.timedelta(days=1)

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name(CA_NAME))
        .issuer_name(name(CA_NAME))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(name("a leader"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = folder / "ca.pem", folder / "server.pem", folder / "server.key"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_file, cert_file, key_file


class TlsLeader:
    """A TLS server on 127.0.0.1 that answers every request `{"ok": true}`."""

    def __init__(self, folder: Path, san: x509.GeneralName):
        self.ca_file, self._cert, self._key = write_ca_and_server(folder, san)
        self._server: asyncio.Server | None = None
        self.target: LeaderTarget | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = b'{"ok": true}'
        head = (
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
            f"content-length: {len(body)}\r\nconnection: close\r\n\r\n"
        )
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(head.encode() + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass  # a client that refused the certificate hangs up mid-handshake
        finally:
            writer.close()

    async def __aenter__(self) -> "TlsLeader":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self._cert, self._key)
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=context)
        port = self._server.sockets[0].getsockname()[1]
        self.target = LeaderTarget("lan-1", f"https://127.0.0.1:{port}", CREDENTIAL)
        return self

    async def __aexit__(self, *_exc) -> None:
        self._server.close()
        await self._server.wait_closed()


@pytest.fixture
async def private_leader(tmp_path):
    san = x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
    async with TlsLeader(tmp_path, san) as leader:
        yield leader


async def status(client: LeaderClient, target: LeaderTarget):
    try:
        return await client.call(
            target, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=5.0
        )
    finally:
        await client.aclose()


# --- the setting ------------------------------------------------------------------------


def test_no_leader_ca_file_is_the_default():
    assert settings().leader_ca_file is None
    assert leader_tls_context(None) is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_leader_ca_file_is_unset(blank):
    # Compose and Kubernetes pass an unset variable as an empty string.
    assert settings(leader_ca_file=blank).leader_ca_file is None


@pytest.mark.parametrize("kind", ["missing", "not-pem", "a-folder"])
def test_a_leader_ca_file_without_certificates_is_refused(kind, tmp_path):
    bad = {"missing": tmp_path / "none.pem", "not-pem": tmp_path / "junk.pem", "a-folder": tmp_path}
    (tmp_path / "junk.pem").write_text("not a certificate\n")
    with pytest.raises(ValidationError) as refused:
        settings(leader_ca_file=str(bad[kind]))
    assert "leader_ca_file must be a readable PEM file" in str(refused.value)
    assert str(tmp_path) not in str(refused.value)  # hide_input_in_errors


def test_the_ca_file_adds_to_the_public_roots(tmp_path):
    ca_file, _, _ = write_ca_and_server(tmp_path, x509.DNSName("leader.example"))
    context = leader_tls_context(settings(leader_ca_file=str(ca_file)).leader_ca_file)
    names = [dict(part[0] for part in ca["subject"]).get("commonName")
             for ca in context.get_ca_certs()]
    assert CA_NAME in names
    assert len(names) > 1  # the public roots are still trusted
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


# --- real TLS ---------------------------------------------------------------------------


async def test_a_leader_on_a_private_ca_is_reached_with_the_ca_file(private_leader):
    reply = await status(LeaderClient(ca_file=private_leader.ca_file), private_leader.target)
    assert (reply.status, reply.body) == (200, {"ok": True})


async def test_a_leader_on_a_private_ca_is_refused_without_it(private_leader):
    with pytest.raises(LeaderUnreachable) as refused:
        await status(LeaderClient(), private_leader.target)
    assert refused.value.reason == "connect_error"


async def test_the_environment_cannot_add_a_ca(private_leader, monkeypatch):
    # Leader calls carry credentials: SSL_CERT_FILE and SSL_CERT_DIR are never read.
    monkeypatch.setenv("SSL_CERT_FILE", str(private_leader.ca_file))
    monkeypatch.setenv("SSL_CERT_DIR", str(private_leader.ca_file.parent))
    with pytest.raises(LeaderUnreachable):
        await status(LeaderClient(), private_leader.target)


async def test_a_trusted_ca_does_not_excuse_the_wrong_host_name(tmp_path):
    async with TlsLeader(tmp_path, x509.DNSName("another-leader.example")) as leader:
        with pytest.raises(LeaderUnreachable) as refused:
            await status(LeaderClient(ca_file=leader.ca_file), leader.target)
    assert refused.value.reason == "connect_error"


async def test_the_console_uses_the_setting(private_leader, make_settings):
    application = create_app(
        make_settings(leader_ca_file=str(private_leader.ca_file)), background=False
    )
    try:
        reply = await application.state.leader_client.call(
            private_leader.target,
            "GET",
            "/v1/admin/status",
            actor=POLLER_ACTOR,
            role="viewer",
            timeout=5.0,
        )
    finally:
        await application.state.leader_client.aclose()
        await application.state.engine.dispose()
    assert reply.status == 200

# --- the leader CA does not widen identity-provider trust ----------------------------------


async def exchange_at(private_leader, *, provider_name="entra"):
    provider = WebProvider(
        name=provider_name,
        client_id="client",
        client_secret=SecretStr("secret"),
        authorization_endpoint="https://127.0.0.1/authorize",
        token_endpoint=f"{private_leader.target.base_url}/token",
        verification=None,
    )
    return await exchange_code(
        provider, code="c", verifier="v" * 64, redirect_uri="https://console.example.org/cb"
    )


async def test_the_sign_in_code_exchange_refuses_a_server_signed_only_by_the_leader_ca(
    private_leader, make_settings, monkeypatch
):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    application = create_app(
        make_settings(leader_ca_file=str(private_leader.ca_file)), background=False
    )
    try:
        with pytest.raises(CodeExchangeFailed) as refused:
            await exchange_at(private_leader)
    finally:
        await application.state.leader_client.aclose()
        await application.state.engine.dispose()
    assert "could not be used" in str(refused.value)


async def test_ssl_cert_file_is_how_the_identity_provider_calls_get_a_ca(
    private_leader, monkeypatch
):
    # Control for the test above: with the CA in SSL_CERT_FILE the TLS handshake succeeds
    # (the stand-in then answers without an ID token), so the refusal above is the CA's doing.
    monkeypatch.setenv("SSL_CERT_FILE", str(private_leader.ca_file))
    with pytest.raises(CodeExchangeFailed) as reached:
        await exchange_at(private_leader)
    assert "returned no ID token" in str(reached.value)
