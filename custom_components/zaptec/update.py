"""Zaptec component update."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant import const
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Charger
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecUpdate(ZaptecBaseEntity, UpdateEntity):
    """Base class for Zaptec update entities."""

    zaptec_obj: Charger

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        try:
            self._attr_installed_version = self._get_zaptec_value(
                key="firmware_current_version"
            )
            self._attr_latest_version = self._get_zaptec_value(
                key="firmware_available_version"
            )
            self._attr_available = True
            self._log_value(self._attr_installed_version)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()

    async def async_install(self, version, backup):
        """Install the update."""
        _LOGGER.debug(
            "Updating firmware %s of %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command("upgrade_firmware")
        except Exception as exc:
            raise HomeAssistantError("Sending update firmware command failed") from exc

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapUpdateEntityDescription(UpdateEntityDescription):
    """Class describing Zaptec update entities."""

    cls: type | None = None


INSTALLATION_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapUpdateEntityDescription(
        key="firmware_update",
        translation_key="firmware_update",
        device_class=UpdateDeviceClass.FIRMWARE,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        # icon="mdi:lock",  # FIXME: Find how icons work for firmware
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zaptec update entities."""
    _LOGGER.debug("Setup binary sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = ZaptecUpdate.create_from_zaptec(
        coordinator,
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
