"""A2345 message decoder tests."""

import pytest

from ankerprime.model import PortId
from ankerprime.protocol.a2345.fields import MessageError, MessageErrorKind
from ankerprime.protocol.a2345.frame import Frame, Tlv, decode_frame
from ankerprime.protocol.a2345.messages import (
    MSG_STATUS_SNAPSHOT, PORTS, RawPortReading, decode_acknowledgement,
    decode_realtime, decode_status_snapshot, decode_version_info)
from tests.protocol.a2345.fixtures import (
    REALTIME_ACKNOWLEDGEMENT, REALTIME_CHINA_FIRMWARE,
    REALTIME_NEW_FIRMWARE, REALTIME_OLD_FIRMWARE, VERSION_INFO)


def with_fields(frame: Frame, fields: list[Tlv],
                message_type: int | None = None) -> Frame:
    if message_type is None:
        message_type = frame.message_type
    return Frame(version=frame.version, speaker=frame.speaker,
                 group=frame.group, message_type=message_type,
                 increment=frame.increment, fields=fields, length=frame.length)


def replace_field(frame: Frame, tag: int, raw: bytes) -> Frame:
    fields = [Tlv(tag, raw) if field.tag == tag else field
              for field in frame.fields]
    return with_fields(frame, fields)


def raw_of(frame: Frame, tag: int) -> bytes:
    field = frame.field(tag)
    assert field is not None
    return field.raw


def electrical(reading: RawPortReading) -> tuple[int, int, int, int]:
    return (reading.status, reading.millivolts, reading.milliamps,
            reading.centiwatts)


def snapshot_frame(tail: bytes = b'', extra: list[Tlv] | None = None) -> Frame:
    fields = []
    for offset, port in enumerate(PORTS):
        base = 1000 + offset * 100
        status = 0 if port == PortId.A2 else 1
        payload = (bytes((0x04, status)) + base.to_bytes(2, 'little')
                   + (base + 1).to_bytes(2, 'little')
                   + (base + 2).to_bytes(2, 'little') + tail)
        fields.append(Tlv(0xA4 + offset, payload))
    return Frame(version=3, speaker=1, group=15,
                 message_type=MSG_STATUS_SNAPSHOT, increment=None,
                 fields=fields + (extra or []), length=0)


def test_decodes_six_port_realtime_telemetry_and_scaling() -> None:
    telemetry = decode_realtime(decode_frame(REALTIME_OLD_FIRMWARE))
    c1 = telemetry.ports[PortId.C1]
    assert telemetry.response_marker == 0x34
    assert len(telemetry.ports) == 6
    assert electrical(c1) == (1, 14_984, 1_632, 2_384)
    assert c1.voltage == pytest.approx(14.984)
    assert c1.current == pytest.approx(1.632)
    assert c1.power == pytest.approx(23.84)
    assert telemetry.ports[PortId.C3].voltage == pytest.approx(5.0)
    assert telemetry.ports[PortId.A1].power == 0
    assert telemetry.ports[PortId.A2].power == 0
    assert telemetry.timestamp == 0x12345678
    assert telemetry.unknown_fields == ()


def test_old_firmware_without_a9_remains_valid() -> None:
    telemetry = decode_realtime(decode_frame(REALTIME_OLD_FIRMWARE))
    assert telemetry.provisional_metadata is None
    assert len(telemetry.metadata) == 4


def test_preserves_a8_raw_words_and_sentinels() -> None:
    slots = decode_realtime(decode_frame(REALTIME_OLD_FIRMWARE)).metadata
    assert slots[0].port == PortId.C1
    assert (slots[0].word0, slots[0].word1) == (0x05AC, 0x12A8)
    assert slots[0].raw == bytes((0xAC, 0x05, 0xA8, 0x12))
    assert slots[1].is_unidentified and slots[2].is_unidentified
    assert slots[3].is_all_ones
    assert (slots[3].word0, slots[3].word1) == (0xFFFF, 0xFFFF)


def test_new_firmware_preserves_provisional_a9_words() -> None:
    slots = decode_realtime(
        decode_frame(REALTIME_NEW_FIRMWARE)).provisional_metadata
    assert slots is not None and len(slots) == 4
    assert slots[0].port == PortId.C1
    assert (slots[0].word0, slots[0].word1) == (1, 31)
    assert slots[0].raw == bytes((0x01, 0x00, 0x1F, 0x00))
    assert slots[1].is_all_ones
    assert slots[2].is_unidentified
    assert slots[3].is_all_ones


def test_rejects_short_port_struct() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    frame = replace_field(frame, 0xA2, raw_of(frame, 0xA2)[:-1])
    with pytest.raises(MessageError) as exc_info:
        decode_realtime(frame)
    assert exc_info.value.kind == MessageErrorKind.INVALID_FIELD_LENGTH
    assert exc_info.value.tag == 0xA2


def test_rejects_wrong_port_value_type() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    frame = replace_field(frame, 0xA2, b'\x03' + raw_of(frame, 0xA2)[1:])
    with pytest.raises(MessageError) as exc_info:
        decode_realtime(frame)
    assert exc_info.value.kind == MessageErrorKind.UNEXPECTED_FIELD_TYPE


def test_china_firmware_longer_port_fields_keep_legacy_readings() -> None:
    legacy = decode_realtime(decode_frame(REALTIME_OLD_FIRMWARE))
    telemetry = decode_realtime(decode_frame(REALTIME_CHINA_FIRMWARE))
    for port in PORTS:
        assert electrical(telemetry.ports[port]) == electrical(legacy.ports[port])
        assert legacy.ports[port].cable is None
    c1 = telemetry.ports[PortId.C1]
    assert (c1.cable, c1.fast_charge, c1.device_brand) == (1, 1, 1)
    assert telemetry.ports[PortId.C3].cable == 0
    assert telemetry.metadata == legacy.metadata
    assert telemetry.timestamp == legacy.timestamp
    assert telemetry.unknown_fields == ()


