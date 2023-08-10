"""Switch platform for Zaptec."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.switch import (SwitchDeviceClass, SwitchEntity,
                                             SwitchEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Account, Charger
from .const import CHARGE_MODE_MAP, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecSwitch(ZaptecBaseEntity, SwitchEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        self._attr_is_on = bool(self._get_zaptec_value())
        self._log_value(self._attr_is_on)


class ZaptecChargeSwitch(ZaptecSwitch):

    zaptec_obj: Charger

    @callback
    def _update_from_zaptec(self) -> None:
        state = self._get_zaptec_value()
        self._attr_is_on = state in ["Connected_Charging"]
        self._log_value(self._attr_is_on)

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        try:
            await self.zaptec_obj.command('resume_charging')
        except Exception as exc:
            raise HomeAssistantError(exc) from exc

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        try:
            await self.zaptec_obj.command('stop_pause')
        except Exception as exc:
            raise HomeAssistantError(exc) from exc


@dataclass
class ZapSwitchEntityDescription(SwitchEntityDescription):

    cls: type|None = None


INSTALLATION_SWITCH_TYPES: list[EntityDescription] = [
]

CIRCUIT_SWITCH_TYPES: list[EntityDescription] = [
]

CHARGER_SWITCH_TYPES: list[EntityDescription] = [
    ZapSwitchEntityDescription(
        key="operating_mode",
        translation_key="charging",
        device_class=SwitchDeviceClass.SWITCH,
        cls=ZaptecChargeSwitch,
    ),
    # FIXME: Implement a authentication required switch
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup switches")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    acc = coordinator.account

    switches = ZaptecSwitch.create_from_zaptec(
        acc,
        coordinator,
        INSTALLATION_SWITCH_TYPES,
        CIRCUIT_SWITCH_TYPES,
        CHARGER_SWITCH_TYPES,
    )
    async_add_entities(switches, True)
