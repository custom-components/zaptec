"""Tests for button.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.button import ZapButtonEntityDescription, ZaptecButton
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
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


def test_button_available_delegates_to_is_command_valid(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecButton.available checks is_command_valid using its own key as the command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = True
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())

    assert entity.available is True
    charger.is_command_valid.assert_called_once_with("restart_charger")


def test_button_unavailable_when_command_invalid(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecButton.available is False when is_command_valid returns False."""
    charger = make_charger({})
    charger.is_command_valid.return_value = False
    description = ZapButtonEntityDescription(key="resume_charging", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())

    assert entity.available is False


async def test_button_press_sends_command_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_press sends the command named by the button's key and triggers a poll."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_press()

    charger.command.assert_awaited_once_with("restart_charger")
    entity.trigger_poll.assert_awaited_once()


async def test_button_press_wraps_command_failure(coordinator: ZaptecUpdateCoordinator) -> None:
    """async_press wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_press()

    entity.trigger_poll.assert_not_called()
