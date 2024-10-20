"""Xaptec lock."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .const import DOMAIN
from .api import Charger

_LOGGER = logging.getLogger(__name__)


class ZaptecLock(ZaptecBaseEntity, LockEntity):
    @callback
    def _update_from_zaptec(self) -> None:
        try:
            self._attr_is_locked = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_is_locked)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecCableLock(ZaptecLock):
    zaptec_obj: Charger

    async def async_unlock(self, **kwargs) -> None:
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(False)
        except Exception as exc:
            raise HomeAssistantError("Removing permanent cable lock failed") from exc

        await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs) -> None:
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(True)
        except Exception as exc:
            raise HomeAssistantError("Setting permanent cable lock failed") from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapLockEntityDescription(LockEntityDescription):
    cls: type | None = None


INSTALLATION_ENTITIES: list[ZapLockEntityDescription] = [
]

CIRCUIT_ENTITIES: list[ZapLockEntityDescription] = [
]

CHARGER_ENTITIES: list[ZapLockEntityDescription] = [
    ZapLockEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        cls=ZaptecCableLock,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup lock entry")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entitites = ZaptecLock.create_from_zaptec(
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entitites, True)
