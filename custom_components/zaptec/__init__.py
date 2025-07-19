"""Zaptec component."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from copy import copy
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util.ssl import get_default_context

from .api import (
    AuthenticationError,
    Charger,
    Installation,
    RequestConnectionError,
    RequestTimeoutError,
    Zaptec,
    ZaptecApiError,
    ZaptecBase,
)
from .const import (
    CONF_CHARGERS,
    CONF_MANUAL_SELECT,
    CONF_PREFIX,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MISSING,
    REQUEST_REFRESH_DELAY,
    ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS,
    ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS,
)
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

type ZaptecConfigEntry = ConfigEntry[ZaptecManager]

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


@dataclass(frozen=True, kw_only=True)
class ZaptecEntityDescription(EntityDescription):
    """Class describing Zaptec binary sensor entities."""

    cls: type[ZaptecBaseEntity]


@dataclass(frozen=True, kw_only=True)
class ZaptecUpdateOptions:
    """Options for the Zaptec update coordinator."""

    name: str
    update_interval: int
    tracked_devices: set[str]
    poll_args: dict[str, bool]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up zaptec as config entry."""

    redacted_data = {**entry.data}
    for key in ("password", "username"):
        if key in redacted_data:
            redacted_data[key] = "********"

    _LOGGER.debug("Setting up entry %s: %s", entry.entry_id, redacted_data)

    configured_chargers = None
    if entry.data.get(CONF_MANUAL_SELECT, False):
        configured_chargers = entry.data.get(CONF_CHARGERS)

    prefix = entry.data.get(CONF_PREFIX, "").rstrip()
    if prefix:
        prefix = prefix + " "

    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # Create the Zaptec object
    zaptec = Zaptec(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=async_get_clientsession(hass),
        max_time=scan_interval,
    )

    # Login to the Zaptec account
    try:
        await zaptec.login()
    except AuthenticationError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise ConfigEntryAuthFailed from err
    except (RequestTimeoutError, RequestConnectionError) as err:
        _LOGGER.error("Connection error: %s", err)
        raise ConfigEntryNotReady from err
    except ZaptecApiError as err:
        _LOGGER.error("Zaptec API error: %s", err)
        raise ConfigEntryError from err

    # Get the structure of devices from Zaptec and determine the zaptec objects to track
    tracked_devices = await ZaptecManager.first_time_setup(
        zaptec=zaptec,
        configured_chargers=configured_chargers,
    )

    # Setup the manager which will be where all the instance data is stored
    manager = ZaptecManager(
        hass,
        entry=entry,
        zaptec=zaptec,
        name_prefix=prefix,
        tracked_devices=tracked_devices,
    )

    # Setup the update coordinator
    manager.coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
        manager=manager,
        options=ZaptecUpdateOptions(
            name="zaptec",
            update_interval=scan_interval,
            tracked_devices=manager.tracked_devices,
            poll_args={"state": True, "info": True, "firmware": True},
        ),
    )
    await manager.coordinator.async_config_entry_first_refresh()

    # Install the local data for later retrieval
    entry.runtime_data = manager

    # Setup services
    await async_setup_services(hass, manager)

    # Setup all platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Setup the streams
    manager.create_streams()

    # Make a set of the circuit ids from zaptec to check for deprecated Circuit-devices
    circuit_ids = {cid for c in manager.zaptec.chargers if (cid := c.get("CircuitId"))}

    # Clean up unused device entries with no entities
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry_id=entry.entry_id
    )
    for dev in device_entries:
        dev_entities = er.async_entries_for_device(
            entity_registry, dev.id, include_disabled_entities=True
        )
        if not dev_entities:
            device_registry.async_remove_device(dev.id)
            continue
        # identifiers is a set with a (single) tuple ('zaptec', '<zaptec_id>')
        for _, zap_dev_id in dev.identifiers:
            if zap_dev_id in circuit_ids:
                _LOGGER.warning(
                    "Detected deprecated Circuit device %s, removing device and associated entities",
                    zap_dev_id,
                )
                for ent in dev_entities:
                    _LOGGER.debug("Deleting entity %s", ent.entity_id)
                    entity_registry.async_remove(ent.entity_id)
                device_registry.async_remove_device(dev.id)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZaptecConfigEntry) -> bool:
    """Unload a config entry."""

    manager = entry.runtime_data
    await manager.cancel_streams()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await async_unload_services(hass)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


