"""Zaptec component."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (CONF_PASSWORD, CONF_SCAN_INTERVAL,
                                 CONF_USERNAME, Platform)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import (CoordinatorEntity,
                                                      DataUpdateCoordinator,
                                                      UpdateFailed)

from .api import Account, ZaptecApiError, ZaptecBase
from .const import (DEFAULT_SCAN_INTERVAL, DOMAIN, MANUFACTURER, MISSING,
                    REQUEST_REFRESH_DELAY, STARTUP)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    # Platform.DEVICE_TRACKER,
    # Platform.LOCK,
    # Platform.NOTIFY,
    Platform.NUMBER,
    # Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

# FIXME: Informing users that the interface is considerable different
# FIXME: Setting that allows users to continue with old naming scheme?

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration."""
    if DOMAIN in hass.data:
        _LOGGER.info("Delete zaptec from your yaml")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up zaptec as config entry."""

    _LOGGER.info(STARTUP)
    _LOGGER.debug("Setting up entry %s: %s", entry.entry_id, entry.data)

    coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Setup all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    _LOGGER.debug("Unloading entry %s", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.cancel_streams()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class ZaptecUpdateCoordinator(DataUpdateCoordinator[None]):

    account: Account

    def __init__(self, hass: HomeAssistant, *, entry: ConfigEntry) -> None:
        """Initialize account-wide Zaptec data updater."""

        _LOGGER.debug("Setting up coordinator")

        self.account = Account(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            client=async_get_clientsession(hass),
        )

        self._entry = entry
        scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{entry.data['username']}",
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REQUEST_REFRESH_DELAY, immediate=False,
            ),
        )

    async def cancel_streams(self):
        await asyncio.gather(*(
            i.cancel_stream() for i in self.account.installs
        ))

    @callback
    async def _stream_update(self, event):
        """Handle new update event from the zaptec stream. The zaptec objects
           are updated in-place prior to this callback being called.
        """
        self.async_update_listeners()

    async def _async_update_data(self) -> None:
        """Fetch data from Zaptec."""

        try:
            async with async_timeout.timeout(10):
                if not self.account.is_built:
                    # Build the Zaptec hierarchy
                    await self.account.build()

                    # Setup the stream subscription
                    for install in self.account.installs:
                        await install.stream(cb=self._stream_update)
                    return

                # Fetch updates
                for k, v in self.account.map.items():
                    await v.state()
        except ZaptecApiError as err:
            _LOGGER.exception(f"{err}")
            raise UpdateFailed(err) from err


class ZaptecBaseEntity(CoordinatorEntity[ZaptecUpdateCoordinator]):

    coordinator: ZaptecUpdateCoordinator
    zaptec_obj: ZaptecBase
    entity_description: EntityDescription
    _attr_has_entity_name = True
    _prev_value: Any = MISSING

    def __init__(
        self,
        coordinator: ZaptecUpdateCoordinator,
        zaptec_object: ZaptecBase,
        description: EntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)

        self.zaptec_obj = zaptec_object
        self.entity_description = description
        self._attr_unique_id = f"{zaptec_object.id}_{description.key}"
        self._attr_device_info = device_info

        # Call this last if the inheriting class needs to do some addition
        # initialization
        self._post_init()

    def _post_init(self) -> None:
        '''Called after the entity has been initialized. Implement this for a
           custom light-weight init in the inheriting class.
        '''

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._update_from_zaptec()
        except Exception as exc:
            _LOGGER.exception("Error updating switch '%s': %s", self.name, exc)
            self._update_from_zaptec_failed()
            raise HomeAssistantError(exc) from exc
        super()._handle_coordinator_update()

    @callback
    def _update_from_zaptec(self) -> None:
        '''Called when the coordinator has new data. Implement this in the
           inheriting class to update the entity state.
        '''

    @callback
    def _update_from_zaptec_failed(self) -> None:
        '''Called when the coordinator has failed to update. Implement this in
           the inheriting class to update the entity state.
        '''

    @callback
    def _get_zaptec_value(self, *, default=MISSING, key=None):
        '''Helper to retrieve the value from the Zaptec object. This is to
           be called from _handle_coordinator_update() in the inheriting class.
           It will fetch the attr given by the entity description key.
        '''
        obj = self.zaptec_obj
        key = key or self.key
        for k in key.split('.'):
            # Do dict because some object contains sub-dicts which must
            # be handled differently than attributes
            if isinstance(obj, dict):
                if default is MISSING:
                    obj = obj[k]
                else:
                    obj = obj.get(k, default)
            else:
                if default is MISSING:
                    obj = getattr(obj, k)
                else:
                    obj = getattr(obj, k, default)
            if obj is default:
                return obj
        return obj

    @callback
    def _log_value(self, value, force=False):
        '''Helper to log a new value. This is to be called from
           _handle_coordinator_update() in the inheriting class.
        '''
        prev = self._prev_value
        if force or value != prev:
            self._prev_value = value
            # Only logs when the value changes
            _LOGGER.debug("    %s.%s  =  <%s> %s   (in %s)",
                          self.__class__.__qualname__, self.key,
                          type(value).__qualname__, value,
                          self.zaptec_obj.id,
            )

    @classmethod
    def create_from(
        cls,
        sensors: list[EntityDescription],
        coordinator: ZaptecUpdateCoordinator,
        zaptec_obj: ZaptecBase,
        device_info: DeviceInfo,
    ) -> list[ZaptecBaseEntity]:
        '''Helper factory to create a list of entities from a list of
           EntityDescription objects.
        '''

        # Start with the common device info and append the provided device info
        dev_info = DeviceInfo(
            manufacturer=MANUFACTURER,
            identifiers={(DOMAIN, zaptec_obj.id)},
            name=zaptec_obj.name,
        )
        dev_info.update(device_info)

        entities = []
        for description in sensors:
            # Use provided class if it exists, otherwise use the class this
            # function was called from
            klass = getattr(description, "cls", cls) or cls
            entity = klass(coordinator, zaptec_obj, description, dev_info)
            entities.append(entity)
        return entities

    @classmethod
    def create_from_zaptec(
        cls,
        account: Account,
        coordinator: ZaptecUpdateCoordinator,
        installation_entities: list[EntityDescription],
        circuit_entities: list[EntityDescription],
        charger_entities: list[EntityDescription],
    ) -> list[ZaptecBaseEntity]:
        '''Helper factory to populate the listed entities for the detected
           Zaptec devices. It sets the proper device info on the installation,
           circuit and charger object in order for them to be grouped in HA.
        '''
        entities = []

        for zap_install in account.installs:
            entities.extend(cls.create_from(
                installation_entities, coordinator, zap_install,
                DeviceInfo(
                    model=f"{zap_install.name} Installation",
                ),
            ))

            for zap_circuit in zap_install.circuits:
                entities.extend(cls.create_from(
                    circuit_entities, coordinator, zap_circuit,
                    DeviceInfo(
                        model=f"{zap_circuit.name} Circuit",
                        via_device=(DOMAIN, zap_install.id),
                    ),
                ))

                for zap_charger in zap_circuit.chargers:
                    entities.extend(cls.create_from(
                        charger_entities, coordinator, zap_charger,
                        DeviceInfo(
                            model=f"{zap_charger.name} Charger",
                            via_device=(DOMAIN, zap_circuit.id),
                        ),
                    ))

        for zap_charger in account.stand_alone_chargers:
            entities.extend(cls.create_from(
                charger_entities, coordinator, zap_charger,
                DeviceInfo(
                    model=f"{zap_charger.name} Charger",
                ),
            ))

        return entities

    @property
    def key(self):
        '''Helper to retrieve the key from the entity description.'''
        return self.entity_description.key
