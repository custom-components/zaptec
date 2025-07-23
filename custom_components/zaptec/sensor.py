"""Zaptec component sensors."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant import const
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import ZCONST

_LOGGER = logging.getLogger(__name__)


class ZaptecSensor(ZaptecBaseEntity, SensorEntity):
    """Base class for Zaptec sensors."""

    # What to log on entity update
    _log_attribute = "_attr_native_value"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_native_value = self._get_zaptec_value()
        self._attr_available = True


class ZaptecChargeSensor(ZaptecSensor):
    """Zaptec charge sensor entity."""

    _log_attribute = "_attr_native_value"

    # See ZCONST.charger_operation_modes for possible values
    CHARGE_MODE_ICON_MAP = {
        "Unknown": "mdi:help-rhombus-outline",
        "Disconnected": "mdi:power-plug-off",
        "Connected_Requesting": "mdi:timer-sand",
        "Connected_Charging": "mdi:lightning-bolt",
        "Connected_Finished": "mdi:battery-charging-100",
    }

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_native_value = self._get_zaptec_value()
        self._attr_icon = self.CHARGE_MODE_ICON_MAP.get(
            self._attr_native_value, self.CHARGE_MODE_ICON_MAP["Unknown"])
        self._attr_available = True


@dataclass(frozen=True, kw_only=True)
class ZapSensorEntityDescription(SensorEntityDescription):
    """Provide a description of a Zaptec sensor."""

    cls: type[SensorEntity]


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapSensorEntityDescription(
        key="available_current_phase1",
        translation_key="available_current_phase1",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="available_current_phase2",
        translation_key="available_current_phase2",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="available_current_phase3",
        translation_key="available_current_phase3",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="max_current",
        translation_key="max_current",
        device_class=SensorDeviceClass.CURRENT,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="authentication_type",
        translation_key="authentication_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.installation_authentication_type_list,
        icon="mdi:key-change",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
    ZapSensorEntityDescription(
        key="installation_type",
        translation_key="installation_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.installation_types_list,
        icon="mdi:shape-outline",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
    ZapSensorEntityDescription(
        key="network_type",
        translation_key="network_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.network_types_list,
        icon="mdi:waves-arrow-up",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapSensorEntityDescription(
        key="charger_operation_mode",
        translation_key="charger_operation_mode",
        device_class=SensorDeviceClass.ENUM,
        options=ZCONST.charger_operation_modes_list,
        icon="mdi:ev-station",
        cls=ZaptecChargeSensor,
        # No state class as its not a numeric value
    ),
    ZapSensorEntityDescription(
        key="current_phase1",
        translation_key="current_phase1",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="current_phase2",
        translation_key="current_phase2",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="current_phase3",
        translation_key="current_phase3",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase1",
        translation_key="voltage_phase1",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase2",
        translation_key="voltage_phase2",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="voltage_phase3",
        translation_key="voltage_phase3",
        device_class=SensorDeviceClass.VOLTAGE,
        icon="mdi:sine-wave",
        native_unit_of_measurement=const.UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="total_charge_power",
        translation_key="total_charge_power",
        device_class=SensorDeviceClass.POWER,
        icon="mdi:flash",
        native_unit_of_measurement=const.UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="total_charge_power_session",
        translation_key="total_charge_power_session",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="signed_meter_value_kwh",
        translation_key="signed_meter_value",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="completed_session.Energy",
        translation_key="completed_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        icon="mdi:water-percent",
        native_unit_of_measurement=const.PERCENTAGE,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="temperature_internal5",
        translation_key="temperature_internal5",
        device_class=SensorDeviceClass.TEMPERATURE,
        icon="mdi:temperature-celsius",
        native_unit_of_measurement=const.UnitOfTemperature.CELSIUS,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="charge_current_set",
        translation_key="charge_current_set",
        device_class=SensorDeviceClass.CURRENT,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        cls=ZaptecSensor,
    ),
    ZapSensorEntityDescription(
        key="device_type",
        translation_key="device_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.device_types_list,
        icon="mdi:shape-outline",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec sensors."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
