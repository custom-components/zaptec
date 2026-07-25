"""Behavior tests for ZaptecBaseEntity, driven through the real harness."""

import logging
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.zaptec import MISSING
from tests.conftest import setup_integration


async def _get_zaptec_entity(hass: HomeAssistant) -> str:
    """Return one live zaptec entity_id whose value is backed by seeded data.

    Not every zaptec entity reads a key that `mock_zaptec` seeds (e.g. the
    3-to-1-phase-switch-current number entity has no backing value and stays
    "unknown"), and platform setup order is not guaranteed to surface a
    backed entity first. Skip past unbacked entities to find one that
    actually resolved a value, so the test exercises real value surfacing
    rather than an incidental "unknown" state.
    """
    for state in hass.states.async_all():
        if state.entity_id.startswith(
            ("sensor.", "binary_sensor.", "switch.", "number.")
        ) and state.state not in ("unavailable", "unknown"):
            return state.entity_id
    raise AssertionError("no backed zaptec entity found")


async def test_entity_reports_value_from_zaptec(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A backed key surfaces as the entity's state (not 'unavailable'/'unknown')."""
    await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)
    state = hass.states.get(entity_id)
    assert state.state not in ("unavailable", "unknown")


@pytest.mark.xfail(
    reason="#410: _attr_available is set on KeyUnavailableError but never affects "
    "reported availability (available is not overridden). Documenting current behavior.",
    strict=True,
)
async def test_entity_becomes_unavailable_when_key_missing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A key that disappears SHOULD mark the entity unavailable (currently it does not — #410)."""
    await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)

    # Make every key lookup miss, then re-run a refresh so entities re-read.
    mock_zaptec.chargers[0].get.side_effect = lambda _key, default=MISSING: default
    manager = mock_config_entry.runtime_data
    for coordinator in manager.all_coordinators:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    # This assertion is what SHOULD hold; strict xfail => the test failing here is expected
    # and will turn XPASS (alerting us) once #410 is fixed.
    assert hass.states.get(entity_id).state == "unavailable"


async def test_log_value_logs_on_change_then_skips_when_unchanged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    caplog: pytest.LogCaptureFixture,
    enable_custom_integrations: None,
) -> None:
    """_log_value logs when the tracked value changes and stays quiet when it doesn't."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    # Grab a real entity instance from the platform via the coordinator's listeners.
    # `_listeners` also holds the coordinator's own `set_update_interval` listener
    # (registered in ZaptecUpdateCoordinator.__init__), so filter for a callback
    # bound to an actual entity rather than assuming the first one qualifies.
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = next(
        cb.__self__
        for cb, _context in coordinator._listeners.values()  # noqa: SLF001
        if hasattr(cb.__self__, "_log_value")
    )
    entity.some_attr = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert "value1" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert caplog.text == ""
