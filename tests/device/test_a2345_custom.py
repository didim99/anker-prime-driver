"""A2345 custom charging mode and account-side settings."""

from collections.abc import AsyncIterator

import pytest

from ankerprime.device.a2345 import A2345Device
from ankerprime.model import (
    A2345, ChargingMode, ChargingProfile, CustomModeRules, CustomPortLimit,
    CustomProtocol, DeviceInfo, InvalidParameterError, OutputId, ProtocolError)
from ankerprime.protocol.a2345.settings import PortProtocol
from tests.device.account import FakeAccount
from tests.device.charger import FakeChannel, charger_responder
from tests.device.test_a2345_device import connected
from tests.device.timings import A2345_FAST
from tests.utils import wait_until


CHARGING_MODE = 0x0206
EXIT_CUSTOM_MODE = 0x0211
MAX_COMPATIBILITY = 0x0214
DEVICE_IDENTIFICATION = 0x0223


def rules() -> CustomModeRules:
    assert A2345.custom_mode is not None
    return A2345.custom_mode


@pytest.fixture
def account() -> FakeAccount:
    return FakeAccount()


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel(charger_responder())


@pytest.fixture
async def device(channel: FakeChannel, account: FakeAccount
                 ) -> AsyncIterator[A2345Device]:
    info = DeviceInfo(serial='SERIAL123')
    device = A2345Device(channel, info, account=account, timings=A2345_FAST)
    yield device
    await device.disconnect()


async def loaded(device: A2345Device) -> A2345Device:
    await connected(device)
    await wait_until(lambda: device.state.settings.custom_profiles
                     is not None)
    return device


async def test_connect_reads_the_account(device: A2345Device,
                                         account: FakeAccount) -> None:
    account.store(rules().template(1, 'Desk'))
    await loaded(device)
    settings = device.state.settings
    assert settings.custom_mode_enabled is True
    assert settings.charging_mode == ChargingMode.CUSTOM
    assert settings.active_profile == 1
    profile = settings.custom_profile(1)
    assert profile is not None and profile.name == 'Desk'


async def test_account_failure_does_not_stop_the_connection(
        device: A2345Device, account: FakeAccount) -> None:
    account.fail_with(ProtocolError('refused'))
    await connected(device)
    assert device.state.available
    assert device.state.settings.custom_profiles is None
    with pytest.raises(ProtocolError):
        await device.reload_custom_profiles()