def test_port_tail_follows_official_length_gates() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    raw = raw_of(frame, 0xA2)
    reading = decode_realtime(replace_field(frame, 0xA2, raw + b'\x01'))
    assert reading.ports[PortId.C1].cable is None
    c1 = decode_realtime(
        replace_field(frame, 0xA2, raw + b'\x01\x02')).ports[PortId.C1]
    assert (c1.cable, c1.fast_charge, c1.device_brand) == (1, 2, None)


def test_unknown_port_status_is_kept_raw() -> None:
    reading = RawPortReading(2, 5000, 1000, 500)
    assert reading.status == 2


def test_rejects_missing_core_field() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    frame = with_fields(frame, [field for field in frame.fields
                                if field.tag != 0xA7])
    with pytest.raises(MessageError) as exc_info:
        decode_realtime(frame)
    assert exc_info.value.kind == MessageErrorKind.MISSING_FIELD
    assert exc_info.value.tag == 0xA7


def test_duplicate_metadata_is_kept_as_unknown() -> None:
    frame = decode_frame(REALTIME_OLD_FIRMWARE)
    a8 = frame.field(0xA8)
    assert a8 is not None
    telemetry = decode_realtime(with_fields(frame, [*frame.fields, a8]))
    assert len(telemetry.ports) == 6
    assert telemetry.metadata == ()
    assert [field.tag for field in telemetry.unknown_fields] == [0xA8, 0xA8]


@pytest.mark.parametrize('corrupt', ['truncated', 'wrong_type'])
def test_malformed_a9_does_not_discard_core_telemetry(corrupt: str) -> None:
    frame = decode_frame(REALTIME_NEW_FIRMWARE)
    raw = raw_of(frame, 0xA9)
    raw = raw[:-1] if corrupt == 'truncated' else b'\x03' + raw[1:]
    telemetry = decode_realtime(replace_field(frame, 0xA9, raw))
    assert len(telemetry.ports) == 6
    assert telemetry.provisional_metadata is None
    assert [field.tag for field in telemetry.unknown_fields] == [0xA9]


def test_rejects_other_message_type() -> None:
    with pytest.raises(MessageError) as exc_info:
        decode_realtime(decode_frame(VERSION_INFO))
    assert exc_info.value.kind == MessageErrorKind.UNEXPECTED_MESSAGE_TYPE


def test_decodes_version_and_component_fields() -> None:
    version = decode_version_info(decode_frame(VERSION_INFO))
    assert version.hardware_version == '1.0.0'
    assert version.software_version == '2.1.1.6'
    assert version.product_code == 'A2345'
    assert version.mcu_component == 'A2345_mcu'
    assert version.esp32_component == 'A2345_esp32'
    assert version.unknown_fields == ()


def test_version_without_type_byte_and_with_v_prefix() -> None:
    frame = replace_field(decode_frame(VERSION_INFO), 0xA2, b'v2.1.1.6\x00')
    assert decode_version_info(frame).software_version == '2.1.1.6'


def test_rejects_invalid_utf8_in_version_field() -> None:
    frame = replace_field(decode_frame(VERSION_INFO), 0xA1, b'\x00\xff')
    with pytest.raises(MessageError) as exc_info:
        decode_version_info(frame)
    assert exc_info.value.kind == MessageErrorKind.INVALID_UTF8
    assert exc_info.value.tag == 0xA1


def test_decodes_synthetic_status_snapshot() -> None:
    preserved = Tlv(0xB3, bytes((70,)))
    snapshot = decode_status_snapshot(snapshot_frame(extra=[preserved]))
    assert len(snapshot.ports) == 6
    assert snapshot.ports[PortId.C1].millivolts == 1000
    assert snapshot.ports[PortId.A2].centiwatts == 1502
    assert snapshot.ports[PortId.A2].status == 0
    assert snapshot.unknown_fields == (preserved,)


def test_decodes_china_firmware_status_snapshot_ports() -> None:
    snapshot = decode_status_snapshot(snapshot_frame(tail=b'\x02\x01\x02'))
    c1 = snapshot.ports[PortId.C1]
    assert (c1.millivolts, c1.milliamps) == (1000, 1001)
    assert snapshot.ports[PortId.A2].centiwatts == 1502
    assert (c1.cable, c1.fast_charge, c1.device_brand) == (2, 1, 2)
    assert snapshot.unknown_fields == ()


def test_decodes_fourteen_byte_acknowledgement() -> None:
    frame = decode_frame(REALTIME_ACKNOWLEDGEMENT)
    assert frame.length == 14
    acknowledgement = decode_acknowledgement(frame)
    assert acknowledgement.message_type == 0x0A0B
    assert acknowledgement.increment == 0
    assert acknowledgement.marker == 0x34


def test_rejects_acknowledgement_of_wrong_type_or_length() -> None:
    with pytest.raises(MessageError) as exc_info:
        decode_acknowledgement(decode_frame(VERSION_INFO))
    assert exc_info.value.kind == MessageErrorKind.UNEXPECTED_MESSAGE_TYPE
    frame = decode_frame(REALTIME_ACKNOWLEDGEMENT)
    longer = Frame(version=3, speaker=1, group=15, message_type=0x0A0B,
                   increment=0, fields=[*frame.fields, Tlv(0xA2, b'\x01')],
                   length=17)
    with pytest.raises(MessageError) as exc_info:
        decode_acknowledgement(longer)
    assert exc_info.value.kind == MessageErrorKind.INVALID_ACK_LENGTH
