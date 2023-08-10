"""Zaptec component binary sensors."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant import const
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecUpdateCoordinator
from .api import Account
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaptecBinarySensor(ZaptecBaseEntity, BinarySensorEntity):

    @callback
    def _update_from_zaptec(self) -> None:
        self._attr_is_on = bool(self._get_zaptec_value())
        self._log_value(self._attr_is_on)


class ZaptecBinarySensorWithAttrs(ZaptecBinarySensor):

    def _post_init(self):
        self._attr_extra_state_attributes = self.zaptec_obj._attrs
        self._attr_unique_id = self.zaptec_obj.id


class ZaptecBinarySensorLock(ZaptecBinarySensor):

    @callback
    def _update_from_zaptec(self) -> None:
        self._attr_is_on = not bool(self._get_zaptec_value())
        self._log_value(self._attr_is_on)


@dataclass
class ZapBinarySensorEntityDescription(BinarySensorEntityDescription):

    cls: type|None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Installation",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:home-lightning-bolt-outline",
        has_entity_name=False,
        cls=ZaptecBinarySensorWithAttrs,
    ),
]

CIRCUIT_ENTITIES: list[EntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="is_active",
        name="Circuit",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:orbit",
        has_entity_name=False,
        cls=ZaptecBinarySensorWithAttrs,
    ),
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Charger",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:ev-station",
        has_entity_name=False,
        cls=ZaptecBinarySensorWithAttrs,
    ),
    ZapBinarySensorEntityDescription(
        key="is_authorization_required",
        translation_key="is_authorization_required",
        device_class=BinarySensorDeviceClass.LOCK,  # False=unlocked, True=locked
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
        cls=ZaptecBinarySensorLock,
    ),
    ZapBinarySensorEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        device_class=BinarySensorDeviceClass.LOCK,  # False=unlocked, True=locked
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
        cls=ZaptecBinarySensorLock,
    )
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    _LOGGER.debug("Setup binary sensors")

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    acc = coordinator.account

    entities = ZaptecBinarySensor.create_from_zaptec(
        acc,
        coordinator,
        INSTALLATION_ENTITIES,
        CIRCUIT_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
