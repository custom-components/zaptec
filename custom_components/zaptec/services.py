"""Zaptec components services."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall

from .api import Account, Charger, Installation
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TServiceHandler = Callable[[ServiceCall], Awaitable[None]]

# SCHEMAS for services
# ====================
has_id_schema = vol.Schema({vol.Required("charger_id"): str})

has_limit_current_schema = vol.Schema(vol.SomeOf(
    min_valid=1, max_valid=1, msg="Must specify either only available_current or all "
    "three available_current_phaseX (where X is 1-3). They are mutually exclusive",
    validators=[
    {
        vol.Required("installation_id"): str,
        vol.Required("available_current"): int,
    },
    {
        vol.Required("installation_id"): str,
        vol.Required("available_current_phase1"): int,
        vol.Required("available_current_phase2"): int,
        vol.Required("available_current_phase3"): int,
    },
]))


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the Plex component."""

    _LOGGER.debug("Set up services")
    acc: Account = hass.data[DOMAIN]["api"]

    async def service_handle_stop_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called stop charging")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.stop_charging_final()

    async def service_handle_resume_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called resume charging")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.resume_charging()

    async def service_handle_authorize_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called authorize charging")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.authorize_charge()

    async def service_handle_deauthorize_charging(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called deauthorize charging and stop")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.deauthorize_and_stop()

    async def service_handle_restart_charger(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called restart charger")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.restart_charger()

    async def service_handle_update_firmware(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called update firmware")
        charger_id = service_call.data["charger_id"]
        charger: Charger = acc.map[charger_id]
        await charger.upgrade_firmware()

    async def service_handle_limit_current(service_call: ServiceCall) -> None:
        _LOGGER.debug("Called set current limit")
        installation_id = service_call.data["installation_id"]
        available_current = service_call.data.get("available_current")
        available_current_phase1 = service_call.data.get("available_current_phase1")
        available_current_phase2 = service_call.data.get("available_current_phase2")
        available_current_phase3 = service_call.data.get("available_current_phase3")
        installation: Installation = acc.map[installation_id]
        await installation.set_limit_current(
            availableCurrent=available_current,
            availableCurrentPhase1=available_current_phase1,
            availableCurrentPhase2=available_current_phase2,
            availableCurrentPhase3=available_current_phase3,
        )

    # LIST OF SERVICES
    services: list[tuple[str, vol.Schema, TServiceHandler]] = [
        ("stop_charging",        has_id_schema, service_handle_stop_charging),
        ("resume_charging",      has_id_schema, service_handle_resume_charging),
        ("authorize_charging",   has_id_schema, service_handle_authorize_charging),
        ("deauthorize_charging", has_id_schema, service_handle_deauthorize_charging),
        ("restart_charger",      has_id_schema, service_handle_restart_charger),
        ("update_firmware",      has_id_schema, service_handle_update_firmware),
        ("limit_current",        has_limit_current_schema, service_handle_limit_current),
    ]

    # Register the services
    for name, schema, handler in services:
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