async def test_apply_sends_limits_and_protocols(
        device: A2345Device, channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    profile = rules().template(3, 'Travel').with_port(
        CustomPortLimit(OutputId.C1, 60, {CustomProtocol.SCP,
                                          CustomProtocol.PD_12V}))
    await device.apply_custom_profile(profile)
    frame = channel.sent[0]
    assert frame.message_type == CHARGING_MODE
    a2, a3, a4 = (frame.field(tag) for tag in (0xA2, 0xA3, 0xA4))
    assert a2 is not None and a2.raw[-1] == 5
    assert a3 is not None and a3.raw[-7:] == bytes((3, 0, 60, 65, 45, 25, 15))
    assert a4 is not None
    flags = a4.raw[-12:]
    assert flags[0] == PortProtocol.SCP | PortProtocol.PD_12V
    assert flags[3] & PortProtocol.PPS_21V
    assert device.state.settings.active_profile == 3


async def test_apply_without_protocols(device: A2345Device,
                                       channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    profile = rules().template(2, 'Old').replace(protocols_enabled=False)
    await device.apply_custom_profile(profile)
    assert channel.sent[0].field(0xA4) is None


async def test_mainland_sends_huawei_as_scp(channel: FakeChannel) -> None:
    account = FakeAccount(mainland=True)
    device = A2345Device(channel, account=account, timings=A2345_FAST)
    try:
        await loaded(device)
        channel.clear()
        profile = rules().template(1, 'P').with_port(CustomPortLimit(
            OutputId.C1, 100, {CustomProtocol.HUAWEI, CustomProtocol.XIAOMI,
                               CustomProtocol.SCP}))
        await device.apply_custom_profile(profile)
        a4 = channel.sent[0].field(0xA4)
        assert a4 is not None
        assert a4.raw[-12] == PortProtocol.SCP | PortProtocol.XIAOMI
    finally:
        await device.disconnect()


async def test_invalid_profile_is_not_sent(device: A2345Device,
                                           channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    profile = rules().template(1, 'P').with_port(
        CustomPortLimit(OutputId.C2, 120))
    with pytest.raises(InvalidParameterError):
        await device.apply_custom_profile(profile)
    assert CHARGING_MODE not in channel.sent_types()


async def test_save_adds_then_updates(device: A2345Device,
                                      account: FakeAccount,
                                      channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    await device.save_custom_profile(rules().template(2, '  Desk '))
    name, added = account.calls[-1]
    assert name == 'add'
    assert isinstance(added, ChargingProfile) and added.name == 'Desk'
    stored = device.state.settings.custom_profile(2)
    assert stored is not None and stored.profile_id is not None
    # Profile 2 is not in use: nothing goes to the charger.
    assert CHARGING_MODE not in channel.sent_types()

    await device.save_custom_profile(rules().template(2, 'Desk 2'))
    name, updated = account.calls[-1]
    assert name == 'update'
    assert isinstance(updated, ChargingProfile)
    assert updated.profile_id == stored.profile_id
    assert len(device.state.settings.custom_profiles or ()) == 1


async def test_saving_the_active_profile_applies_it(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    account.store(rules().template(1, 'Desk'))
    await loaded(device)
    channel.clear()
    changed = rules().template(1, 'Desk').with_port(
        CustomPortLimit(OutputId.C1, 50))
    await device.save_custom_profile(changed)
    assert channel.sent_types()[0] == CHARGING_MODE


async def test_deleting_the_active_profile_exits_first(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    stored = account.store(rules().template(1, 'Desk'))
    await loaded(device)
    channel.clear()
    await device.delete_custom_profile(1)
    assert channel.sent_types()[0] == EXIT_CUSTOM_MODE
    assert account.calls[-1] == ('delete', stored.profile_id)
    assert device.state.settings.custom_profiles == ()
    with pytest.raises(InvalidParameterError):
        await device.delete_custom_profile(1)


async def test_switching_labs_off_leaves_custom_mode(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    await device.set_custom_mode_enabled(False)
    assert account.calls == [('custom_mode', False)]
    assert channel.sent_types()[0] == EXIT_CUSTOM_MODE
    assert device.state.settings.custom_mode_enabled is False


async def test_compatibility_turns_identification_off_first(
        device: A2345Device, account: FakeAccount,
        channel: FakeChannel) -> None:
    await loaded(device)
    channel.clear()
    await device.set_max_compatibility(True)
    assert account.calls == [('device_identification', False),
                             ('max_compatibility', True)]
    writes = [message for message in channel.sent_types()
              if message in (MAX_COMPATIBILITY, DEVICE_IDENTIFICATION)]
    assert writes == [DEVICE_IDENTIFICATION, MAX_COMPATIBILITY]
    settings = device.state.settings
    assert settings.max_compatibility is True
    assert settings.device_identification is False


async def test_identification_turns_compatibility_off_first(
        device: A2345Device, account: FakeAccount) -> None:
    await loaded(device)
    await device.set_max_compatibility(True)
    account.calls.clear()
    await device.set_device_identification(True)
    assert account.calls == [('max_compatibility', False),
                             ('device_identification', True)]


async def test_switching_off_touches_only_that_switch(
        device: A2345Device, account: FakeAccount) -> None:
    await loaded(device)
    await device.set_device_identification(False)
    assert account.calls == [('device_identification', False)]
