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
from homeassistant.const import EntityCategory

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Installation
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecNumber(ZaptecBaseEntity, NumberEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        try:
            self._attr_native_value = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_native_value)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecAvailableCurrentNumber(ZaptecNumber):

    zaptec_obj: Installation

    def _post_init(self):
        # Get the max current rating from the reported max current
        self.entity_description.native_max_value = self.zaptec_obj.get('max_current', 32)

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        _LOGGER.debug(
            "Setting %s.%s to <%s> %s   (in %s)",
            self.__class__.__qualname__, self.key,
            type(value).__qualname__, value,
            self.zaptec_obj.id
        )

        try:
            await self.zaptec_obj.set_limit_current(availableCurrent=value)
        except Exception as exc:
            raise HomeAssistantError(f"Set current limit to {value} failed") from exc

        await self.coordinator.async_request_refresh()


class ZaptecSettingNumber(ZaptecNumber):

    zaptec_obj: Installation

    def _post_init(self):
        # Get the max current rating from the reported max current
        self.entity_description.native_max_value = self.zaptec_obj.get('charge_current_installation_max_limit', 32)

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        _LOGGER.debug(
            "Setting %s.%s to <%s> %s   (in %s)",
            self.__class__.__qualname__, self.key,
            type(value).__qualname__, value,
            self.zaptec_obj.id
        )

        try:
            await self.zaptec_obj.set_settings({
                self.entity_description.setting: value
            })
        except Exception as exc:
            raise HomeAssistantError(f"Setting {self.entity_description.setting} to {value} failed") from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapNumberEntityDescription(NumberEntityDescription):

    cls: type|None = None
    setting: str|None = None


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
]

CIRCUIT_ENTITIES: list[EntityDescription] = [
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapNumberEntityDescription(
        key="charger_min_current",
        translation_key="charger_min_current",
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=32,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecSettingNumber,
        setting="CurrentInMinimum",
    ),
    ZapNumberEntityDescription(
        key="charger_max_current",
        translation_key="charger_max_current",
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=32,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecSettingNumber,
        setting="CurrentInMaximum",
    ),
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
