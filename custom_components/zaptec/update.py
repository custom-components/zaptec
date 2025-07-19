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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import Charger

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

    async def async_install(self, version, backup, **kwargs):
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

    cls: type[UpdateEntity]


INSTALLATION_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapUpdateEntityDescription(
        key="firmware_update",
        translation_key="firmware_update",
        device_class=UpdateDeviceClass.FIRMWARE,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        # icon="mdi:lock",  # FIXME: Find how icons work for firmware
        cls=ZaptecUpdate,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec update entities."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
