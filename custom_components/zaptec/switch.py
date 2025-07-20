"""Switch platform for Zaptec."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import Charger

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

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        state = self._get_zaptec_value()
        self._attr_is_on = state in ["Connected_Charging"]
        self._attr_available = True

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
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

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
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


@dataclass(frozen=True, kw_only=True)
class ZapSwitchEntityDescription(SwitchEntityDescription):
    """Class describing Zaptec switch entities."""

    cls: type[SwitchEntity]


INSTALLATION_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapSwitchEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        device_class=SwitchDeviceClass.SWITCH,
        cls=ZaptecChargeSwitch,
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
    async_add_entities(entities, True)
