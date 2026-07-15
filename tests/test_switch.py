"""Tests for switch.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.switch import (
    ZapSwitchEntityDescription,
    ZaptecCableLockSwitch,
    ZaptecChargeSwitch,
    ZaptecSwitch,
)
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_switch_update_from_zaptec_sets_is_on(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecSwitch._update_from_zaptec reads the raw boolean value for its key."""
    charger = make_charger({"permanent_cable_lock": True})
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecSwitch)
    entity = ZaptecSwitch(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_is_on is True  # noqa: SLF001
    assert entity._attr_available is True  # noqa: SLF001


def test_charge_switch_update_from_zaptec_true_only_when_charging(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecChargeSwitch is only "on" when the mode is exactly Connected_Charging."""
    charger = make_charger({"charger_operation_mode": "Connected_Charging"})
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_is_on is True  # noqa: SLF001


def test_charge_switch_available_checks_stop_command_when_on(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """When on, ZaptecChargeSwitch.available checks the stop_charging_final command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = True
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity._attr_is_on = True  # noqa: SLF001

    assert entity.available is True
    charger.is_command_valid.assert_called_once_with("stop_charging_final")


def test_charge_switch_available_checks_resume_command_when_off(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """When off, ZaptecChargeSwitch.available checks the resume_charging command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = False
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity._attr_is_on = False  # noqa: SLF001

    assert entity.available is False
    charger.is_command_valid.assert_called_once_with("resume_charging")


async def test_charge_switch_turn_on_resumes_charging_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on sends resume_charging and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_on()

    charger.command.assert_awaited_once_with("resume_charging")
    entity.trigger_poll.assert_awaited_once()


async def test_charge_switch_turn_on_wraps_command_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    entity.trigger_poll.assert_not_called()


async def test_charge_switch_turn_off_stops_charging_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off sends stop_charging_final and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_off()

    charger.command.assert_awaited_once_with("stop_charging_final")
    entity.trigger_poll.assert_awaited_once()


async def test_charge_switch_turn_off_wraps_command_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()

    entity.trigger_poll.assert_not_called()


async def test_cable_lock_switch_turn_on_locks_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on locks the cable and triggers a poll on success."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock()
    description = ZapSwitchEntityDescription(
        key="permanent_cable_lock", cls=ZaptecCableLockSwitch
    )
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_on()

    charger.set_permanent_cable_lock.assert_awaited_once_with(True)  # noqa: FBT003
    entity.trigger_poll.assert_awaited_once()


async def test_cable_lock_switch_turn_on_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(
        key="permanent_cable_lock", cls=ZaptecCableLockSwitch
    )
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    entity.trigger_poll.assert_not_called()


async def test_cable_lock_switch_turn_off_unlocks_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off unlocks the cable and triggers a poll on success."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock()
    description = ZapSwitchEntityDescription(
        key="permanent_cable_lock", cls=ZaptecCableLockSwitch
    )
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_off()

    charger.set_permanent_cable_lock.assert_awaited_once_with(False)  # noqa: FBT003
    entity.trigger_poll.assert_awaited_once()


async def test_cable_lock_switch_turn_off_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(
        key="permanent_cable_lock", cls=ZaptecCableLockSwitch
    )
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()

    entity.trigger_poll.assert_not_called()
