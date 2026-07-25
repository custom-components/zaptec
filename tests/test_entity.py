"""Behavior tests for ZaptecBaseEntity, driven through the real harness."""

import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator
from custom_components.zaptec.entity import KeyUnavailableError, ZaptecBaseEntity
from custom_components.zaptec.zaptec import MISSING, ZaptecApiError
from tests.conftest import setup_integration


def _entity_from_coordinator(
    coordinator: ZaptecUpdateCoordinator, *, key_not_in_skip_list: bool = False
) -> ZaptecBaseEntity:
    """Return a real entity instance bound to `coordinator`.

    `_listeners` also holds the coordinator's own `set_update_interval` listener
    (registered in ZaptecUpdateCoordinator.__init__), so filter for a callback
    bound to an actual entity rather than assuming the first one qualifies.
    """
    for cb, _context in coordinator._listeners.values():  # noqa: SLF001
        candidate = cb.__self__
        if not hasattr(candidate, "_log_value"):
            continue
        if key_not_in_skip_list and candidate.key in KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK:
            continue
        return candidate
    raise AssertionError("no matching zaptec entity found")


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


async def test_entity_stays_available_when_single_key_missing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A single missing backing key does NOT mark the entity unavailable (#410 is not a bug).

    `ZaptecBaseEntity` extends `CoordinatorEntity`, whose `available` property is
    driven solely by `coordinator.last_update_success` and never reads
    `_attr_available`. When `_update_from_zaptec` raises `KeyUnavailableError`,
    `_handle_coordinator_update` catches it and the coordinator's poll still
    succeeds, so the entity stays available and simply retains its previous
    value/state.
    """
    await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)

    state_before = hass.states.get(entity_id)
    assert state_before.state not in ("unavailable", "unknown")

    # Make every key lookup miss, then re-run a refresh so entities re-read.
    mock_zaptec.chargers[0].get.side_effect = lambda _key, default=MISSING: default
    manager = mock_config_entry.runtime_data
    for coordinator in manager.all_coordinators:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    state_after = hass.states.get(entity_id)
    assert state_after.state != "unavailable"
    assert state_after.state == state_before.state


async def test_entity_unavailable_when_coordinator_poll_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The entity reports 'unavailable' when its coordinator's poll fails.

    This is the actual mechanism behind entity availability: `CoordinatorEntity.available`
    reflects `coordinator.last_update_success`, not any per-key state.
    """
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)

    mock_zaptec.poll.side_effect = ZaptecApiError("boom")
    coordinator = manager.device_coordinators["chg-mock-1"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

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
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator)
    entity.some_attr = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert "value1" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert caplog.text == ""


async def test_get_zaptec_value_returns_default_when_key_missing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """_get_zaptec_value() returns the caller's default when the key isn't backed."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator)

    sentinel = object()
    assert entity._get_zaptec_value(key="totally_missing_key", default=sentinel) is sentinel  # noqa: SLF001


async def test_get_zaptec_value_raises_when_intermediate_value_not_mapping(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A dotted key whose first segment resolves to a non-Mapping value raises."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator)

    # "operating_mode" is seeded as a plain string, which has no `.get()`.
    with pytest.raises(KeyUnavailableError):
        entity._get_zaptec_value(key="operating_mode.sub")  # noqa: SLF001


async def test_log_zaptec_attribute_formats_none_str_and_iterable_keys(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """_log_zaptec_attribute formats None, a single key, and an iterable of keys."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator)

    entity._log_zaptec_key = None  # noqa: SLF001
    assert entity._log_zaptec_attribute == ""  # noqa: SLF001

    entity._log_zaptec_key = "foo"  # noqa: SLF001
    assert entity._log_zaptec_attribute == ".foo"  # noqa: SLF001

    entity._log_zaptec_key = ["foo", "bar"]  # noqa: SLF001
    assert entity._log_zaptec_attribute == ".foo and .bar"  # noqa: SLF001


async def test_log_unavailable_logs_error_and_recovery_transitions(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    caplog: pytest.LogCaptureFixture,
    enable_custom_integrations: None,
) -> None:
    """_log_unavailable logs the real exception on going unavailable, and logs recovery."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator, key_not_in_skip_list=True)

    entity._prev_available = True  # noqa: SLF001
    entity._attr_available = False  # noqa: SLF001
    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(RuntimeError("boom"))  # noqa: SLF001
    assert f"Entity {entity.entity_id} is unavailable" in caplog.text
    assert "Getting value failed" in caplog.text

    caplog.clear()
    entity._prev_available = False  # noqa: SLF001
    entity._attr_available = True  # noqa: SLF001
    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable()  # noqa: SLF001
    assert f"Entity {entity.entity_id} is available" in caplog.text


async def test_entity_trigger_poll_delegates_to_coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """ZaptecBaseEntity.trigger_poll() awaits the bound coordinator's trigger_poll()."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = _entity_from_coordinator(coordinator)

    coordinator.trigger_poll = AsyncMock()
    await entity.trigger_poll()

    coordinator.trigger_poll.assert_awaited_once()
