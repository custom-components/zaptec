"""Tests for number.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.number import (
    ZapNumberEntityDescription,
    ZaptecAvailableCurrentNumber,
    ZaptecHmiBrightness,
    ZaptecNumber,
    ZaptecSettingNumber,
    ZaptecThreeToOnePhaseSwitchCurrent,
)
from custom_components.zaptec.zaptec import Charger, Installation


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


def make_installation(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Installation) whose .get() reads from data."""
    installation = MagicMock(spec=Installation)
    installation.id = "install1"
    installation.qual_id = "Installation[install1]"
    installation.get.side_effect = data.get
    return installation


def test_number_update_from_zaptec_sets_value(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecNumber._update_from_zaptec reads the raw value for its key."""
    installation = make_installation({"available_current": 16.0})
    description = ZapNumberEntityDescription(key="available_current", cls=ZaptecNumber)
    entity = ZaptecNumber(coordinator, installation, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == 16.0  # noqa: SLF001, PLR2004
    assert entity._attr_available is True  # noqa: SLF001


def test_available_current_post_init_uses_reported_max_current(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAvailableCurrentNumber._post_init sets native_max_value from MaxCurrent."""
    installation = make_installation({"MaxCurrent": 20})
    description = ZapNumberEntityDescription(
        key="available_current", native_max_value=0, cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 20  # noqa: PLR2004


def test_available_current_post_init_defaults_to_32(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAvailableCurrentNumber._post_init defaults to 32A when MaxCurrent is absent."""
    installation = make_installation({})
    description = ZapNumberEntityDescription(
        key="available_current", native_max_value=0, cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 32  # noqa: PLR2004


async def test_available_current_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value sets the current limit and triggers a poll on success."""
    installation = make_installation({})
    installation.set_limit_current = AsyncMock()
    description = ZapNumberEntityDescription(
        key="available_current", cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(10.0)

    installation.set_limit_current.assert_awaited_once_with(availableCurrent=10.0)
    entity.trigger_poll.assert_awaited_once()


async def test_available_current_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    installation = make_installation({})
    installation.set_limit_current = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="available_current", cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(10.0)

    entity.trigger_poll.assert_not_called()


async def test_three_to_one_phase_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value sets the switch current and triggers a poll on success."""
    installation = make_installation({})
    installation.set_three_to_one_phase_switch_current = AsyncMock()
    description = ZapNumberEntityDescription(
        key="three_to_one_phase_switch_current", cls=ZaptecThreeToOnePhaseSwitchCurrent
    )
    entity = ZaptecThreeToOnePhaseSwitchCurrent(
        coordinator, installation, description, DeviceInfo()
    )
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(8.0)

    installation.set_three_to_one_phase_switch_current.assert_awaited_once_with(8.0)
    entity.trigger_poll.assert_awaited_once()


async def test_three_to_one_phase_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    installation = make_installation({})
    installation.set_three_to_one_phase_switch_current = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="three_to_one_phase_switch_current", cls=ZaptecThreeToOnePhaseSwitchCurrent
    )
    entity = ZaptecThreeToOnePhaseSwitchCurrent(
        coordinator, installation, description, DeviceInfo()
    )
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(8.0)

    entity.trigger_poll.assert_not_called()


def test_setting_number_post_init_uses_reported_max_limit(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSettingNumber._post_init sets native_max_value from ChargeCurrentInstallationMaxLimit."""
    charger = make_charger({"ChargeCurrentInstallationMaxLimit": 25})
    description = ZapNumberEntityDescription(
        key="charger_max_current",
        native_max_value=0,
        setting="maxChargeCurrent",
        cls=ZaptecSettingNumber,
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 25  # noqa: PLR2004


async def test_setting_number_missing_setting_raises_without_calling_api(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value raises HomeAssistantError when no setting is configured."""
    charger = make_charger({})
    charger.set_settings = AsyncMock()
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting=None, cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(16.0)

    charger.set_settings.assert_not_called()
    entity.trigger_poll.assert_not_called()


async def test_setting_number_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value writes the configured setting and triggers a poll on success."""
    charger = make_charger({})
    charger.set_settings = AsyncMock()
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting="maxChargeCurrent", cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(16.0)

    charger.set_settings.assert_awaited_once_with({"maxChargeCurrent": 16.0})
    entity.trigger_poll.assert_awaited_once()


async def test_setting_number_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_settings = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting="maxChargeCurrent", cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(16.0)

    entity.trigger_poll.assert_not_called()


def test_hmi_brightness_update_from_zaptec_scales_up(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecHmiBrightness._update_from_zaptec scales the 0-1 API value to a 0-100 percentage."""
    charger = make_charger({"hmi_brightness": 0.55})
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == pytest.approx(55.0)  # noqa: SLF001


async def test_hmi_brightness_set_native_value_scales_down(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value scales the 0-100 percentage back to 0-1 and triggers a poll."""
    charger = make_charger({})
    charger.set_hmi_brightness = AsyncMock()
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(50.0)

    charger.set_hmi_brightness.assert_awaited_once_with(0.5)
    entity.trigger_poll.assert_awaited_once()


async def test_hmi_brightness_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_hmi_brightness = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(50.0)

    entity.trigger_poll.assert_not_called()
