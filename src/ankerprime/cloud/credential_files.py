"""Client certificate files for the MQTT TLS connection.

Python's ``ssl`` loads a client identity from files only, so the PEM text goes
into a caller-provided directory, readable by the owner alone. The functions
block; async callers run them in an executor.
"""

import ssl
from pathlib import Path

from ankerprime.cloud.client import MqttCredentials
from ankerprime.storage.files import write_private_file


CERTIFICATE_FILE = 'mqtt-certificate.pem'
PRIVATE_KEY_FILE = 'mqtt-private-key.pem'


def write_credential_files(credentials: MqttCredentials,
                           directory: Path) -> tuple[Path, Path]:
    """Write the certificate and key; return their paths."""

    certificate = directory / CERTIFICATE_FILE
    private_key = directory / PRIVATE_KEY_FILE
    write_private_file(certificate, credentials.certificate_pem)
    write_private_file(private_key, credentials.private_key_pem)
    return certificate, private_key


def remove_credential_files(directory: Path) -> None:
    for name in (CERTIFICATE_FILE, PRIVATE_KEY_FILE):
        (directory / name).unlink(missing_ok=True)


def create_tls_context(certificate: Path, private_key: Path) -> ssl.SSLContext:
    """TLS 1.2+ context trusting the system roots, with the client identity."""

    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    return context
