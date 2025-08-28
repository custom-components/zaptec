"""Switch platform for Zaptec."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ZaptecBaseEntity
from .manager import ZaptecConfigEntry
from .zaptec import Charger

_LOGGER = logging.getLogger(__name__)


class ZaptecSwitch(ZaptecBaseEntity, SwitchEntity):
    """Base class for Zaptec switches."""

    # What to log on entity update
    _log_attribute = "_attr_is_on"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_is_on = self._get_zaptec_value()
        self._attr_available = True


class ZaptecChargeSwitch(ZaptecSwitch):
    """Zaptec charge switch entity."""

    zaptec_obj: Charger
    _log_attribute = "_attr_is_on"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        command = "stop_charging_final" if self._attr_is_on else "resume_charging"
        return super().available and self.zaptec_obj.is_command_valid(command)

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        state = self._get_zaptec_value()
        self._attr_is_on = state in ["Connected_Charging"]
        self._attr_available = True

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn on the switch."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command("resume_charging")
        except Exception as exc:
            raise HomeAssistantError("Resuming charging failed") from exc

        await self.trigger_poll()

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn off the switch."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command("stop_charging_final")
        except Exception as exc:
            raise HomeAssistantError("Stop/pausing charging failed") from exc

        await self.trigger_poll()


class ZaptecCableLockSwitch(ZaptecSwitch):
    """Zaptec cable lock entity."""

    zaptec_obj: Charger

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the cable lock."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(False)
        except Exception as exc:
            raise HomeAssistantError("Removing permanent cable lock failed") from exc

        await self.trigger_poll()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the cable lock."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(True)
        except Exception as exc:
            raise HomeAssistantError("Setting permanent cable lock failed") from exc

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapSwitchEntityDescription(SwitchEntityDescription):
    """Class describing Zaptec switch entities."""

    cls: type[SwitchEntity]


INSTALLATION_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapSwitchEntityDescription(
        key="charger_operation_mode",
        translation_key="charger_operation_mode",
        device_class=SwitchDeviceClass.SWITCH,
        cls=ZaptecChargeSwitch,
    ),
    ZapSwitchEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        entity_category=EntityCategory.CONFIG,
        cls=ZaptecCableLockSwitch,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec switches."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, update_before_add=True)
