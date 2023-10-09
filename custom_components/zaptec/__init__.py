"""Zaptec component."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import Account, Charger, Circuit, Installation, ZaptecApiError, ZaptecBase
from .const import (
    API_TIMEOUT,
    CONF_CHARGERS,
    CONF_MANUAL_SELECT,
    CONF_PREFIX,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MISSING,
    REQUEST_REFRESH_DELAY,
    STARTUP,
)

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
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
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
    _LOGGER.debug("Reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


class ZaptecUpdateCoordinator(DataUpdateCoordinator[None]):
    account: Account
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, *, entry: ConfigEntry) -> None:
        """Initialize account-wide Zaptec data updater."""

        _LOGGER.debug("Setting up coordinator")

        self.account = Account(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            client=async_get_clientsession(hass),
        )

        scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{entry.data['username']}",
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_DELAY,
                immediate=False,
            ),
        )

    async def cancel_streams(self):
        await asyncio.gather(*(i.cancel_stream() for i in self.account.installations))

    @callback
    async def _stream_update(self, event):
        """Handle new update event from the zaptec stream. The zaptec objects
        are updated in-place prior to this callback being called.
        """
        self.async_update_listeners()

    async def _async_update_data(self) -> None:
        """Fetch data from Zaptec."""

        try:
            # This timeout is only a safeguard against the API methods locking
            # up. The API methods themselves have their own timeouts.
            async with async_timeout.timeout(10 * API_TIMEOUT):
                if not self.account.is_built:
                    # Build the Zaptec hierarchy
                    await self.account.build()

                    # Get the list if chargers to include
                    chargers = None
                    if self.config_entry.data.get(CONF_MANUAL_SELECT, False):
                        chargers = self.config_entry.data.get(CONF_CHARGERS)

                    # Selected chargers to add
                    if chargers is not None:
                        _LOGGER.debug("Configured chargers: %s", chargers)
                        want = set(chargers)
                        all_objects = set(self.account.map.keys())

                        # Log if there are any objects listed not found in Zaptec
                        not_present = want - all_objects
                        if not_present:
                            _LOGGER.error("Charger objects %s not found", not_present)

                        # Calculate the objects to keep. From the list of chargers
                        # we want to keep, we also want to keep the circuit and
                        # installation objects.
                        keep = set()
                        for charger in self.account.get_chargers():
                            if charger.id in want:
                                keep.add(charger.id)
                                if charger.circuit:
                                    keep.add(charger.circuit.id)
                                    if charger.circuit.installation:
                                        keep.add(charger.circuit.installation.id)

                        if not keep:
                            _LOGGER.error("No zaptec objects will be added")

                        # Unregister all discovered objects not in the keep list
                        for objid in all_objects:
                            if objid not in keep:
                                _LOGGER.debug("Unregistering: %s", objid)
                                self.account.unregister(objid)

                    # Setup the stream subscription
                    for install in self.account.installations:
                        if install.id in self.account.map:
                            await install.stream(cb=self._stream_update)

                # Fetch updates
                await self.account.update_states()

        except ZaptecApiError as err:
            _LOGGER.exception(
                "Fetching data failed: %s: %s", type(err).__qualname__, err
            )
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
        """Called after the entity has been initialized. Implement this for a
        custom light-weight init in the inheriting class.
        """

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._update_from_zaptec()
        except Exception as exc:
            raise HomeAssistantError(f"Error updating entity {self.key}") from exc
        super()._handle_coordinator_update()

    @callback
    def _update_from_zaptec(self) -> None:
        """Called when the coordinator has new data. Implement this in the
        inheriting class to update the entity state.
        """

    @callback
    def _get_zaptec_value(self, *, default=MISSING, key=None):
        """Helper to retrieve the value from the Zaptec object. This is to
        be called from _handle_coordinator_update() in the inheriting class.
        It will fetch the attr given by the entity description key.
        """
        obj = self.zaptec_obj
        key = key or self.key
        for k in key.split("."):
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
        """Helper to log a new value. This is to be called from
        _handle_coordinator_update() in the inheriting class.
        """
        prev = self._prev_value
        if force or value != prev:
            self._prev_value = value
            # Only logs when the value changes
            _LOGGER.debug(
                "    %s.%s  =  <%s> %s   (in %s %s)",
                self.__class__.__qualname__,
                self.key,
                type(value).__qualname__,
                value,
                type(self.zaptec_obj).__qualname__,
                self.zaptec_obj.name,
            )

    @callback
    def _log_unavailable(self):
        """Helper to log when unavailable."""
        _LOGGER.debug(
            "    %s.%s  =  UNAVAILABLE   (in %s %s)",
            self.__class__.__qualname__,
            self.key,
            type(self.zaptec_obj).__qualname__,
            self.zaptec_obj.name,
        )

    @property
    def key(self):
        """Helper to retrieve the key from the entity description."""
        return self.entity_description.key

    @classmethod
    def create_from_descriptions(
        cls,
        descriptions: list[EntityDescription],
        coordinator: ZaptecUpdateCoordinator,
        zaptec_obj: ZaptecBase,
        device_info: DeviceInfo,
    ) -> list[ZaptecBaseEntity]:
        """Helper factory to create a list of entities from a list of
        EntityDescription objects.
        """

        # Calculate the prefix to use for the entity name
        prefix = coordinator.config_entry.data.get(CONF_PREFIX, "").rstrip()
        if prefix:
            prefix = prefix + " "

        # Start with the common device info and append the provided device info
        dev_info = DeviceInfo(
            manufacturer=MANUFACTURER,
            identifiers={(DOMAIN, zaptec_obj.id)},
            name=prefix + zaptec_obj.name,
        )
        dev_info.update(device_info)

        entities: list[ZaptecBaseEntity] = []
        for description in descriptions:
            # Use provided class if it exists, otherwise use the class this
            # function was called from
            klass: type[ZaptecBaseEntity] = getattr(description, "cls", cls) or cls

            # Create the entity object
            _LOGGER.debug(
                "Adding %s.%s   (in %s %s)",
                klass.__qualname__,
                description.key,
                type(zaptec_obj).__qualname__,
                zaptec_obj.name,
            )
            entity = klass(coordinator, zaptec_obj, description, dev_info)
            entities.append(entity)

        return entities

    @classmethod
    def create_from_zaptec(
        cls,
        coordinator: ZaptecUpdateCoordinator,
        installation_descriptions: list[EntityDescription],
        circuit_descriptions: list[EntityDescription],
        charger_descriptions: list[EntityDescription],
    ) -> list[ZaptecBaseEntity]:
        """Helper factory to populate the listed entities for the detected
        Zaptec devices. It sets the proper device info on the installation,
        circuit and charger object in order for them to be grouped in HA.
        """
        entities = []

        # Iterate over every zaptec object in the account mapping and add
        # the listed entities for each object type
        for obj in coordinator.account.map.values():
            if isinstance(obj, Installation):
                info = DeviceInfo(model=f"{obj.name} Installation")

                entities.extend(
                    cls.create_from_descriptions(
                        installation_descriptions,
                        coordinator,
                        obj,
                        info,
                    )
                )

            elif isinstance(obj, Circuit):
                info = DeviceInfo(model=f"{obj.name} Circuit")
                if obj.installation:
                    info["via_device"] = (DOMAIN, obj.installation.id)

                entities.extend(
                    cls.create_from_descriptions(
                        circuit_descriptions,
                        coordinator,
                        obj,
                        info,
                    )
                )

            elif isinstance(obj, Charger):
                info = DeviceInfo(model=f"{obj.name} Charger")
                if obj.circuit:
                    info["via_device"] = (DOMAIN, obj.circuit.id)

                entities.extend(
                    cls.create_from_descriptions(
                        charger_descriptions,
                        coordinator,
                        obj,
                        info,
                    )
                )

            else:
                _LOGGER.error("Unknown zaptec object type: %s", type(obj).__qualname__)

        return entities
