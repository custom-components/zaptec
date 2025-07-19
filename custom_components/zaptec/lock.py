"""Xaptec lock."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant import const
from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import Charger

_LOGGER = logging.getLogger(__name__)


class ZaptecLock(ZaptecBaseEntity, LockEntity):
    """Base class for Zaptec locks."""

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        try:
            self._attr_is_locked = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_is_locked)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecCableLock(ZaptecLock):
    """Zaptec cable lock entity."""

    zaptec_obj: Charger

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the cable lock."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(False)
        except Exception as exc:
            raise HomeAssistantError("Removing permanent cable lock failed") from exc

        await self.trigger_poll()

    async def async_lock(self, **kwargs) -> None:
        """Lock the cable lock."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(True)
        except Exception as exc:
            raise HomeAssistantError("Setting permanent cable lock failed") from exc

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapLockEntityDescription(LockEntityDescription):
    """Class describing Zaptec lock entities."""

    cls: type[LockEntity]


INSTALLATION_ENTITIES: list[ZapLockEntityDescription] = []

CHARGER_ENTITIES: list[ZapLockEntityDescription] = [
    ZapLockEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        cls=ZaptecCableLock,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec locks."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
