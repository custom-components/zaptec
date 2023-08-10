"""Zaptec component number entities."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.number import (NumberDeviceClass, NumberEntity,
                                             NumberEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Installation
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecNumber(ZaptecBaseEntity, NumberEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        self._attr_native_value = self._get_zaptec_value()
        self._log_value(self._attr_native_value)


class ZaptecAvailableCurrentNumber(ZaptecNumber):

    zaptec_obj: Installation

    def _post_init(self):
        # Get the max current rating from the reported max current
        self.entity_description.native_max_value = self.zaptec_obj.max_current

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        _LOGGER.debug(
            "Setting %s '%s' to '%s' in %s",
            self.__class__.__qualname__,
            self.key,
            value,
            self.zaptec_obj.id
        )
        try:
            await self.zaptec_obj.limit_current(availableCurrent=value)
        except Exception as exc:
            raise HomeAssistantError(exc) from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapNumberEntityDescription(NumberEntityDescription):

    cls: type|None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapNumberEntityDescription(
        key="available_current",
        translation_key="available_current",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=0,
        icon="mdi:waves",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecAvailableCurrentNumber,
    ),
    ZapNumberEntityDescription(
        key="available_current_phase1",
        translation_key="available_current_phase1",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=32,  # FIXME: Implememt max current per phase
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        # cls=ZaptecAvailableCurrentNumber,  # FIXME: Implement 3phase adjustment
    ),
    ZapNumberEntityDescription(
        key="available_current_phase2",
        translation_key="available_current_phase2",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=32,  # FIXME: Implememt max current per phase
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        # cls=ZaptecAvailableCurrentNumber,  # FIXME: Implement 3phase adjustment
    ),
    ZapNumberEntityDescription(
        key="available_current_phase3",
        translation_key="available_current_phase3",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=32,  # FIXME: Implememt max current per phase
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        # cls=ZaptecAvailableCurrentNumber,  # FIXME: Implement 3phase adjustment
    ),
]

CIRCUIT_ENTITIES: list[EntityDescription] = [
]

CHARGER_ENTITIES: list[EntityDescription] = [
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup numbers")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    acc = coordinator.account

    entities = ZaptecNumber.create_from_zaptec(
        acc,
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
