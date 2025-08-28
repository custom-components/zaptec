"""Zaptec component sensors."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import ClassVar

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

from .entity import ZaptecBaseEntity
from .manager import ZaptecConfigEntry
from .zaptec import ZCONST, get_ocmf_max_reader_value

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


class ZaptecSensorTranslate(ZaptecSensor):
    """Sensor with strings intended for translations.

    This class should be used when the sensor value is a string that should be
    translated. HA requires all translations keys to be lower case, and this
    class converts the option strings to lower case, and converts any string
    value to lower case before setting the value in the entity.
    """

    entity_description: SensorEntityDescription

    # What to log on entity update
    _log_attribute = "_attr_native_value"

    def _post_init(self) -> None:
        """Post initialization."""
        # Convert any options strings into lower case for translations
        if (options := self.entity_description.options) is not None:
            self.entity_description = replace(
                self.entity_description,
                options=[s.lower() for s in options],
            )

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        # Convert any strings to lower case because they will be used for translations
        self._attr_native_value = self._get_zaptec_value(lower_case_str=True)
        self._attr_available = True


class ZaptecChargeSensor(ZaptecSensorTranslate):
    """Zaptec charge sensor entity."""

    _log_attribute = "_attr_native_value"

    # See ZCONST.charger_operation_modes for possible values
    CHARGE_MODE_ICON_MAP: ClassVar[dict[str, str]] = {
        "unknown": "mdi:help-rhombus-outline",
        "disconnected": "mdi:power-plug-off",
        "connected_requesting": "mdi:timer-sand",
        "connected_charging": "mdi:lightning-bolt",
        "connected_finished": "mdi:battery-charging-100",
    }

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_native_value: str = self._get_zaptec_value(lower_case_str=True)
        self._attr_icon = self.CHARGE_MODE_ICON_MAP.get(
            self._attr_native_value, self.CHARGE_MODE_ICON_MAP["unknown"]
        )
        self._attr_available = True


class ZaptecEnengySensor(ZaptecSensor):
    """Zaptec energy sensor entity."""

    _log_attribute = "_attr_native_value"
    # This entity use several attributes from Zaptec
    _log_zaptec_key = ["signed_meter_value", "completed_session"]

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()

        # The energy sensor value is found in two different attributes. They
        # are OCMF (Open Charge Metering Format) data structures and must be
        # parsed to get the latest reading.

        # Ge the two OCMF data structures from Zaptec. The first one must exists,
        # the second one is optional.
        meter_value = self._get_zaptec_value(key="signed_meter_value")
        session = self._get_zaptec_value(key="completed_session", default={})

        # Get the latest energy reading from both and use the largest value
        reading = get_ocmf_max_reader_value(meter_value)
        session_reading = get_ocmf_max_reader_value(session.get("SignedSession", {}))

        self._attr_native_value = max(reading, session_reading)
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
        cls=ZaptecSensorTranslate,
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
        cls=ZaptecSensorTranslate,
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
        # This key is no longer used to get Zaptec values, but is linked to the
        # entity unique_id, so it is kept for <0.7 compatibility
        key="signed_meter_value_kwh",
        translation_key="signed_meter_value",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:counter",
        native_unit_of_measurement=const.UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        cls=ZaptecEnengySensor,
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
    async_add_entities(entities, update_before_add=True)
