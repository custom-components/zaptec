"""Tests for entity.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

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
