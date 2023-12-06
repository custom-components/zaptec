"""Zaptec component sensors."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import ZCONST
from .const import DOMAIN

# pylint: disable=missing-function-docstring

_LOGGER = logging.getLogger(__name__)


class ZaptecSensor(ZaptecBaseEntity, SensorEntity):
    @callback
    def _update_from_zaptec(self) -> None:
        try:
            self._attr_native_value = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_native_value)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecChargeSensor(ZaptecSensor):
    # See ZCONST.charger_operation_modes for possible values
    CHARGE_MODE_MAP = {
        "Unknown": ["Unknown", "mdi:help-rhombus-outline"],
        "Disconnected": ["Disconnected", "mdi:power-plug-off"],
        "Connected_Requesting": ["Waiting", "mdi:timer-sand"],
        "Connected_Charging": ["Charging", "mdi:lightning-bolt"],
        "Connected_Finished": ["Charge done", "mdi:battery-charging-100"],
    }

    @callback
    def _update_from_zaptec(self) -> None:
        try:
            state = self._get_zaptec_value()
            mode = self.CHARGE_MODE_MAP.get(state, self.CHARGE_MODE_MAP["Unknown"])
            self._attr_native_value = mode[0]
            self._attr_icon = mode[1]
            self._attr_available = True
            self._log_value(self._attr_native_value)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


@dataclass
class ZapSensorEntityDescription(SensorEntityDescription):
    """Provide a description of a Zaptec sensor."""

    cls: type | None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapSensorEntityDescription(
        key="available_current_phase1",
        translation_key="available_current_phase1",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="available_current_phase2",
        translation_key="available_current_phase2",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="available_current_phase3",
        translation_key="available_current_phase3",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="max_current",
        translation_key="max_current",
        device_class=SensorDeviceClass.CURRENT,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="authentication_type",
        translation_key="authentication_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=[x for x in ZCONST.installation_authentication_type],
        icon="mdi:key-change",
    ),
]

CIRCUIT_ENTITIES: list[EntityDescription] = [
    ZapSensorEntityDescription(
        key="max_current",
        translation_key="max_current",
        device_class=SensorDeviceClass.CURRENT,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapSensorEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[x[0] for x in ZaptecChargeSensor.CHARGE_MODE_MAP.values()],
        icon="mdi:ev-station",
        cls=ZaptecChargeSensor,
    ),
    ZapSensorEntityDescription(
        key="current_phase1",
        translation_key="current_phase1",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="current_phase2",
        translation_key="current_phase2",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="current_phase3",
        translation_key="current_phase3",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase1",
        translation_key="voltage_phase1",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase2",
        translation_key="voltage_phase2",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase3",
        translation_key="voltage_phase3",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
    ),
    ZapSensorEntityDescription(
        key="total_charge_power",
        translation_key="total_charge_power",
        device_class=SensorDeviceClass.POWER,
        icon="mdi:flash",
        native_unit_of_measurement=const.UnitOfPower.WATT,
    ),
    ZapSensorEntityDescription(
        key="total_charge_power_session",
        translation_key="total_charge_power_session",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ZapSensorEntityDescription(
        key="signed_meter_value_kwh",
        translation_key="signed_meter_value",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ZapSensorEntityDescription(
        key="completed_session.Energy",
        translation_key="completed_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
    ),
    ZapSensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        icon="mdi:water-percent",
        native_unit_of_measurement=const.PERCENTAGE,
        entity_category=const.EntityCategory.DIAGNOSTIC,
    ),
    ZapSensorEntityDescription(
        key="temperature_internal5",
        translation_key="temperature_internal5",
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:temperature-celsius",
        native_unit_of_measurement=const.TEMP_CELSIUS,
        entity_category=const.EntityCategory.DIAGNOSTIC,
    ),
    ZapSensorEntityDescription(
        key="charge_current_set",
        translation_key="charge_current_set",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = ZaptecSensor.create_from_zaptec(
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
