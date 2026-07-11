"""Tests for entity.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.entity import DeviceInfo, EntityDescription
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.entity import KeyUnavailableError, ZaptecBaseEntity
from custom_components.zaptec.zaptec import MISSING


class FakeZaptecObj:
    """Minimal stand-in for a ZaptecBase object, exposing only what ZaptecBaseEntity uses."""

    def __init__(self, obj_id: str, data: dict[str, Any]) -> None:
        """Initialize the FakeZaptecObj."""
        self.id = obj_id
        self._data = data

    @property
    def qual_id(self) -> str:
        """Return the qualified id."""
        return f"Fake[{self.id}]"

    def get(self, key: str, default: Any = MISSING) -> Any:
        """Get a value from the data dict."""
        return self._data.get(key, default)


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


@pytest.fixture
def zaptec_obj() -> FakeZaptecObj:
    """Create a FakeZaptecObj for testing."""
    return FakeZaptecObj(
        "dev1",
        {"operating_mode": "Connected", "nested": {"inner": "value"}},
    )


@pytest.fixture
def entity(coordinator: ZaptecUpdateCoordinator, zaptec_obj: FakeZaptecObj) -> ZaptecBaseEntity:
    """Create a ZaptecBaseEntity for testing."""
    description = EntityDescription(key="operating_mode")
    return ZaptecBaseEntity(coordinator, zaptec_obj, description, DeviceInfo())


def test_init_sets_unique_id_device_info_and_log_key(
    entity: ZaptecBaseEntity, zaptec_obj: FakeZaptecObj
) -> None:
    """Test that init sets _attr_unique_id, _attr_device_info, and _log_zaptec_key."""
    assert entity._attr_unique_id == "dev1_operating_mode"  # noqa: SLF001
    assert entity._attr_device_info == DeviceInfo()  # noqa: SLF001
    assert entity._log_zaptec_key == "operating_mode"  # noqa: SLF001


def test_key_property_returns_description_key(entity: ZaptecBaseEntity) -> None:
    """Test that key property returns the entity description key."""
    assert entity.key == "operating_mode"


def test_get_zaptec_value_returns_value(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value returns the value from zaptec_obj."""
    assert entity._get_zaptec_value() == "Connected"  # noqa: SLF001


