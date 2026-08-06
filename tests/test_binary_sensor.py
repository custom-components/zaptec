"""Tests for binary_sensor.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.binary_sensor import (
    ZapBinarySensorEntityDescription,
    ZaptecBinarySensor,
    ZaptecBinarySensorWithAttrs,
)
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


def test_binary_sensor_update_from_zaptec_sets_is_on(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecBinarySensor._update_from_zaptec reads the raw boolean value for its key."""
    charger = make_charger({"is_online": True})
    description = ZapBinarySensorEntityDescription(key="is_online", cls=ZaptecBinarySensor)
    entity = ZaptecBinarySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_is_on is True  # noqa: SLF001
    assert entity._attr_available is True  # noqa: SLF001


def test_binary_sensor_with_attrs_post_init_sets_attrs_and_unique_id(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecBinarySensorWithAttrs._post_init copies all raw attrs and overrides unique_id."""
    charger = make_charger({})
    charger.asdict.return_value = {"Id": "charger1", "Active": True}
    description = ZapBinarySensorEntityDescription(key="active", cls=ZaptecBinarySensorWithAttrs)
    entity = ZaptecBinarySensorWithAttrs(coordinator, charger, description, DeviceInfo())

    assert entity._attr_extra_state_attributes == {"Id": "charger1", "Active": True}  # noqa: SLF001
    assert entity._attr_unique_id == "charger1"  # noqa: SLF001
