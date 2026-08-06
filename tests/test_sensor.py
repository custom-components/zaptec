"""Tests for sensor.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.sensor import (
    ZapSensorEntityDescription,
    ZaptecChargeSensor,
    ZaptecEnengySensor,
    ZaptecSensor,
    ZaptecSensorTranslate,
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


def test_sensor_update_from_zaptec_sets_value(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecSensor._update_from_zaptec reads the raw value for its key."""
    charger = make_charger({"total_charge_power": 1500.0})
    description = ZapSensorEntityDescription(key="total_charge_power", cls=ZaptecSensor)
    entity = ZaptecSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == 1500.0  # noqa: SLF001, PLR2004
    assert entity._attr_available is True  # noqa: SLF001


def test_sensor_translate_post_init_lower_cases_options(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSensorTranslate._post_init lower-cases entity_description.options."""
    charger = make_charger({"device_type": "PRO"})
    description = ZapSensorEntityDescription(
        key="device_type", options=["Pro", "GO"], cls=ZaptecSensorTranslate
    )
    entity = ZaptecSensorTranslate(coordinator, charger, description, DeviceInfo())

    assert entity.entity_description.options == ["pro", "go"]


def test_sensor_translate_update_from_zaptec_lower_cases_value(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSensorTranslate._update_from_zaptec lower-cases the retrieved value."""
    charger = make_charger({"device_type": "PRO"})
    description = ZapSensorEntityDescription(
        key="device_type", options=["pro"], cls=ZaptecSensorTranslate
    )
    entity = ZaptecSensorTranslate(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == "pro"  # noqa: SLF001
    assert entity._attr_available is True  # noqa: SLF001


def test_charge_sensor_maps_known_mode_to_icon(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecChargeSensor picks the icon matching a known charger_operation_mode."""
    charger = make_charger({"charger_operation_mode": "Connected_Charging"})
    description = ZapSensorEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSensor)
    entity = ZaptecChargeSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == "connected_charging"  # noqa: SLF001
    assert entity._attr_icon == "mdi:lightning-bolt"  # noqa: SLF001


def test_charge_sensor_falls_back_to_unknown_icon(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecChargeSensor falls back to the 'unknown' icon for an unmapped mode."""
    charger = make_charger({"charger_operation_mode": "Something_Weird"})
    description = ZapSensorEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSensor)
    entity = ZaptecChargeSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_icon == "mdi:help-rhombus-outline"  # noqa: SLF001


def test_energy_sensor_uses_meter_value_when_no_session(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecEnengySensor falls back to the meter reading when no session is present."""
    charger = make_charger({"signed_meter_value": {"RD": [{"RV": 12.5}]}})
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == 12.5  # noqa: SLF001, PLR2004


def test_energy_sensor_uses_session_value_when_larger(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecEnengySensor uses the session reading when it exceeds the meter reading."""
    charger = make_charger(
        {
            "signed_meter_value": {"RD": [{"RV": 10.0}]},
            "completed_session": {"SignedSession": {"RD": [{"RV": 20.0}]}},
        }
    )
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == 20.0  # noqa: SLF001, PLR2004


def test_energy_sensor_ignores_non_dict_session(
    coordinator: ZaptecUpdateCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """ZaptecEnengySensor logs and defaults the session reading to 0.0 when it isn't a dict."""
    charger = make_charger(
        {
            "signed_meter_value": {"RD": [{"RV": 10.0}]},
            "completed_session": "not-a-dict",
        }
    )
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    with caplog.at_level(logging.DEBUG):
        entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == 10.0  # noqa: SLF001, PLR2004
    assert "Incorrect typing for completed_session" in caplog.text