class ZaptecManager:
    """Manager for Zaptec data."""

    # NOTE: This class may appear excessive since there's currently only one
    # coordinator. The data and methods could be placed directly in the
    # coordinator class. However, using a separate manager makes it easier to
    # expand and support multiple coordinators in the future.

    zaptec: Zaptec
    """The Zaptec account object."""

    tracked_devices: set[str]
    """Set of tracked device that will be used by the integration."""

    name_prefix: str
    """Name prefix to use for the entities."""

    coordinator: ZaptecUpdateCoordinator
    """The coordinator for the Zaptec account."""

    streams: list[tuple[asyncio.Task, Installation]] = []
    """List of active streams for the installations."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry: ZaptecConfigEntry,
        zaptec: Zaptec,
        tracked_devices: set[str] | None = None,
        name_prefix: str = "",
    ) -> None:
        """Initialize the Zaptec manager."""
        self.hass = hass
        self.config_entry = entry
        self.zaptec = zaptec
        self.tracked_devices = tracked_devices or set()
        self.name_prefix = name_prefix
        self.streams = []

    def create_entities_from_descriptions(
        self,
        descriptions: Iterable[ZaptecEntityDescription],
        zaptec_obj: ZaptecBase,
        device_info: DeviceInfo,
    ) -> list[ZaptecBaseEntity]:
        """Factory to create a list of entities from EntityDescription objects."""

        # Start with the common device info and append the provided device info
        dev_info = DeviceInfo(
            manufacturer=MANUFACTURER,
            identifiers={(DOMAIN, zaptec_obj.id)},
            name=self.name_prefix + zaptec_obj.name,
        )
        dev_info.update(device_info)

        entities: list[ZaptecBaseEntity] = []
        for description in descriptions:
            # Use provided class if it exists, otherwise use the class this
            # function was called from
            cls: type[ZaptecBaseEntity] = description.cls
            # NOTE! In later versions, this needs to loop up which coordinator
            # the entity should be registered to.
            entities.append(
                cls(
                    coordinator=self.coordinator,
                    zaptec_object=zaptec_obj,
                    description=copy(description),
                    device_info=dev_info,
                )
            )

        return entities

    def create_entities_from_zaptec(
        self,
        installation_descriptions: Iterable[EntityDescription],
        charger_descriptions: Iterable[EntityDescription],
    ) -> list[ZaptecBaseEntity]:
        """Factory to entities from the discovered Zaptec objects.

        Helper factory to populate the listed entities for the detected
        Zaptec devices. It sets the proper device info on the installation
        and charger objects in order for them to be grouped in HA.
        """
        entities = []

        # Iterate over every zaptec object in the account mapping and add
        # the listed entities for each object type
        for obj in self.zaptec.objects():
            if isinstance(obj, Installation):
                info = DeviceInfo(model=f"{obj.name} Installation")

                entities.extend(
                    self.create_entities_from_descriptions(
                        installation_descriptions,
                        obj,
                        info,
                    )
                )

            elif isinstance(obj, Charger):
                info = DeviceInfo(model=f"{obj.name} Charger")
                if obj.installation:
                    info["via_device"] = (DOMAIN, obj.installation.id)

                entities.extend(
                    self.create_entities_from_descriptions(
                        charger_descriptions,
                        obj,
                        info,
                    )
                )

            else:
                _LOGGER.error("Unknown zaptec object type: %s", type(obj).__qualname__)

        return entities

    def create_streams(self):
        """Create the streams for all installations."""
        for install in self.zaptec.installations:
            if install.id in self.zaptec:
                task = self.config_entry.async_create_background_task(
                    self.hass,
                    install.stream_main(
                        cb=self.stream_callback,
                        ssl_context=get_default_context(),
                    ),
                    name=f"Zaptec Stream for {install.qual_id}",
                )
                self.streams.append((task, install))

    async def cancel_streams(self):
        """Cancel all streams for the account."""
        for task, install in self.streams:
            _LOGGER.debug("Cancelling stream for %s", install.qual_id)
            await install.stream_close()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def stream_callback(self, event):
        """Handle new update event from the zaptec stream.

        The zaptec objects are updated in-place prior to this callback being called.
        """
        # FIXME: Later implementation will need to figure out which coordinator
        # to update based on the event payload.
        self.coordinator.async_update_listeners()

    @staticmethod
    async def first_time_setup(
        zaptec: Zaptec, configured_chargers: set[str] | None
    ) -> set[str]:
        """Run the first time setup for the account."""
        _LOGGER.debug("Running first time setup")

        # Build the Zaptec hierarchy
        await zaptec.build()

        all_objects = set(zaptec)
        tracked_devices = all_objects

        # Selected chargers to add
        if configured_chargers is not None:
            _LOGGER.debug("Configured chargers: %s", configured_chargers)
            want = set(configured_chargers)

            # Log if there are any objects listed not found in Zaptec
            if not_present := want - all_objects:
                _LOGGER.error("Charger objects %s not found", not_present)

            # Calculate the objects to keep. From the list of chargers we
            # want to keep, we also want to keep the installation objects.
            keep = set()
            for charger in zaptec.chargers:
                if charger.id in want:
                    keep.add(charger.id)
                    if charger.installation:
                        keep.add(charger.installation.id)

            if not keep:
                _LOGGER.error("No zaptec objects will be added")

            # These objects will be updated by the coordinator
            tracked_devices = keep

        return tracked_devices


class ZaptecUpdateCoordinator(DataUpdateCoordinator[None]):
    """Coordinator for Zaptec data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry: ZaptecConfigEntry,
        manager: ZaptecManager,
        options: ZaptecUpdateOptions,
    ) -> None:
        """Initialize account-wide Zaptec data updater."""
        self.manager: ZaptecManager = manager
        self.options: ZaptecUpdateOptions = options
        self.zaptec: Zaptec = manager.zaptec

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-{entry.data['username']}-{options.name}",
            update_interval=timedelta(seconds=self.options.update_interval),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_DELAY,
                immediate=False,
            ),
        )

    async def _async_update_data(self) -> None:
        """Poll data from Zaptec."""

        try:
            _LOGGER.debug("Polling from Zaptec")
            await self.zaptec.poll(
                self.options.tracked_devices,
                **self.options.poll_args,
            )
        except ZaptecApiError as err:
            _LOGGER.exception(
                "Fetching data failed: %s: %s", type(err).__qualname__, err
            )
            raise UpdateFailed(err) from err

    async def _trigger_poll(self, obj: ZaptecBase) -> None:
        """Trigger a poll update sequence for the given object.

        This sequence is useful to ensure that the state is fully synced after a
        HA initiated update.
        """

        what = {obj.id}
        if isinstance(obj, Installation):
            delays = ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS
            kw = {"state": True, "info": True}
        else:
            delays = ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS
            kw = {"state": True}

        # Calculcate the deltas for the delays. E.g. [2, 5, 10] -> [2, 3, 5]
        deltas = [b - a for a, b in zip([0] + delays[:-1], delays)]

        for i, delta in enumerate(deltas, start=1):
            await asyncio.sleep(delta)
            _LOGGER.debug(
                "Triggering poll %s of %s after %s seconds. %s",
                i,
                obj.qual_id,
                delta,
                kw,
            )
            await self.zaptec.poll(what, **kw)
            self.async_update_listeners()

    async def trigger_poll(self, obj: ZaptecBase) -> None:
        """Trigger a poll update sequence."""

        # FIXME: The current imeplementation will create a new background task
        # no matter if a task is already running. If they are updating the same
        # object, this can cause too many updates for the same object.
        # A single task per device will be needed.

        self.config_entry.async_create_background_task(
            self.hass,
            self._trigger_poll(obj),
            f"Zaptec Poll Update for {obj.qual_id}",
        )


