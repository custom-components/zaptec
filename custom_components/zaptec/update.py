"""Zaptec component update."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.update import (UpdateDeviceClass, UpdateEntity,
                                             UpdateEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Account, Charger
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecUpdate(ZaptecBaseEntity, UpdateEntity):

    zaptec_obj: Charger

    @callback
    def _update_from_zaptec(self) -> None:
        try:
            self._attr_installed_version = self._get_zaptec_value(key="current_firmware_version")
            self._attr_latest_version = self._get_zaptec_value(key="available_firmware_version")
            self._attr_available = True
            self._log_value(self._attr_installed_version)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()

    async def async_install(self, version, backup):
        _LOGGER.debug(
            "Updating firmware %s.%s  (in %s)",
            self.__class__.__qualname__, self.key,
            self.zaptec_obj.id
        )

        try:
            await self.zaptec_obj.upgrade_firmware()
        except Exception as exc:
            raise HomeAssistantError("Sending update firmware command failed") from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapUpdateEntityDescription(UpdateEntityDescription):

    cls: type|None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
]

CIRCUIT_ENTITIES: list[EntityDescription] = [
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapUpdateEntityDescription(
        key="firmware_update",
        translation_key="firmware_update_to_date",
        device_class=UpdateDeviceClass.FIRMWARE,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        # icon="mdi:lock",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup binary sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    acc = coordinator.account

    entities = ZaptecUpdate.create_from_zaptec(
        acc,
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
