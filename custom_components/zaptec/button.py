"""Zaptec component binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant import const
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import Charger

_LOGGER = logging.getLogger(__name__)


class ZaptecButton(ZaptecBaseEntity, ButtonEntity):
    """Base class for Zaptec buttons."""

    zaptec_obj: Charger

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and self.zaptec_obj.is_command_valid(self.key)

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

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapButtonEntityDescription(ButtonEntityDescription):
    """Class describing Zaptec button entities."""

    cls: type[ButtonEntity]


INSTALLATION_ENTITIES: list[EntityDescription] = []

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapButtonEntityDescription(
        key="resume_charging",
        translation_key="resume_charging",
        icon="mdi:play-circle-outline",
        cls=ZaptecButton,
    ),
    ZapButtonEntityDescription(
        key="stop_charging_final",
        translation_key="stop_charging",
        icon="mdi:pause-circle-outline",
        cls=ZaptecButton,
    ),
    ZapButtonEntityDescription(
        key="authorize_charge",
        translation_key="authorize_charge",
        icon="mdi:lock-check-outline",
        cls=ZaptecButton,
    ),
    ZapButtonEntityDescription(
        key="deauthorize_and_stop",
        translation_key="deauthorize_and_stop",
        icon="mdi:lock-remove-outline",
        cls=ZaptecButton,
    ),
    ZapButtonEntityDescription(
        key="restart_charger",
        translation_key="restart_charger",
        entity_category=const.EntityCategory.CONFIG,
        icon="mdi:restart",
        cls=ZaptecButton,
    ),
    ZapButtonEntityDescription(
        key="upgrade_firmware",
        translation_key="upgrade_firmware",
        entity_category=const.EntityCategory.CONFIG,
        icon="mdi:memory",
        cls=ZaptecButton,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec buttons."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
