"""GATT identifiers, opcodes and field ids of the A2687 protocol.

Values follow Charker, cross-checked there against SolixBLE (bb2d398) and
Anker_Prime_160W_WebBLE (ad4355d).
"""

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

# Negotiation opcodes (group 0x01).
OP_INITIAL_CONNECT = 0x0001
OP_CAPABILITY = 0x0003
OP_SET_CAPABILITY = 0x0005
OP_PUBLIC_KEY = 0x0021
OP_AES_METADATA = 0x0022
OP_USER_AUTH = 0x0027
OP_BASE_INFO = 0x0029

# Session opcodes (group 0x0F).
OP_READ_ALL = 0x0200
OP_LANGUAGE = 0x0202
OP_SCREEN_TIMEOUT = 0x0203
OP_BRIGHTNESS = 0x0204
OP_FIXED_ALLOCATION = 0x0205
OP_CHARGING_MODE = 0x0206
OP_PORT_OUTPUT = 0x0207
OP_PORT_TIMER = 0x0209
OP_BIND_SUCCESS = 0x020A
# Shared by the realtime trigger (byte block) and the orientation write (u8).
OP_REALTIME_TRIGGER = 0x020B
OP_ORIENTATION = 0x020B
OP_GYROSCOPE = 0x020D
OP_SET_SCREENSAVER = 0x021F
OP_COVER_TRANSFER_START = 0x0220
OP_COVER_TRANSFER_CHUNK = 0x0221
OP_REALTIME_REPORT = 0x0300

# Field ids.
FIELD_A1 = 0xA1
FIELD_A2 = 0xA2
FIELD_A3 = 0xA3
FIELD_A4 = 0xA4
FIELD_A5 = 0xA5
FIELD_A6 = 0xA6
FIELD_A7 = 0xA7
FIELD_A8 = 0xA8
FIELD_SCREEN_TIMEOUT = 0xA8
FIELD_BRIGHTNESS = 0xA9
FIELD_CHARGING_MODE = 0xAA
FIELD_CONTROL_C1 = 0xAC
FIELD_CONTROL_C2 = 0xAD
FIELD_CONTROL_C3 = 0xAE
FIELD_ORIENTATION = 0xAF
FIELD_GYROSCOPE = 0xB2
FIELD_CONNECTED_DEVICES = 0xB4
# Found by Charker in live frames: the screen picture shown (bytes 2-3).
FIELD_COVER_STATE = 0xE1
FIELD_URL = 0xFD
FIELD_TIMESTAMP = 0xFE

# Every session command carries A1 = 0x21.
SESSION_ACTION = 0x21

# Port telemetry and control fields, C1..C3 in order.
PORT_FIELDS = (FIELD_A5, FIELD_A6, FIELD_A7)
CONTROL_FIELDS = (FIELD_CONTROL_C1, FIELD_CONTROL_C2, FIELD_CONTROL_C3)

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
