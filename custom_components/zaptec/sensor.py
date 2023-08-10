"""Zaptec component sensors."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity,
                                             SensorEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Account
from .const import *

# pylint: disable=missing-function-docstring

_LOGGER = logging.getLogger(__name__)


class ZaptecSensor(ZaptecBaseEntity, SensorEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        self._attr_native_value = self._get_zaptec_value()
        self._log_value(self._attr_native_value)


class ZaptecChargeSensor(ZaptecSensor):

    @callback
    def _update_from_zaptec(self) -> None:
        state = self._get_zaptec_value()
        mode = CHARGE_MODE_MAP.get(state, CHARGE_MODE_MAP["Unknown"])
        self._attr_native_value = mode[0]
        self._attr_icon = mode[1]
        self._log_value(self._attr_native_value)


@dataclass
class ZapSensorEntityDescription(SensorEntityDescription):
    """Provide a description of a Zaptec sensor."""

    cls: type|None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
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
        options=[x[0] for x in CHARGE_MODE_MAP.values()],
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
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    acc: Account = coordinator.account

    entities = ZaptecSensor.create_from_zaptec(
        acc,
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
