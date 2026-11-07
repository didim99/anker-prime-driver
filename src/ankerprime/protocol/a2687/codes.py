"""GATT identifiers, opcodes and field ids of the A2687 protocol.

Values follow Charker, cross-checked there against SolixBLE (bb2d398) and
Anker_Prime_160W_WebBLE (ad4355d).
"""

from enum import IntEnum

from ankerprime.model.enums import (
    CableRating, ChargingMode, ChargingProtocol, DisplayLanguage,
    FixedAllocation, OutputId, PortId, ScreenOrientation, ScreenTimeout)


# GATT. The advertised FF09 service is shared by the Anker Solix/Prime family,
# so it is a discovery filter, not proof of the model.
ADVERTISED_SERVICE = '0000ff09-0000-1000-8000-00805f9b34fb'
PRIMARY_SERVICE = '8c850001-0302-41c5-b46e-cf057c562025'
WRITE_CHARACTERISTIC = '8c850002-0302-41c5-b46e-cf057c562025'
NOTIFY_CHARACTERISTIC = '8c850003-0302-41c5-b46e-cf057c562025'
NAME_PREFIX = 'ASHDJW'

# Frame groups and command flag bits.
NEGOTIATION_GROUP = 0x01
SESSION_GROUP = 0x0F
ENCRYPTED_FLAG = 0x4000
RESPONSE_FLAG = 0x0800


class Opcode(IntEnum):
    """Command opcodes; ``ORIENTATION`` is an alias of ``REALTIME_TRIGGER``."""

    # Negotiation (group 0x01).
    INITIAL_CONNECT = 0x0001
    CAPABILITY = 0x0003
    SET_CAPABILITY = 0x0005
    PUBLIC_KEY = 0x0021
    AES_METADATA = 0x0022
    USER_AUTH = 0x0027
    BASE_INFO = 0x0029

    # Session (group 0x0F).
    READ_ALL = 0x0200
    LANGUAGE = 0x0202
    SCREEN_TIMEOUT = 0x0203
    BRIGHTNESS = 0x0204
    FIXED_ALLOCATION = 0x0205
    CHARGING_MODE = 0x0206
    PORT_OUTPUT = 0x0207
    PORT_TIMER = 0x0209
    BIND_SUCCESS = 0x020A
    # Shared by the realtime trigger (byte block) and the orientation write (u8).
    REALTIME_TRIGGER = 0x020B
    ORIENTATION = 0x020B
    GYROSCOPE = 0x020D
    SET_SCREENSAVER = 0x021F
    COVER_TRANSFER_START = 0x0220
    COVER_TRANSFER_CHUNK = 0x0221
    REALTIME_REPORT = 0x0300


class FieldId(IntEnum):
    """Field ids; ``SCREEN_TIMEOUT`` is an alias of ``A8``."""

    A1 = 0xA1
    A2 = 0xA2
    A3 = 0xA3
    A4 = 0xA4
    A5 = 0xA5
    A6 = 0xA6
    A7 = 0xA7
    A8 = 0xA8
    SCREEN_TIMEOUT = 0xA8
    BRIGHTNESS = 0xA9
    CHARGING_MODE = 0xAA
    CONTROL_C1 = 0xAC
    CONTROL_C2 = 0xAD
    CONTROL_C3 = 0xAE
    ORIENTATION = 0xAF
    GYROSCOPE = 0xB2
    CONNECTED_DEVICES = 0xB4
    # Found by Charker in live frames: the screen picture shown (bytes 2-3).
    COVER_STATE = 0xE1
    URL = 0xFD
    TIMESTAMP = 0xFE


# Every session command carries A1 = 0x21.
SESSION_ACTION = 0x21

# Port telemetry and control fields, C1..C3 in order.
PORT_FIELDS = (FieldId.A5, FieldId.A6, FieldId.A7)
CONTROL_FIELDS = (FieldId.CONTROL_C1, FieldId.CONTROL_C2, FieldId.CONTROL_C3)

# Port order on the wire: the index is the port code of commands.
PORTS = (PortId.C1, PortId.C2, PortId.C3)
OUTPUTS = (OutputId.C1, OutputId.C2, OutputId.C3)

# Wire codes of settings. Reports have shown 0 and 1 only; custom is 2 in the
# official app (an earlier reading of 4 was a tagged constant).
CHARGING_MODE_CODES = {
    ChargingMode.AI: 0,
    ChargingMode.STANDARD: 1,
    ChargingMode.CUSTOM: 2,
}
DISPLAY_LANGUAGE_CODES = {
    DisplayLanguage.ENGLISH: 0,
    DisplayLanguage.CHINESE_SIMPLIFIED: 1,
    DisplayLanguage.JAPANESE: 2,
    DisplayLanguage.GERMAN: 3,
}
FIXED_ALLOCATION_CODES = {
    FixedAllocation.C1_PRIORITY: 0,
    FixedAllocation.DUAL_LAPTOP: 1,
}
SCREEN_ORIENTATION_CODES = {
    ScreenOrientation.UP: 0,
    ScreenOrientation.LEFT: 1,
    ScreenOrientation.DOWN: 2,
    ScreenOrientation.RIGHT: 3,
}
SCREEN_TIMEOUT_CODES = {
    ScreenTimeout.THIRTY_SECONDS: 0,
    ScreenTimeout.ONE_MINUTE: 1,
    ScreenTimeout.FIVE_MINUTES: 2,
    ScreenTimeout.THIRTY_MINUTES: 3,
    ScreenTimeout.TWELVE_HOURS: 4,
}

# Code 3 means no cable detected.
CABLE_RATINGS = {
    0: CableRating.THREE_AMP,
    1: CableRating.FIVE_AMP,
    2: CableRating.EPR,
}
# Code 0 means no vendor profile.
CHARGING_PROTOCOLS = {
    1: ChargingProtocol.APPLE_PD,
    2: ChargingProtocol.SAMSUNG_FAST,
    3: ChargingProtocol.SAMSUNG_SUPER_FAST,
    5: ChargingProtocol.HUAWEI_SCP,
    6: ChargingProtocol.XIAOMI_HYPERCHARGE,
}
