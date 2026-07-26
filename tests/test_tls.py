"""The one defence that has to hold on the wire: is the server who it claims?

Everything else this package does about secrets guards the disk -- mode 0600, a
refusal to read a group-readable file, a staged write that is never briefly
loose. None of it matters if the password is handed to whoever answers the
socket, and that is what an unverified certificate means.

It was handed over, until these tests. `smtplib` negotiates TLS with
`ssl._create_stdlib_context()` when no context is given, and that function *is*
`ssl._create_unverified_context` -- `check_hostname=False`,
`verify_mode=CERT_NONE`. A self-signed certificate claiming to be
smtp.naver.com was accepted without complaint and `login()` sent the app
password straight through it.

These tests do not read the code. They stand up a TLS server with a certificate
signed by nobody, point mailmail at it, and check that mailmail refuses to talk --
which is the only claim worth making here, and the only one an implementation
change cannot quietly break.
"""

import socket
import ssl
import subprocess
import threading

import pytest

from mailmail import Mailer, Message, SmtpAccount
from mailmail.provider import MailProvider

pytestmark = pytest.mark.skipif(
    subprocess.run(["which", "openssl"], capture_output=True).returncode != 0,
    reason="needs openssl to mint the attacker's certificate",
)


@pytest.fixture(scope="module")
def attacker_certificate(tmp_path_factory):
    """A certificate for smtp.example.com, signed by nobody at all."""
    directory = tmp_path_factory.mktemp("attacker")
    certificate, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(certificate),
            "-days", "1", "-nodes", "-subj", "/CN=smtp.example.com",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


class ImpostorServer:
    """An SMTP server holding a certificate no authority vouches for.

    Speaks just enough of the protocol to reach the point where a client that
    does not check would hand over its password: a greeting, an EHLO that
    advertises STARTTLS, and the TLS upgrade itself.
    """

    def __init__(self, certificate, key):
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certificate, key)
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self.handshake_succeeded = False
        self.credentials_seen: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        connection.sendall(b"220 smtp.example.com ESMTP\r\n")
        stream = connection.makefile("rb")
        while True:
            try:
                line = stream.readline()
            except OSError:
                return
            if not line:
                return
            command = line.decode(errors="replace").strip().upper()
            if command.startswith(("EHLO", "HELO")):
                connection.sendall(
                    b"250-smtp.example.com\r\n250-STARTTLS\r\n250 AUTH PLAIN LOGIN\r\n"
                )
            elif command == "STARTTLS":
                connection.sendall(b"220 Go ahead\r\n")
                try:
                    connection = self._context.wrap_socket(connection, server_side=True)
                except (ssl.SSLError, OSError):
                    return  # the client checked, and walked away. Good.
                self.handshake_succeeded = True
                stream = connection.makefile("rb")
            elif command.startswith("AUTH"):
                self.credentials_seen.append(line)
                connection.sendall(b"235 OK\r\n")
            else:
                connection.sendall(b"250 OK\r\n")

    def close(self):
        self._socket.close()


@pytest.fixture
def impostor(attacker_certificate):
    server = ImpostorServer(*attacker_certificate)
    yield server
    server.close()


def _account_pointed_at(port) -> SmtpAccount:
    provider = MailProvider(
        name              = "impostor",
        smtp_host         = "127.0.0.1",
        smtp_port         = port,
        security          = "starttls",
        max_message_bytes = 35_882_577,
        max_recipients    = 100,
        blocked_extensions = frozenset(),
        login_requirements = "n/a",
    )
    return SmtpAccount(name="test", username="me@example.com", provider=provider)


class TestAServerNobodyVouchesFor:
    def test_the_handshake_is_refused(self, impostor, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "app-password")
        message = Message.compose(subject="s", body="b", to="lead@example.com")

        with (
            pytest.raises(ssl.SSLCertVerificationError),
            Mailer(_account_pointed_at(impostor.port)) as mailer,
        ):
            mailer.send(message)

        assert not impostor.handshake_succeeded

    def test_the_password_never_reaches_it(self, impostor, monkeypatch):
        """The finding itself, stated as the thing that must not happen.

        The handshake assertion above could pass for the wrong reason some day --
        a refused connection, a protocol change. This one cannot: it fails only
        if the secret actually left the machine.
        """
        monkeypatch.setenv("MAILMAIL_PASSWORD", "app-password")
        message = Message.compose(subject="s", body="b", to="lead@example.com")

        with (
            pytest.raises(ssl.SSLCertVerificationError),
            Mailer(_account_pointed_at(impostor.port)) as mailer,
        ):
            mailer.send(message)

        assert impostor.credentials_seen == []
