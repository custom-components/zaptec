"""Zaptec component number entities."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging

from homeassistant import const
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZaptecBaseEntity, ZaptecConfigEntry
from .api import Charger, Installation

_LOGGER = logging.getLogger(__name__)


class ZaptecNumber(ZaptecBaseEntity, NumberEntity):
    """Base class for Zaptec number entities."""

    # What to log on entity update
    _log_attribute = "_attr_native_value"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_native_value = self._get_zaptec_value()
        self._attr_available = True

    def _log_number(self, value: float) -> None:
        """Log the number value change."""
        _LOGGER.debug(
            "Setting %s.%s to <%s> %s   (in %s)",
            self.__class__.__qualname__,
            self.key,
            type(value).__qualname__,
            value,
            self.zaptec_obj.qual_id,
        )


class ZaptecAvailableCurrentNumber(ZaptecNumber):
    """Zaptec available current number entity."""

    zaptec_obj: Installation
    entity_description: ZapNumberEntityDescription

    def _post_init(self):
        # Get the max current rating from the reported max current
        self.entity_description = replace(
            self.entity_description,
            native_max_value=self.zaptec_obj.get("MaxCurrent", 32),
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        self._log_number(value)
        try:
            await self.zaptec_obj.set_limit_current(availableCurrent=value)
        except Exception as exc:
            raise HomeAssistantError(f"Set current limit to {value} failed") from exc

        await self.trigger_poll()


class ZaptecThreeToOnePhaseSwitchCurrent(ZaptecNumber):
    """Zaptec three to one phase switch current number entity."""

    zaptec_obj: Installation
    entity_description: ZapNumberEntityDescription

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        self._log_number(value)
        try:
            await self.zaptec_obj.set_three_to_one_phase_switch_current(value)
        except Exception as exc:
            raise HomeAssistantError(
                f"Setting three to one phase switch current to {value} failed"
            ) from exc

        await self.trigger_poll()


class ZaptecSettingNumber(ZaptecNumber):
    """Zaptec setting number entity."""

    zaptec_obj: Charger
    entity_description: ZapNumberEntityDescription

    def _post_init(self):
        # Get the max current rating from the reported max current
        self.entity_description = replace(
            self.entity_description,
            native_max_value=self.zaptec_obj.get("ChargeCurrentInstallationMaxLimit", 32),
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        self._log_number(value)
        try:
            await self.zaptec_obj.set_settings({self.entity_description.setting: value})
        except Exception as exc:
            raise HomeAssistantError(
                f"Setting {self.entity_description.setting} to {value} failed"
            ) from exc

        await self.trigger_poll()


class ZaptecHmiBrightness(ZaptecNumber):
    """Zaptec HMI brightness number entity."""

    zaptec_obj: Charger
    entity_description: ZapNumberEntityDescription
    _log_attribute = "_attr_native_value"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_native_value = float(self._get_zaptec_value()) * 100
        self._attr_available = True

    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        self._log_number(value)
        try:
            await self.zaptec_obj.set_hmi_brightness(value / 100)
        except Exception as exc:
            raise HomeAssistantError(f"Set HmiBrightness to {value} failed") from exc

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapNumberEntityDescription(NumberEntityDescription):
    """Class describing Zaptec number entities."""

    cls: type[NumberEntity]
    setting: str | None = None


INSTALLATION_ENTITIES: list[EntityDescription] = [
    ZapNumberEntityDescription(
        key="available_current",
        translation_key="available_current",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=0,
        icon="mdi:waves",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecAvailableCurrentNumber,
    ),
    ZapNumberEntityDescription(
        key="three_to_one_phase_switch_current",
        translation_key="three_to_one_phase_switch_current",
        device_class=NumberDeviceClass.CURRENT,
        native_min_value=0,
        native_max_value=32,
        icon="mdi:waves",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecThreeToOnePhaseSwitchCurrent,
    ),
]

CHARGER_ENTITIES: list[EntityDescription] = [
    ZapNumberEntityDescription(
        key="charger_min_current",
        translation_key="charger_min_current",
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=32,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecSettingNumber,
        setting="minChargeCurrent",
    ),
    ZapNumberEntityDescription(
        key="charger_max_current",
        translation_key="charger_max_current",
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=32,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecSettingNumber,
        setting="maxChargeCurrent",
    ),
    ZapNumberEntityDescription(
        key="hmi_brightness",
        translation_key="hmi_brightness",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:brightness-6",
        native_unit_of_measurement=const.PERCENTAGE,
        cls=ZaptecHmiBrightness,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec numbers."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, True)
