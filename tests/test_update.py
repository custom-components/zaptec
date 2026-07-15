"""Tests for update.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.update import ZaptecUpdate, ZapUpdateEntityDescription
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


def test_update_from_zaptec_sets_installed_and_latest_version(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecUpdate._update_from_zaptec reads both firmware version keys."""
    charger = make_charger(
        {
            "firmware_current_version": "1.0.0",
            "firmware_available_version": "1.1.0",
        }
    )
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_installed_version == "1.0.0"  # noqa: SLF001
    assert entity._attr_latest_version == "1.1.0"  # noqa: SLF001
    assert entity._attr_available is True  # noqa: SLF001


async def test_async_install_sends_upgrade_firmware_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_install sends the upgrade_firmware command and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_install(version=None, backup=False)

    charger.command.assert_awaited_once_with("upgrade_firmware")
    entity.trigger_poll.assert_awaited_once()


async def test_async_install_wraps_command_failure(coordinator: ZaptecUpdateCoordinator) -> None:
    """async_install wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_install(version=None, backup=False)

    entity.trigger_poll.assert_not_called()