class ZaptecBaseEntity(CoordinatorEntity[ZaptecUpdateCoordinator]):
    """Base class for Zaptec entities."""

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
        """Initialize the Zaptec entity."""
        super().__init__(coordinator)

        self.zaptec_obj = zaptec_object
        self.entity_description = description
        self._attr_unique_id = f"{zaptec_object.id}_{description.key}"
        self._attr_device_info = device_info

        # Call this last if the inheriting class needs to do some addition
        # initialization
        self._post_init()

    def _post_init(self) -> None:
        """Post-initialization method for the entity.

        Called after the entity has been initialized. Implement this for a
        custom light-weight init in the inheriting class.
        """

    async def async_added_to_hass(self) -> None:
        """Callback when entity is registered in HA."""
        await super().async_added_to_hass()

        _LOGGER.debug("Adding entity  %s  -->  %s.%s",
                      self.entity_id, self.zaptec_obj.qual_id, self.key,
                     )

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._update_from_zaptec()
        except Exception as exc:
            raise HomeAssistantError(f"Error updating entity {self.key}") from exc
        super()._handle_coordinator_update()

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity state from the Zaptec object.

        Called when the coordinator has new data. Implement this in the
        inheriting class to update the entity state.
        """

    @callback
    def _get_zaptec_value(self, *, default=MISSING, key=None):
        """Retrieve a value from the Zaptec object.

        Helper to retrieve the value from the Zaptec object. This is to
        be called from _handle_coordinator_update() in the inheriting class.
        It will fetch the attr given by the entity description key.
        """
        obj = self.zaptec_obj
        key = key or self.key
        for k in key.split("."):
            # Also do dict because some object contains sub-dicts
            if isinstance(obj, (ZaptecBase, dict)):
                obj = obj.get(k, default)
            else:
                raise HomeAssistantError(
                    f"Object {type(obj).__qualname__} is not supported"
                )
            if obj is MISSING:
                raise HomeAssistantError(
                    f"Zaptec object {self.zaptec_obj.qual_id} does not have key {key}"
                )
            if obj is default:
                return obj
        return obj

    @callback
    def _log_value(self, value, force=False):
        """Helper to log a new value."""
        prev = self._prev_value
        if force or value != prev:
            self._prev_value = value
            # Only logs when the value changes
            _LOGGER.debug(
                "    %s  =  <%s> %s   from %s",
                self.entity_id,
                type(value).__qualname__,
                value,
                self.zaptec_obj.qual_id,
            )

    @callback
    def _log_unavailable(self):
        """Helper to log when unavailable."""
        _LOGGER.debug(
            "    %s  =  UNAVAILABLE   in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

    @property
    def key(self):
        """Helper to retrieve the key from the entity description."""
        return self.entity_description.key

    async def trigger_poll(self) -> None:
        """Trigger a poll for this entity."""
        await self.coordinator.trigger_poll(self.zaptec_obj)
