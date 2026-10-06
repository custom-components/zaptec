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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .authorization import is_authorized
from .entity import ZaptecBaseEntity
from .manager import ZaptecConfigEntry, ZaptecEntityDescription

_LOGGER = logging.getLogger(__name__)


class ZaptecBinarySensor(ZaptecBaseEntity, BinarySensorEntity):
    """Base class for Zaptec binary sensors."""

    # What to log on entity update
    _log_attribute = "_attr_is_on"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_is_on = self._get_zaptec_value()
        self._attr_available = True


class ZaptecActiveBinarySensor(ZaptecBinarySensor):
    """Zaptec 'active' binary sensor for the main charger/installation device.

    Uses the bare Zaptec object id as the unique id (without the entity key
    suffix) to preserve the historical entity identity.
    """

    def _post_init(self) -> None:
        self._attr_unique_id = self.zaptec_obj.id


class ZaptecAuthorizedBinarySensor(ZaptecBinarySensor):
    """Binary sensor for whether the current session is authorized."""

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        if self._get_zaptec_value(key="authentication_required", default=None) is False:
            # Nothing ever authorizes, so "Not authorized" would mislead.
            self._attr_is_on = None
        else:
            # A charger that has never had a session lacks the key entirely.
            self._attr_is_on = is_authorized(self._get_zaptec_value(default=None))
        self._attr_available = True


@dataclass(frozen=True, kw_only=True)
class ZapBinarySensorEntityDescription(ZaptecEntityDescription, BinarySensorEntityDescription):
    """Class describing Zaptec binary sensor entities."""


INSTALLATION_ENTITIES: list[ZaptecEntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Installation",  # Special case, no translation
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud",
        has_entity_name=False,
        cls=ZaptecActiveBinarySensor,
    ),
    ZapBinarySensorEntityDescription(
        # The Zaptec API is not consistent with the naming of the usage of
        # authorization and authentication. The Zaptec Portal seems to use
        # "authorisation" consistently.
        key="is_required_authentication",
        translation_key="authorization_required",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
        cls=ZaptecBinarySensor,
    ),
]

CHARGER_ENTITIES: list[ZaptecEntityDescription] = [
    ZapBinarySensorEntityDescription(
        key="active",
        name="Charger",  # Special case, no translation
        device_class=BinarySensorDeviceClass.CONNECTIVITY,  # False=disconnected, True=connected
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud",
        has_entity_name=False,
        cls=ZaptecActiveBinarySensor,
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
        key="authentication_required",
        translation_key="authorization_required",
        entity_category=const.EntityCategory.DIAGNOSTIC,
        icon="mdi:lock",
        cls=ZaptecBinarySensor,
    ),
    ZapBinarySensorEntityDescription(
        # Not diagnostic: with delayed charging the mode stays
        # Connected_Requesting whether or not it is authorized.
        key="charger_current_user_uuid",
        translation_key="authorized",
        icon="mdi:lock-open-check",
        cls=ZaptecAuthorizedBinarySensor,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec binary sensors."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, update_before_add=True)
