import asyncio
import inspect
import logging

import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


has_id_schema = vol.Schema({vol.Required("charger_id"): str})

has_inst_schema_1 = vol.Schema({vol.Required("installation_id"): str, vol.Required("available_current"): int})
has_inst_schema_3 = vol.Schema({vol.Required("installation_id"): str, vol.Required("available_current_phase1"): int, vol.Required("available_current_phase2"): int, vol.Required("available_current_phase3"): int})


async def async_setup_services(hass):
    """Set up services for the Plex component."""

    acc = hass.data[DOMAIN]["api"]
    _LOGGER.debug("Setting up services.")

    # just the new one for now.
    # #  require firmware > 3.2
    async def service_handle_stop_pause(service_call):
        _LOGGER.debug("called new stop pause")
        charger_id = service_call.data["charger_id"]
        return await acc.map[charger_id].stop_pause()

    async def service_handle_resume_charging(service_call):
        _LOGGER.debug("service new start and or resume")
        charger_id = service_call.data["charger_id"]
        return await acc.map[charger_id].resume_charging()

    # Add old one to see if they even work.
    async def service_handle_start_charging(service_call):
        _LOGGER.debug("service old start")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/501"
        return await acc._request(cmd, method="post")

    async def service_handle_stop_charging(service_call):
        _LOGGER.debug("service old stop")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/502"
        return await acc._request(cmd, method="post")

    async def service_handle_restart_charger(service_call):
        _LOGGER.debug("service restart_charger")
        charger_id = service_call.data["charger_id"]
        return await acc.map[charger_id].restart_charger()

    async def service_handle_update_firmware(service_call):
        _LOGGER.debug("service update_firmware")
        charger_id = service_call.data["charger_id"]
        return await acc.map[charger_id].update_firmware()


    async def service_handle_update_installation_1(service_call):
        _LOGGER.debug("update current single phase")
        installation_id = service_call.data["installation_id"]
        available_current = service_call.data["available_current"]
        #how to send available_current
        return await acc.map[installation_id].update_installation_1()

    async def service_handle_update_installation_3(service_call):
        _LOGGER.debug("update current single phase")
        installation_id = service_call.data["installation_id"]
        available_current_phase1 = service_call.data["available_current_phase1"]
        available_current_phase2 = service_call.data["available_current_phase2"]
        available_current_phase3 = service_call.data["available_current_phase3"]
        #how to send available_current_phase1/2/3?
        return await acc.map[installation_id].update_installation_3()

    hass.services.async_register(
        DOMAIN, "stop_pause_charging", service_handle_stop_pause, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "resume_charging", service_handle_resume_charging, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "start_charging", service_handle_start_charging, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "stop_charging", service_handle_stop_charging, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "restart_charger", service_handle_restart_charger, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "update_firmware", service_handle_update_firmware, schema=has_id_schema
    )

    hass.services.async_register(
        DOMAIN, "update_installation_1", service_handle_update_installation_1, schema=has_inst_schema_1
    )

    hass.services.async_register(
        DOMAIN, "update_installation_3", service_handle_update_installation_3, schema=has_inst_schema_3
    )
