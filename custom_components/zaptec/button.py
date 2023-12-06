"""Zaptec component binary sensors."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
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


class ZaptecButton(ZaptecBaseEntity, ButtonEntity):
    zaptec_obj: Charger

    async def async_press(self) -> None:
        """Press the button."""
        _LOGGER.debug(
            "Press button of %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command(self.key)
        except Exception as exc:
            raise HomeAssistantError(f"Running command '{self.key}' failed") from exc

        await self.coordinator.async_request_refresh()


@dataclass
class ZapButtonEntityDescription(ButtonEntityDescription):
    cls: type | None = None


INSTALLATION_ENTITIES: list[EntityDescription] = []

CIRCUIT_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapButtonEntityDescription(
        key="resume_charging",
        translation_key="resume_charging",
        icon="mdi:play-circle-outline",
    ),
    ZapButtonEntityDescription(
        key="stop_charging_final",
        translation_key="stop_charging",
        icon="mdi:pause-circle-outline",
    ),
    ZapButtonEntityDescription(
        key="authorize_charge",
        translation_key="authorize_charge",
        icon="mdi:lock-check-outline",
    ),
    ZapButtonEntityDescription(
        key="deauthorize_and_stop",
        translation_key="deauthorize_and_stop",
        icon="mdi:lock-remove-outline",
    ),
    ZapButtonEntityDescription(
        key="restart_charger",
        translation_key="restart_charger",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:restart",
    ),
    ZapButtonEntityDescription(
        key="upgrade_firmware",
        translation_key="upgrade_firmware",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:memory",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup buttons")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = ZaptecButton.create_from_zaptec(
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
