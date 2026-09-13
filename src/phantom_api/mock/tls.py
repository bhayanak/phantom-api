"""Self-signed TLS, generated at start.

Clients of the systems worth simulating almost always disable certificate
verification -- so a generated, ephemeral certificate is sufficient and avoids
asking anyone to run a CA. The private key stays in a temporary file that is
removed on exit unless explicitly written out.
"""

from __future__ import annotations

import atexit
import contextlib
import datetime
import ipaddress
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TlsMaterial:
    cert_path: Path
    key_path: Path
    ephemeral: bool


class TlsUnavailable(RuntimeError):
    """Raised when TLS is requested but the optional dependency is absent."""


def generate_self_signed(
    hostname: str = "localhost", *, out_dir: Path | None = None, days: int = 365
) -> TlsMaterial:
    """Create a certificate and key valid for ``hostname`` plus loopback."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise TlsUnavailable(
            "TLS needs the 'cryptography' package: pip install 'phantomapi-server[mock]'"
        ) from exc

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    alt_names: list[x509.GeneralName] = [x509.DNSName(hostname), x509.DNSName("localhost")]
    with contextlib.suppress(ValueError):
        alt_names.append(x509.IPAddress(ipaddress.ip_address(hostname)))
    alt_names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    ephemeral = out_dir is None
    target = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="phantom-tls-"))
    target.mkdir(parents=True, exist_ok=True)
    cert_path = target / "mock-cert.pem"
    key_path = target / "mock-key.pem"

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)

    if ephemeral:
        atexit.register(_cleanup, cert_path, key_path, target)

    return TlsMaterial(cert_path=cert_path, key_path=key_path, ephemeral=ephemeral)


def _cleanup(cert_path: Path, key_path: Path, directory: Path) -> None:  # pragma: no cover
    for path in (cert_path, key_path):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        directory.rmdir()
