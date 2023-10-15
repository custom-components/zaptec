"""Zaptec components services."""
from __future__ import annotations

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry

from .api import Charger, Installation
from .const import DOMAIN

if TYPE_CHECKING:
    from . import ZaptecUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

TServiceHandler = Callable[[ServiceCall], Awaitable[None]]
T = TypeVar("T")

CHARGER_ID_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("charger_id", "device_id", "entity_id"),
                    msg=(
                        "At leas one of 'charger_id', 'device_id' or "
                        "'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Schema(
            {
                vol.Optional("charger_id"): str,
                vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
            }
        ),
    )
)

LIMIT_CURRENT_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("installation_id", "device_id", "entity_id"),
                    msg=(
                        "At least one of 'installation_id', 'device_id' or "
                        "'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Any(
            vol.Schema(
                {
                    vol.Optional("installation_id"): str,
                    vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                    vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                    vol.Required("available_current"): int,
                },
            ),
            vol.Schema(
                {
                    vol.Optional("installation_id"): str,
                    vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                    vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                    vol.Required("available_current_phase1"): int,
                    vol.Required("available_current_phase2"): int,
                    vol.Required("available_current_phase3"): int,
                },
            ),
            msg=(
                "Missing either 'available_current' or all three of "
                "'available_current_phase1' "
                "'available_current_phase2' and 'available_current_phase3'."
            ),
        ),
    )
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for zaptec."""
    _LOGGER.debug("Set up services")

    def get_as_set(service_call: ServiceCall, key: str) -> set[str]:
        data = service_call.data.get(key, [])
        if not isinstance(data, list):
            data = [data]
        return set(data)

    def iter_objects(
        service_call: ServiceCall, mustbe: type[T]
    ) -> Generator[tuple[ZaptecUpdateCoordinator, T], None, None]:
        ent_reg = entity_registry.async_get(hass)
        dev_reg = device_registry.async_get(hass)

        device_ids = get_as_set(service_call, "device_id")
        lookup: dict[str, str] = {}

        # Parse all entities and find their device ids which is appended to the
        # list of devices.
        for entity_id in get_as_set(service_call, "entity_id"):
            entity_entry = ent_reg.async_get(entity_id)
            if entity_entry is None:
                raise HomeAssistantError(f"Unable to find entity '{entity_id}'")
            if not entity_entry.device_id:
                raise HomeAssistantError(f"Entity '{entity_id}' doesn't have a device")
            device_ids.add(entity_entry.device_id)
            lookup[entity_entry.device_id] = f"entity '{entity_id}'"

        # Parse all device ids and find the uid for each device
        uids: set[str] = set()
        for device_id in device_ids:
            device_entry = dev_reg.async_get(device_id)
            err_device = lookup.get(device_id, f"device '{device_id}'")
            if device_entry is None:
                raise HomeAssistantError(f"Unable to find device {err_device}")
            err_device = lookup.get(device_id, f"device {device_entry.name}")
            if not device_entry.identifiers:
                raise HomeAssistantError(f"Unable to find identifiers for {err_device}")
            for domain, uid in device_entry.identifiers:
                if domain != DOMAIN:
                    raise HomeAssistantError(
                        f"Non-zaptec device specified {err_device}"
                    )
                uids.add(uid)
                lookup[uid] = err_device

        # Append any legacy charger_id or installation_id that might be specified
        field = None
        if mustbe is Charger:
            field = "charger_id"
        elif mustbe is Installation:
            field = "installation_id"
        if field:
            uids.update(get_as_set(service_call, field))

        # Any uid specified at all?
        if not uids:
            suffix = f". Missing field '{field}'" if field else ""
            raise HomeAssistantError(f"No zaptec devices specified{suffix}")

        # Loop through every uid and find the object
        for uid in uids:
            # Set the human readable identifier for the error message
            if uid in lookup:
                err_device = f"{lookup[uid]} ({uid})"
            else:
                err_device = f"id {uid}"

            # Get all coordinators and objects that matches the uid from all coordinator objects
            matches = set(
                (coord, obj)
                for coord in hass.data[DOMAIN].values()
                for obj in coord.account.map.values()
                if obj.id == uid
            )
            # Filter out the objects that doesn't match the expected type
            want: set[tuple[ZaptecUpdateCoordinator, T]] = set(
                (a, o) for a, o in matches if isinstance(o, mustbe)
            )

            def cap_first(s):
                return s[0].upper() + s[1:]

            if not matches:
                raise HomeAssistantError(
                    f"Unable to find zaptec object for {err_device}"
                )
            if want != matches:
                raise HomeAssistantError(
                    f"{cap_first(err_device)} is not a {mustbe.__name__}"
                )
            if len(want) > 1:
                _LOGGER.warning(
                    "Unexpected multiple matches for '%s' (%s): %s",
                    err_device,
                    uid,
                    [o.id for _, o in want],
                )

            # Send to caller
            yield from want

    async def service_handle_stop_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called stop charging")
        for coordinator, obj in iter_objects(service_call, Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.stop_charging_final()
            await coordinator.async_request_refresh()

    async def service_handle_resume_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called resume charging")
        for coordinator, obj in iter_objects(service_call, mustbe=Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.resume_charging()
            await coordinator.async_request_refresh()

    async def service_handle_authorize_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called authorize charging")
        for coordinator, obj in iter_objects(service_call, mustbe=Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.authorize_charge()
            await coordinator.async_request_refresh()

    async def service_handle_deauthorize_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called deauthorize charging and stop")
        for coordinator, obj in iter_objects(service_call, mustbe=Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.deauthorize_and_stop()
            await coordinator.async_request_refresh()

    async def service_handle_restart_charger(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called restart charger")
        for coordinator, obj in iter_objects(service_call, mustbe=Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.restart_charger()
            await coordinator.async_request_refresh()

    async def service_handle_upgrade_firmware(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called update firmware")
        for coordinator, obj in iter_objects(service_call, mustbe=Charger):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.upgrade_firmware()
            await coordinator.async_request_refresh()

    async def service_handle_limit_current(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called set current limit")
        available_current = service_call.data.get("available_current")
        available_current_phase1 = service_call.data.get("available_current_phase1")
        available_current_phase2 = service_call.data.get("available_current_phase2")
        available_current_phase3 = service_call.data.get("available_current_phase3")
        for coordinator, obj in iter_objects(service_call, mustbe=Installation):
            _LOGGER.debug("  >> to %s", obj.id)
            await obj.set_limit_current(
                availableCurrent=available_current,
                availableCurrentPhase1=available_current_phase1,
                availableCurrentPhase2=available_current_phase2,
                availableCurrentPhase3=available_current_phase3,
            )
            await coordinator.async_request_refresh()

    # LIST OF SERVICES
    services: list[tuple[str, vol.Schema, TServiceHandler]] = [
        ("stop_charging", CHARGER_ID_SCHEMA, service_handle_stop_charging),
        ("resume_charging", CHARGER_ID_SCHEMA, service_handle_resume_charging),
        ("authorize_charging", CHARGER_ID_SCHEMA, service_handle_authorize_charging),
        (
            "deauthorize_charging",
            CHARGER_ID_SCHEMA,
            service_handle_deauthorize_charging,
        ),
        ("restart_charger", CHARGER_ID_SCHEMA, service_handle_restart_charger),
        ("upgrade_firmware", CHARGER_ID_SCHEMA, service_handle_upgrade_firmware),
        ("limit_current", LIMIT_CURRENT_SCHEMA, service_handle_limit_current),
    ]

    # Register the services
    for name, schema, handler in services:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload zaptec services."""
    _LOGGER.debug("Unload services")
    for service in hass.services.async_services().get(DOMAIN, {}):
        hass.services.async_remove(DOMAIN, service)
