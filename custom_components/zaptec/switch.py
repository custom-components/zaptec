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
from .api import Account, Charger, Installation
from .const import CHARGE_MODE_MAP, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecSwitch(ZaptecBaseEntity, SwitchEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        try:
            self._attr_is_on = self._get_zaptec_value()
            self._attr_available = True
            self._log_value(self._attr_is_on)
        except (KeyError, AttributeError):
            self._attr_available = False
            self._log_unavailable()


class ZaptecChargeSwitch(ZaptecSwitch):

    zaptec_obj: Charger

    @callback
    def _update_from_zaptec(self) -> None:
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
            "Turn on %s.%s   (in %s)",
            self.__class__.__qualname__, self.key,
            self.zaptec_obj.id,
        )

        try:
            await self.zaptec_obj.resume_charging()
        except Exception as exc:
            raise HomeAssistantError("Resuming charging failed") from exc

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        _LOGGER.debug(
            "Turn off %s.%s   (in %s)",
            self.__class__.__qualname__, self.key,
            self.zaptec_obj.id,
        )

        try:
            await self.zaptec_obj.stop_charging_final()
        except Exception as exc:
            raise HomeAssistantError("Stop/pausing charging failed") from exc

        await self.coordinator.async_request_refresh()


class ZaptecAuthorizationRequiredSwitch(ZaptecSwitch):

    zaptec_obj: Installation

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        _LOGGER.debug(
            "Turn on %s.%s   (in %s)",
            self.__class__.__qualname__, self.key,
            self.zaptec_obj.id,
        )

        try:
            await self.zaptec_obj.set_authenication_required(True)
        except Exception as exc:
            raise HomeAssistantError("Setting authorization required failed") from exc

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        _LOGGER.debug(
            "Turn off %s.%s   (in %s)",
            self.__class__.__qualname__, self.key,
            self.zaptec_obj.id,
        )

        try:
            await self.zaptec_obj.set_authenication_required(False)
        except Exception as exc:
            raise HomeAssistantError("Setting authorization required failed") from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapSwitchEntityDescription(SwitchEntityDescription):

    cls: type|None = None


INSTALLATION_SWITCH_TYPES: list[EntityDescription] = [
    ZapSwitchEntityDescription(
        key="is_required_authentication",
        translation_key="authorization_required",
        device_class=SwitchDeviceClass.SWITCH,
        icon="mdi:lock-check-outline",
        cls=ZaptecAuthorizationRequiredSwitch,
    ),
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
