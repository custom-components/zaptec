import asyncio
import inspect
import logging

import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


has_id_schema = vol.Schema({vol.Required("charger_id"): str})


async def async_setup_services(hass):
    """Set up services for the Plex component."""

    acc = hass.data[DOMAIN]["api"]
    _LOGGER.debug("Setting up services.")

    # just the new one for now.
    # #  require firmware > 3.2
    async def service_handle_stop_pause(service_call):
        _LOGGER.debug("called new stop pause")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/506"
        return await acc._request(cmd)

    async def service_handle_resume_charging(service_call):
        _LOGGER.debug("called new start and or resume")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/507"
        return await acc._request(cmd)

    # Add old one to see if they even work.
    async def service_handle_start_charging(service_call):
        _LOGGER.debug("called old start")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/501"
        return await acc._request(cmd)

    async def service_handle_stop_charging(service_call):
        _LOGGER.debug("called old stop")
        charger_id = service_call.data["charger_id"]
        cmd = f"chargers/{charger_id}/SendCommand/502"
        return await acc._request(cmd)

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
