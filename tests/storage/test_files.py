"""Private file, JSON store and MQTT credential file tests."""

import ssl
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat)
from cryptography.x509.oid import NameOID

from ankerprime.cloud.credential_files import (
    create_tls_context, remove_credential_files, write_credential_files)
from ankerprime.model import InvalidParameterError, ProtocolError
from ankerprime.storage.files import write_private_file
from ankerprime.storage.json_store import JsonFileStore
from tests.utils import make_mqtt_credentials


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_file_is_owner_only_and_replaced(tmp_path: Path) -> None:
    path = tmp_path / 'nested' / 'secret.txt'
    write_private_file(path, 'one')
    write_private_file(path, 'two')
    assert path.read_text() == 'two'
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    assert sorted(child.name for child in path.parent.iterdir()) == [
        'secret.txt']


async def test_json_store_round_trip(tmp_path: Path) -> None:
    store = JsonFileStore(tmp_path / 'store')
    assert await store.load('account') is None
    await store.save('account', {'token': 'abc', 'items': [1, 2]})
    assert await store.load('account') == {'token': 'abc', 'items': [1, 2]}
    assert mode(tmp_path / 'store' / 'account.json') == 0o600
    await store.delete('account')
    await store.delete('account')
    assert await store.load('account') is None


@pytest.mark.parametrize('key', ['', '../escape', '.hidden', 'a/b', 'x' * 200])
async def test_json_store_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(InvalidParameterError):
        await JsonFileStore(tmp_path).save(key, {})


async def test_json_store_reports_corrupt_documents(tmp_path: Path) -> None:
    (tmp_path / 'bad.json').write_text('[1, 2]')
    with pytest.raises(ProtocolError):
        await JsonFileStore(tmp_path).load('bad')


def test_credential_files(tmp_path: Path) -> None:
    credentials = make_mqtt_credentials(endpoint='host',
                                        certificate_pem='CERT\n',
                                        private_key_pem='KEY\n')
    certificate, key = write_credential_files(credentials, tmp_path / 'mqtt')
    assert certificate.read_text() == 'CERT\n'
    assert key.read_text() == 'KEY\n'
    assert mode(certificate) == 0o600 and mode(key) == 0o600
    remove_credential_files(tmp_path / 'mqtt')
    remove_credential_files(tmp_path / 'mqtt')
    assert not certificate.exists() and not key.exists()


def test_tls_context_rejects_invalid_identity(tmp_path: Path) -> None:
    certificate = tmp_path / 'cert.pem'
    key = tmp_path / 'key.pem'
    certificate.write_text('not a certificate')
    key.write_text('not a key')
    with pytest.raises(ssl.SSLError):
        create_tls_context(certificate, key)


def test_tls_context_loads_generated_identity(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'thing')])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name)
                   .issuer_name(name).public_key(key.public_key())
                   .serial_number(1).not_valid_before(now)
                   .not_valid_after(now + timedelta(days=1))
                   .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL,
                                NoEncryption()).decode()
    certificate_pem = certificate.public_bytes(Encoding.PEM).decode()
    credentials = make_mqtt_credentials(endpoint='host',
                                        certificate_pem=certificate_pem,
                                        private_key_pem=key_pem)
    paths = write_credential_files(credentials, tmp_path)
    context = create_tls_context(*paths)
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