def test_get_zaptec_value_lower_cases_string(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value lower cases strings when requested."""
    assert entity._get_zaptec_value(lower_case_str=True) == "connected"  # noqa: SLF001


def test_get_zaptec_value_follows_dotted_key(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value follows dotted keys."""
    assert entity._get_zaptec_value(key="nested.inner") == "value"  # noqa: SLF001


def test_get_zaptec_value_returns_default_without_raising(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value returns default when key is missing."""
    assert entity._get_zaptec_value(key="missing_key", default="fallback") == "fallback"  # noqa: SLF001


def test_get_zaptec_value_raises_when_key_missing(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value raises KeyUnavailableError for missing keys."""
    with pytest.raises(KeyUnavailableError) as exc_info:
        entity._get_zaptec_value(key="missing_key")  # noqa: SLF001
    assert exc_info.value.key == "missing_key"


def test_get_zaptec_value_raises_when_object_is_not_a_mapping(entity: ZaptecBaseEntity) -> None:
    """Test that _get_zaptec_value raises KeyUnavailableError when zaptec_obj is not a mapping."""

    class NotMapping:
        @property
        def qual_id(self) -> str:
            return "NotMapping[test]"

    entity.zaptec_obj = NotMapping()

    with pytest.raises(KeyUnavailableError):
        entity._get_zaptec_value(key="operating_mode")  # noqa: SLF001


def test_handle_coordinator_update_success_updates_value_and_writes_state(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that a successful update logs the new value and writes HA state."""
    entity.entity_id = "sensor.test"
    entity.async_write_ha_state = MagicMock()
    entity._log_attribute = "some_attr"  # noqa: SLF001
    entity.some_attr = "new_value"
    entity._update_from_zaptec = lambda: None  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._handle_coordinator_update()  # noqa: SLF001

    entity.async_write_ha_state.assert_called_once()
    assert "new_value" in caplog.text


def test_handle_coordinator_update_key_unavailable_sets_attr_available_false(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that a KeyUnavailableError during update marks the entity unavailable."""
    entity.entity_id = "sensor.test"
    entity.async_write_ha_state = MagicMock()

    def raise_unavailable() -> None:
        raise KeyUnavailableError("operating_mode", "boom")

    entity._update_from_zaptec = raise_unavailable  # noqa: SLF001

    with caplog.at_level(logging.INFO):
        entity._handle_coordinator_update()  # noqa: SLF001

    # NOTE: this sets _attr_available, but ZaptecBaseEntity does not override
    # the `available` property inherited from HA's CoordinatorEntity (which
    # returns coordinator.last_update_success instead), so this flag currently
    # has no effect on the entity's actual reported availability. This test
    # documents today's real behavior, not the intended one - see the "Known
    # finding" note at the top of this plan.
    assert entity._attr_available is False  # noqa: SLF001
    assert "sensor.test is unavailable" in caplog.text
    entity.async_write_ha_state.assert_called_once()


def test_log_zaptec_attribute_formats_string_key(entity: ZaptecBaseEntity) -> None:
    """Test that _log_zaptec_attribute formats a string key with a leading dot."""
    entity._log_zaptec_key = "operating_mode"  # noqa: SLF001
    assert entity._log_zaptec_attribute == ".operating_mode"  # noqa: SLF001


def test_log_zaptec_attribute_formats_none_key(entity: ZaptecBaseEntity) -> None:
    """Test that _log_zaptec_attribute returns an empty string when the key is None."""
    entity._log_zaptec_key = None  # noqa: SLF001
    assert entity._log_zaptec_attribute == ""  # noqa: SLF001


def test_log_zaptec_attribute_formats_iterable_key(entity: ZaptecBaseEntity) -> None:
    """Test that _log_zaptec_attribute joins iterable keys with 'and'."""
    entity._log_zaptec_key = ["mode", "state"]  # noqa: SLF001
    assert entity._log_zaptec_attribute == ".mode and .state"  # noqa: SLF001


def test_log_value_logs_on_change(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_value logs when the value has changed."""
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001

    assert "value1" in caplog.text
    assert entity._prev_value == "value1"  # noqa: SLF001


def test_log_value_skips_logging_when_unchanged(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_value skips logging when the value is unchanged."""
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"
    entity._prev_value = "value1"  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001

    assert caplog.text == ""


def test_log_value_force_logs_even_when_unchanged(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_value logs even when unchanged if force is True."""
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"
    entity._prev_value = "value1"  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr", force=True)  # noqa: SLF001

    assert "value1" in caplog.text


def test_log_value_noop_for_none_attribute(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_value is a no-op when the attribute is None."""
    with caplog.at_level(logging.DEBUG):
        entity._log_value(None)  # noqa: SLF001

    assert caplog.text == ""


def test_log_unavailable_logs_on_transition_to_unavailable(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_unavailable logs when the entity transitions to unavailable."""
    entity.entity_id = "sensor.test"
    entity._attr_available = False  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable()  # noqa: SLF001

    assert "Entity sensor.test is unavailable" in caplog.text


def test_log_unavailable_logs_error_for_unexpected_exception(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_unavailable logs an error for an unexpected exception."""
    entity.entity_id = "sensor.test"
    entity._attr_available = False  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=ValueError("boom"))  # noqa: SLF001

    assert "Getting value failed" in caplog.text


def test_log_unavailable_skips_error_for_key_unavailable_error(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_unavailable skips the error log for KeyUnavailableError."""
    entity.entity_id = "sensor.test"
    entity._attr_available = False  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=KeyUnavailableError("some_key", "boom"))  # noqa: SLF001

    assert "Getting value failed" not in caplog.text


def test_log_unavailable_skips_error_for_keys_in_skip_set(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_unavailable skips the error log for keys in the skip set."""
    entity.entity_id = "sensor.test"
    entity.entity_description = EntityDescription(key="three_to_one_phase_switch_current")
    entity._attr_available = False  # noqa: SLF001

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=ValueError("boom"))  # noqa: SLF001

    assert "Getting value failed" not in caplog.text


def test_log_unavailable_logs_on_recovery(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that _log_unavailable logs when the entity recovers to available."""
    entity.entity_id = "sensor.test"
    entity._prev_available = False  # noqa: SLF001
    entity._attr_available = True  # noqa: SLF001

    with caplog.at_level(logging.INFO):
        entity._log_unavailable()  # noqa: SLF001

    assert "Entity sensor.test is available" in caplog.text


async def test_trigger_poll_delegates_to_coordinator(
    entity: ZaptecBaseEntity, coordinator: ZaptecUpdateCoordinator
) -> None:
    """Test that trigger_poll delegates to coordinator.trigger_poll."""
    coordinator.trigger_poll = AsyncMock()

    await entity.trigger_poll()

    coordinator.trigger_poll.assert_awaited_once()
