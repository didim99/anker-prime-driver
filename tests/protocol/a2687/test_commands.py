"""A2687 command builder tests."""

from ankerprime.protocol.a2687 import commands
from ankerprime.protocol.a2687.codes import (
    NEGOTIATION_GROUP, OP_BIND_SUCCESS, OP_BRIGHTNESS,
    OP_CHARGING_MODE, OP_PORT_OUTPUT, OP_PORT_TIMER,
    OP_READ_ALL, OP_REALTIME_TRIGGER, SESSION_GROUP)
from ankerprime.protocol.a2687.commands import Encryption
from ankerprime.protocol.a2687.tlv import Payload


EPOCH = 1_700_000_000
STAMP = 'fe0400f15365'


def test_charging_mode_matches_charker_vector() -> None:
    message = commands.set_charging_mode(1, EPOCH)
    assert message.plaintext.hex() == 'a10121a2020101' + STAMP
    assert (message.group, message.opcode) == (SESSION_GROUP,
                                               OP_CHARGING_MODE)
    assert message.encryption == Encryption.SESSION


def test_read_all_and_trigger() -> None:
    read = commands.read_all(EPOCH)
    assert (read.opcode, read.plaintext.hex()) == (OP_READ_ALL,
                                                   'a10121' + STAMP)
    trigger = commands.realtime_trigger(EPOCH)
    assert trigger.opcode == OP_REALTIME_TRIGGER
    assert trigger.plaintext.hex() == (
        'a10121a20a04010003150101000000' + STAMP)


def test_realtime_probe_carries_owner_only_when_known() -> None:
    anonymous = Payload.parse(commands.realtime_probe('DE', None,
                                                      EPOCH).plaintext)
    assert anonymous.get(0xA2) == b'\x04DE'
    assert anonymous.get(0xA3) is None
    assert anonymous.get(0xA5) == b'\x01\x01'
    owned = commands.realtime_probe('DE', 'owner', EPOCH)
    assert owned.opcode == OP_BIND_SUCCESS
    assert Payload.parse(owned.plaintext).get(0xA3) == b'\x04owner'


def test_port_commands() -> None:
    output = commands.set_port_output(2, True, EPOCH)
    assert output.opcode == OP_PORT_OUTPUT
    assert output.plaintext.hex() == 'a10121a2020102a3020101' + STAMP
    timer = commands.set_port_timer(0, 3600, EPOCH)
    assert timer.opcode == OP_PORT_TIMER
    assert timer.plaintext.hex() == 'a10121a2020100a30504100e0000' + STAMP


def test_single_value_setting() -> None:
    message = commands.set_setting(OP_BRIGHTNESS, 80, EPOCH)
    assert message.opcode == OP_BRIGHTNESS
    assert message.plaintext.hex() == 'a10121a2020150' + STAMP


def test_negotiation_message() -> None:
    message = commands.negotiation(0x0001, [(0xA1, b'\x01\x02\x03\x04')],
                                   Encryption.NEGOTIATION)
    assert message.group == NEGOTIATION_GROUP
    assert message.plaintext.hex() == 'a10401020304'
