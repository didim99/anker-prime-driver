"""Countries Anker accepts at login and the API server each one uses.

The country code is sent with the login and selects the server; a wrong one
fails the login or lists no devices.
"""

EU_SERVER = 'https://ankerpower-api-eu.anker.com'
COM_SERVER = 'https://ankerpower-api.anker.com'
CN_SERVER = 'https://aiot-api-cn.anker.com.cn'
OVERSEAS_SERVERS = (EU_SERVER, COM_SERVER)

# The charging app's own table; every other overseas country is EU-served.
COM_COUNTRIES = frozenset({'AR', 'AU', 'BR', 'CA', 'MX', 'NZ', 'US'})

# Anker's country table; UK and EL are Anker's codes for GB and GR.
REGIONS = {
    'AL': 'Albania',
    'AM': 'Armenia',
    'AR': 'Argentina',
    'AT': 'Austria',
    'AU': 'Australia',
    'AZ': 'Azerbaijan',
    'BA': 'Bosnia and Herzegovina',
    'BE': 'Belgium',
    'BG': 'Bulgaria',
    'BR': 'Brazil',
    'BY': 'Belarus',
    'CA': 'Canada',
    'CH': 'Switzerland',
    'CN': 'Mainland China',
    'CY': 'Cyprus',
    'CZ': 'Czechia',
    'DE': 'Germany',
    'DK': 'Denmark',
    'DZ': 'Algeria',
    'EE': 'Estonia',
    'EG': 'Egypt',
    'EL': 'Greece',
    'ES': 'Spain',
    'FI': 'Finland',
    'FR': 'France',
    'GE': 'Georgia',
    'HK': 'Hong Kong',
    'HR': 'Croatia',
    'HU': 'Hungary',
    'IE': 'Ireland',
    'IL': 'Israel',
    'IN': 'India',
    'IS': 'Iceland',
    'IT': 'Italy',
    'JO': 'Jordan',
    'JP': 'Japan',
    'KR': 'South Korea',
    'LB': 'Lebanon',
    'LI': 'Liechtenstein',
    'LT': 'Lithuania',
    'LU': 'Luxembourg',
    'LV': 'Latvia',
    'LY': 'Libya',
    'MA': 'Morocco',
    'MD': 'Moldova',
    'ME': 'Montenegro',
    'MK': 'North Macedonia',
    'MT': 'Malta',
    'MX': 'Mexico',
    'NG': 'Nigeria',
    'NL': 'Netherlands',
    'NO': 'Norway',
    'NZ': 'New Zealand',
    'PL': 'Poland',
    'PS': 'Palestine',
    'PT': 'Portugal',
    'RO': 'Romania',
    'RS': 'Serbia',
    'RU': 'Russia',
    'SE': 'Sweden',
    'SG': 'Singapore',
    'SI': 'Slovenia',
    'SK': 'Slovakia',
    'SY': 'Syria',
    'TN': 'Tunisia',
    'TR': 'Turkey',
    'TW': 'Taiwan',
    'UA': 'Ukraine',
    'UK': 'United Kingdom',
    'US': 'United States',
    'XK': 'Kosovo',
    'ZA': 'South Africa',
}

# Mainland China signs in by SMS code, which this library does not support.
UNSUPPORTED_REGIONS = frozenset({'CN'})


def is_known_region(code: str) -> bool:
    return code.upper() in REGIONS


def server_for(country: str) -> str:
    """Return the API server of a country code; unknown codes use EU."""

    country = country.upper()
    if country == 'CN':
        return CN_SERVER
    return COM_SERVER if country in COM_COUNTRIES else EU_SERVER


def overseas_server(domain: str | None) -> str | None:
    """Map a server domain onto the overseas allowlist, None if not on it.

    Anker sends the domain with or without a scheme; anything else is ignored
    so a response can never point the password or token at another host.
    """

    if not domain or not domain.strip():
        return None
    domain = domain.strip().lower()
    if not domain.startswith('https://'):
        domain = f'https://{domain}'
    domain = domain.rstrip('/')
    return domain if domain in OVERSEAS_SERVERS else None


def home_server(domain: str | None, account_country: str | None) -> str | None:
    """Return the overseas server a login response says the account uses.

    An explicit domain wins; otherwise the account's own country decides.
    Unknown and mainland codes leave the route unchanged.
    """

    named = overseas_server(domain)
    if named is not None:
        return named
    if not account_country or not is_known_region(account_country):
        return None
    server = server_for(account_country)
    return None if server == CN_SERVER else server
