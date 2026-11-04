"""Login crypto vectors and server selection tests."""

import pytest

from ankerprime.cloud.crypto import LoginCipher, encrypt_password
from ankerprime.cloud.regions import (
    COM_SERVER, CN_SERVER, EU_SERVER, REGIONS,
    home_server, overseas_server, server_for)
from ankerprime.model import InvalidParameterError


# Vectors produced with the reference client's primitives from a fixed scalar.
CLIENT_PRIVATE_VALUE = bytes.fromhex(
    '7dfbea61cd95cee49c458ad7419e817f1ade9a66136de3c7d5787af1458e39f4')
SHARED_KEY = bytes.fromhex(
    '9eab5f3f93c514a483763eb8dd755c7540f391a00d9fd539fb1124fe12cbede7')
CLIENT_PUBLIC_KEY = (
    '04060ea168f232aedb37fb2d120c49180329ac72ab5ec3eb8fd30a2f252dc5e151'
    'dabccd9b1dc1e288704ca760a0d8c918e5c94823a1f609a4bf07fb4c33ee2190')


def test_shared_key_matches_reference() -> None:
    cipher = LoginCipher.from_private_value(CLIENT_PRIVATE_VALUE)
    assert cipher.shared_key == SHARED_KEY


def test_client_public_key_is_uncompressed_point() -> None:
    cipher = LoginCipher.from_private_value(CLIENT_PRIVATE_VALUE)
    assert cipher.public_key_hex == CLIENT_PUBLIC_KEY
    assert len(bytes.fromhex(cipher.public_key_hex)) == 65


@pytest.mark.parametrize(('plaintext', 'expected'), [
    ('hunter2', 'wCJPQmgOLDIjflE00kSqkA=='),
    ('', 'zFCwJ1eg7fuGOK/0t4G4kg=='),
    ('a' * 16, '1aU4/yoR9bUa6XpgKmuE8VDoHvRr7DuiMxAXGS+SiH4='),
    ('密码测试', 'l1+tJ01LiarN+CqBudbmjw=='),
])
def test_password_encryption_vectors(plaintext: str, expected: str) -> None:
    assert encrypt_password(plaintext, SHARED_KEY) == expected


def test_encryption_rejects_wrong_key_size() -> None:
    with pytest.raises(InvalidParameterError):
        encrypt_password('x', bytes(16))


def test_fresh_ciphers_use_fresh_keys() -> None:
    assert LoginCipher().public_key_hex != LoginCipher().public_key_hex


@pytest.mark.parametrize('country', ['JP', 'de', 'HK', 'TW', 'SG', 'KR', 'IN',
                                     'ZA', 'ZZ'])
def test_eu_served_countries(country: str) -> None:
    assert server_for(country) == EU_SERVER


@pytest.mark.parametrize('country', ['AR', 'AU', 'BR', 'CA', 'MX', 'NZ', 'US'])
def test_com_served_countries(country: str) -> None:
    assert server_for(country) == COM_SERVER


def test_mainland_server() -> None:
    assert server_for('cn') == CN_SERVER


def test_region_table() -> None:
    assert len(REGIONS) == 72
    assert all(len(code) == 2 and code.isupper() for code in REGIONS)
    assert list(REGIONS) == sorted(REGIONS)


@pytest.mark.parametrize(('domain', 'expected'), [
    ('ankerpower-api-eu.anker.com', EU_SERVER),
    ('https://ankerpower-api.anker.com/', COM_SERVER),
    (' HTTPS://ANKERPOWER-API-EU.ANKER.COM ', EU_SERVER),
    ('https://ankerpower-api-eu.anker.com.example.org', None),
    ('aiot-api-cn.anker.com.cn', None),
    ('', None),
    (None, None),
])
def test_overseas_server_allowlist(domain: str | None,
                                   expected: str | None) -> None:
    assert overseas_server(domain) == expected


@pytest.mark.parametrize(('domain', 'country', 'expected'), [
    ('ankerpower-api-eu.anker.com', 'US', EU_SERVER),
    ('', 'JP', EU_SERVER),
    (None, 'US', COM_SERVER),
    ('', 'CN', None),
    ('', 'ZZ', None),
    ('', '', None),
])
def test_home_server(
        domain: str | None, country: str, expected: str | None) -> None:
    assert home_server(domain, country) == expected
