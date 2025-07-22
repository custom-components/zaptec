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
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
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
    DOMAIN,
    MANUFACTURER,
    MISSING,
    REQUEST_REFRESH_DELAY,
    ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS,
    ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS,
    ZAPTEC_POLL_INTERVAL_BUILD,
    ZAPTEC_POLL_INTERVAL_CHARGING,
    ZAPTEC_POLL_INTERVAL_IDLE,
    ZAPTEC_POLL_INTERVAL_INFO,
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


class KeyUnavailableError(Exception):
    """Exception raised when a key is not available in the Zaptec object."""


@dataclass(frozen=True, kw_only=True)
class ZaptecEntityDescription(EntityDescription):
    """Class describing Zaptec entities."""

    cls: type[ZaptecBaseEntity]


@dataclass(frozen=True, kw_only=True)
class ZaptecUpdateOptions:
    """Options for the Zaptec update coordinator."""

    name: str
    update_interval: int
    charging_update_interval: int | None
    tracked_devices: set[str]
    poll_args: dict[str, bool]
    zaptec_object: ZaptecBase | None


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

    # Create the Zaptec object
    zaptec = Zaptec(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        client=async_get_clientsession(hass),
        max_time=ZAPTEC_POLL_INTERVAL_CHARGING,  # The shortest of the intervals
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

    # Setup the head update coordinator
    manager.head_coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
        manager=manager,
        options=ZaptecUpdateOptions(
            name="zaptec",
            update_interval=ZAPTEC_POLL_INTERVAL_BUILD,
            charging_update_interval=None,
            tracked_devices=manager.tracked_devices,
            poll_args={"state": False, "info": False, "firmware": True},
            zaptec_object=None,
        ),
    )
    # Dummy listener to ensure the coordinator runs
    manager.head_coordinator.async_add_listener(lambda: None)

    # Setup the info update coordinator
    manager.info_coordinator = ZaptecUpdateCoordinator(
        hass,
        entry=entry,
        manager=manager,
        options=ZaptecUpdateOptions(
            name="info",
            update_interval=ZAPTEC_POLL_INTERVAL_INFO,
            charging_update_interval=None,
            tracked_devices=manager.tracked_devices,
            poll_args={"state": False, "info": True, "firmware": False},
            zaptec_object=None,
        ),
    )
    # Dummy listener to ensure the coordinator runs
    manager.info_coordinator.async_add_listener(lambda: None)

    # Setup the device coordinators for each tracked device
    for deviceid in tracked_devices:
        zaptec_obj = zaptec[deviceid]

        if isinstance(zaptec_obj, Installation):
            # Since installations do not have a state, we only poll the info endpoint.
            # The polling interval for installations does not change when charging.
            poll_args = {"state": False, "info": True, "firmware": False}
            charging_update_interval = None
        else:
            # Only chargers will have an alternate update interval when charging
            poll_args = {"state": True, "info": False, "firmware": False}
            charging_update_interval = ZAPTEC_POLL_INTERVAL_CHARGING

        manager.device_coordinators[deviceid] = ZaptecUpdateCoordinator(
            hass,
            entry=entry,
            manager=manager,
            options=ZaptecUpdateOptions(
                name=deviceid,
                update_interval=ZAPTEC_POLL_INTERVAL_IDLE,
                charging_update_interval=charging_update_interval,
                tracked_devices={deviceid},  # One device per coordinator
                poll_args=poll_args,
                zaptec_object=zaptec_obj,
            ),
        )

    # Initialize the coordinators
    for co in manager.all_coordinators:
        await co.async_config_entry_first_refresh()

    # Attach the local data to the HA config entry so it can be accessed later
    # in various HA functions.
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

    head_coordinator: ZaptecUpdateCoordinator
    """The account-level coordinator for the Zaptec account."""

    info_coordinator: ZaptecUpdateCoordinator
    """Coordinator for the device info updates."""

    device_coordinators: dict[str, ZaptecUpdateCoordinator]
    """Coordinators for the devices, both installation and chargers."""

    streams: list[tuple[asyncio.Task, Installation]]
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
        self.device_coordinators = {}
        self.streams = []

    @property
    def all_coordinators(self) -> Iterable[ZaptecUpdateCoordinator]:
        """Return all coordinators for the Zaptec objects."""
        return [
            self.head_coordinator,
            self.info_coordinator,
            *self.device_coordinators.values(),
        ]

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
            entity = cls(
                coordinator=self.device_coordinators[zaptec_obj.id],
                zaptec_object=zaptec_obj,
                description=copy(description),
                device_info=dev_info,
            )

            # Check if the zaptec data for the object is available before
            # adding it to the list of entities. The caveat is that if the
            # entity have been added earlier, it will now be listed as
            # "This entity is no longer being provided by the zaptec integration."
            updater = getattr(entity, "_update_from_zaptec", lambda: None)
            try:
                updater()
            except Exception:
                _LOGGER.exception(
                    "Failed to add entity %s keys %s, skipping entity",
                    cls.__name__,
                    description.key,
                )
                continue

            entities.append(entity)

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
        charger_id = event.get("ChargerId")
        coordinator = self.device_coordinators.get(charger_id)
        if coordinator is None:
            _LOGGER.debug(
                "Received stream update for unknown charger %s, ignoring",
                charger_id,
            )
            return
        coordinator.async_update_listeners()

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
        self._trigger_task: asyncio.Task | None = None
        self._default_update_interval = timedelta(seconds=options.update_interval)
        self._charging_update_interval = (
            timedelta(seconds=options.charging_update_interval)
            if options.charging_update_interval is not None
            else None
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-{entry.data['username']}-{options.name}",
            update_interval=self._default_update_interval,
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REQUEST_REFRESH_DELAY,
                immediate=False,
            ),
        )

        # Install the listener to select the update interval on the state
        # of the charger. This only works with a Charger object.
        if options.charging_update_interval is not None:
            if not isinstance(options.zaptec_object, Charger):
                raise ValueError("Charging update interval requires a Charger object")
            self.async_add_listener(self.set_update_interval)

    def set_update_interval(self) -> None:
        """Set the update interval for the coordinator.

        This function is called on data updates from the coordinator.
        """
        zaptec_obj: Charger = self.options.zaptec_object
        current = self.update_interval
        want = (
            self._charging_update_interval
            if zaptec_obj.is_charging()
            else self._default_update_interval
        )
        if current != want:
            _LOGGER.debug(
                "%s is now %s, setting update interval to %s (was %s)",
                zaptec_obj.qual_id,
                zaptec_obj.get("ChargerOperationMode", "Unknown"),
                want,
                current,
            )
            self.update_interval = want
            self._schedule_refresh()

    async def _async_update_data(self) -> None:
        """Poll data from Zaptec."""

        try:
            _LOGGER.debug(">>> Polling %s from Zaptec", self.options.name)
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

        if isinstance(obj, Installation):
            delays = ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS
        else:
            delays = ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS

        _LOGGER.debug("Triggering poll of %s after %s seconds", obj.qual_id, delays)

        # Calculcate the deltas for the delays. E.g. [2, 5, 10] -> [2, 3, 5]
        deltas = [b - a for a, b in zip([0] + delays[:-1], delays)]

        for i, delta in enumerate(deltas, start=1):
            await asyncio.sleep(delta)
            _LOGGER.debug(
                "Triggering poll %s of %s after %s seconds",
                i,
                obj.qual_id,
                delta,
            )
            await self.async_refresh()

    async def trigger_poll(self, obj: ZaptecBase) -> None:
        """Trigger a poll update sequence."""

        # If there is a curent poll task running, cancel it
        if self._trigger_task is not None:
            _LOGGER.debug(
                "A poll task is already running for %s, cancelling it",
                obj.qual_id,
            )
            self._trigger_task.cancel()
            try:
                await self._trigger_task
            except asyncio.CancelledError:
                pass
            finally:
                self._trigger_task = None

        def cleanup_task(_task: asyncio.Task):
            """Cleanup the task after it has run."""
            self._trigger_task = None

        self._trigger_task = self.config_entry.async_create_background_task(
            self.hass,
            self._trigger_poll(obj),
            f"Zaptec Poll Update for {obj.qual_id}",
        )
        self._trigger_task.add_done_callback(cleanup_task)


