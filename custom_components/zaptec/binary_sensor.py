"""Zaptec component binary sensors."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant import const
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecBinarySensor(ZaptecBaseEntity, BinarySensorEntity):
    """Base class for Zaptec binary sensors."""

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


class ZaptecBinarySensorWithAttrs(ZaptecBinarySensor):
    """Zaptec binary sensor with additional attributes."""

    def _post_init(self):
        self._attr_extra_state_attributes = self.zaptec_obj.asdict()
        self._attr_unique_id = self.zaptec_obj.id


@dataclass(frozen=True, kw_only=True)
class ZapBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Class describing Zaptec binary sensor entities."""

    cls: type | None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Installation",  # Special case, no translation
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud",
        has_entity_name=False,
        cls=ZaptecBinarySensorWithAttrs,
    ),
    ZapBinarySensorEntityDescription(
        # The Zaptec API is not consistent with the naming of the usage of
        # authorization and authentication. The Zaptec Portal seems to use
        # "authorisation" consistently.
        key="is_required_authentication",
        translation_key="authorization_required",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
    ),
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Charger",  # Special case, no translation
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud",
        has_entity_name=False,
        cls=ZaptecBinarySensorWithAttrs,
    ),
    ZapBinarySensorEntityDescription(
        key="is_online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:ev-station",
        cls=ZaptecBinarySensor,
    ),
    ZapBinarySensorEntityDescription(
        key="is_authorization_required",
        translation_key="authorization_required",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Zaptec binary sensors."""
    _LOGGER.debug("Setup binary sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = ZaptecBinarySensor.create_from_zaptec(
        coordinator,
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
