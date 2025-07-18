"""Switch platform for Zaptec."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
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


class ZaptecSwitch(ZaptecBaseEntity, SwitchEntity):
    """Base class for Zaptec switches."""

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        try:
            self._attr_is_on = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_is_on)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecChargeSwitch(ZaptecSwitch):
    """Zaptec charge switch entity."""

    zaptec_obj: Charger

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        try:
            state = self._get_zaptec_value()
            self._attr_is_on = state in ["Connected_Charging"]
            self._attr_available = True
            self._log_value(self._attr_is_on)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()

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

    cls: type | None = None


INSTALLATION_SWITCH_TYPES: list[EntityDescription] = []

CHARGER_SWITCH_TYPES: list[EntityDescription] = [
    ZapSwitchEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        device_class=SwitchDeviceClass.SWITCH,
        cls=ZaptecChargeSwitch,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zaptec switches."""
    _LOGGER.debug("Setup switches")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = ZaptecSwitch.create_from_zaptec(
        coordinator,
        INSTALLATION_SWITCH_TYPES,
        CHARGER_SWITCH_TYPES,
    )
    async_add_entities(entities, True)
