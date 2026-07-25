"""Behavior tests for ZaptecUpdateCoordinator, driven through the real harness."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.zaptec import ZaptecApiError
from tests.conftest import setup_integration


async def test_successful_poll_marks_last_update_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A successful poll leaves every coordinator reporting success."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    for coordinator in manager.all_coordinators:
        assert coordinator.last_update_success is True
    mock_zaptec.poll.assert_awaited()


async def test_poll_failure_sets_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A ZaptecApiError during poll flips last_update_success to False."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    head = manager.head_coordinator

    mock_zaptec.poll.side_effect = ZaptecApiError("boom")
    await head.async_refresh()

    assert head.last_update_success is False


async def test_device_coordinator_switches_interval_when_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A charger's coordinator uses the shorter interval once it reports charging."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    charger_coord = manager.device_coordinators["chg-mock-1"]
    idle_interval = charger_coord.update_interval

    # Flip the seeded charger to 'charging' and re-run the update-listener path.
    mock_zaptec.chargers[0].is_charging.return_value = True
    charger_coord.set_update_interval()

    assert charger_coord.update_interval < idle_interval