class ZaptecBaseEntity(CoordinatorEntity[ZaptecUpdateCoordinator]):
    """Base class for Zaptec entities."""

    coordinator: ZaptecUpdateCoordinator
    zaptec_obj: ZaptecBase
    entity_description: EntityDescription
    _attr_has_entity_name = True
    _prev_value: Any = MISSING
    _log_attribute: str | None = None
    """The attribute to log when the value changes."""

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update the entity from Zaptec data.

        If the class have an attribute callback `_update_from_zaptec`, it will
        be called to update the entity data from the Zaptec data. The method is
        expected to call `_get_zaptec_value()` to retrieve the value for the
        entity, which may raise `KeyUnavailableError` if the key is not
        available. This function will log the value if it changes or becomes
        unavailable.
        """
        prev_available = self._attr_available
        update_from_zaptec = getattr(self, "_update_from_zaptec", lambda: None)
        try:
            update_from_zaptec()
            self._log_value(self._log_attribute)
        except KeyUnavailableError as exc:
            self._attr_available = False
            self._log_unavailable(exc, prev_available)
        super()._handle_coordinator_update()

    @callback
    def _get_zaptec_value(self, *, default=MISSING, key=None) -> Any:
        """Retrieve a value from the Zaptec object.

        Helper to retrieve the value from the Zaptec object. This is to
        be called from _handle_coordinator_update() in the inheriting class.
        It will fetch the attr given by the entity description key.

        Raises:
            KeyUnavailableError: If key doesn't exist or obj doesn't have
            `.get()`, which indicates that obj isn't a Mapping-like object
        """
        obj = self.zaptec_obj
        key = key or self.key
        for k in key.split("."):
            try:
                obj = obj.get(k, default)
            except AttributeError:
                # This means that obj doesn't have `.get()`, which indicates that obj isn't a
                # a Mapping-like object.
                raise KeyUnavailableError(
                    f"Failed to retrieve {key!r} from {self.zaptec_obj.qual_id}. Failed getting {k!r}"
                ) from None
            if obj is MISSING:
                raise KeyUnavailableError(
                    f"Failed to retrieve {key!r} from {self.zaptec_obj.qual_id}. Key {k!r} doesn't exist"
                )
            if obj is default:
                return obj
        return obj

    @callback
    def _log_value(self, attribute: str | None, force=False):
        """Helper to log a new value."""
        if attribute is None:
            return
        value = getattr(self, attribute, MISSING)
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
    def _log_unavailable(
        self, exception: Exception | None = None, prev_available: bool | None = None
    ):
        """Helper to log when unavailable."""
        prev_available = (
            prev_available if prev_available is not None else self._attr_available
        )
        available = self._attr_available

        # Log when the entity becomes unavailable
        if not available:
            exc_text = f"   (Error: {exception})" if exception else ""
            _LOGGER.debug(
                "    %s  =  UNAVAILABLE   in %s%s",
                self.entity_id,
                self.zaptec_obj.qual_id,
                exc_text,
            )

        # Dump the traceback only once when the error occurs
        if exception is not None and prev_available and not available:
            _LOGGER.error("Getting value failed", exc_info=exception)

    @property
    def key(self):
        """Helper to retrieve the key from the entity description."""
        return self.entity_description.key

    async def trigger_poll(self) -> None:
        """Trigger a poll for this entity."""
        await self.coordinator.trigger_poll(self.zaptec_obj)
