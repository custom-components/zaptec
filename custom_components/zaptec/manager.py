"""Zaptec integration manager."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import contextlib
from copy import copy
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util.ssl import get_default_context

from .const import DOMAIN, KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK, MANUFACTURER
from .coordinator import ZaptecUpdateCoordinator
from .entity import KeyUnavailableError, ZaptecBaseEntity
from .zaptec import Charger, Installation, Zaptec, ZaptecBase

_LOGGER = logging.getLogger(__name__)

type ZaptecConfigEntry = ConfigEntry[ZaptecManager]


@dataclass(frozen=True, kw_only=True)
class ZaptecEntityDescription(EntityDescription):
    """Class describing Zaptec entities."""

    cls: type[ZaptecBaseEntity]


class ZaptecManager:
    """Manager for Zaptec data."""

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
        """Create entities from EntityDescription objects."""

        # Start with the common device info and append the provided device info
        dev_info = DeviceInfo(
            manufacturer=MANUFACTURER,
            identifiers={(DOMAIN, zaptec_obj.id)},
            model=zaptec_obj.model,
            name=self.name_prefix + zaptec_obj.name,
        )
        dev_info.update(device_info)

        entities: list[ZaptecBaseEntity] = []
        for description in descriptions:
            # Make sure this zaptec object is tracked
            if zaptec_obj.id not in self.tracked_devices:
                continue

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
            except KeyUnavailableError as exc:
                attr = f"'{zaptec_obj.qual_id}.{exc.key}'"
                if description.key in KEYS_TO_SKIP_ENTITY_AVAILABILITY_CHECK:
                    _LOGGER.info(
                        "%s is not available, but adding entity %s %r anyways",
                        attr,
                        cls.__name__,
                        description.key,
                    )
                else:
                    _LOGGER.info(
                        "%s is not available, skip add of entity %s %r",
                        attr,
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
        """Create entities from the present Zaptec objects.

        Helper factory to populate the listed entities for the detected
        Zaptec devices. It sets the proper device info on the installation
        and charger objects in order for them to be grouped in HA.
        """
        entities = []

        # Iterate over every zaptec object in the account mapping and add
        # the listed entities for each object type
        for obj in self.zaptec.objects():
            if isinstance(obj, Installation):
                info = DeviceInfo()

                entities.extend(
                    self.create_entities_from_descriptions(
                        installation_descriptions,
                        obj,
                        info,
                    )
                )

            elif isinstance(obj, Charger):
                info = DeviceInfo()
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

    def create_streams(self) -> None:
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

    async def cancel_streams(self) -> None:
        """Cancel all streams for the account."""
        for task, install in self.streams:
            _LOGGER.debug("Cancelling stream for %s", install.qual_id)
            await install.stream_close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def stream_callback(self, event: dict) -> None:
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
    async def first_time_setup(zaptec: Zaptec, configured_chargers: set[str] | None) -> set[str]:
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
