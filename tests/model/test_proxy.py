"""Proxy addresses."""

import pytest

from ankerprime.model import InvalidParameterError
from ankerprime.model.proxy import ProxyAddress


def test_parse_with_credentials() -> None:
    proxy = ProxyAddress.parse('http://us%40er:p%3Aw@proxy.lan:3128')
    assert (proxy.host, proxy.port) == ('proxy.lan', 3128)
    assert (proxy.username, proxy.password) == ('us@er', 'p:w')
    assert proxy.url == 'http://us%40er:p%3Aw@proxy.lan:3128'
    assert 'p:w' not in repr(proxy) and 'p%3Aw' not in repr(proxy)


def test_parse_defaults_and_ipv6() -> None:
    proxy = ProxyAddress.parse('http://127.0.0.1/')
    assert proxy.port == 80 and proxy.username is None
    assert proxy.url == 'http://127.0.0.1:80'
    assert ProxyAddress.parse('http://[::1]:8080').url == 'http://[::1]:8080'


@pytest.mark.parametrize('text, message', [
    ('https://proxy:443', 'only http://'),
    ('socks5://proxy:1080', 'only http://'),
    ('proxy:3128', 'only http://'),
    ('http://proxy:3128/path', 'no path'),
    ('http://proxy:99999', 'invalid proxy URL'),
    ('http://:3128', 'host missing'),
])
def test_invalid(text: str, message: str) -> None:
    with pytest.raises(InvalidParameterError, match=message):
        ProxyAddress.parse(text)
