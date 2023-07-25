import asyncio
import logging
import os

import voluptuous as vol

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

has_redacted_schema = vol.Schema({vol.Optional("redacted", default=True): bool})


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

    async def service_handle_limit_current(service_call):
        _LOGGER.debug("update current limit")
        installation_id = service_call.data["installation_id"]
        available_current = service_call.data.get("available_current")
        available_current_phase1 = service_call.data.get("available_current_phase1")
        available_current_phase2 = service_call.data.get("available_current_phase2")
        available_current_phase3 = service_call.data.get("available_current_phase3")
        return await acc.map[installation_id].limit_current(
            availableCurrent=available_current,
            availableCurrentPhase1=available_current_phase1,
            availableCurrentPhase2=available_current_phase2,
            availableCurrentPhase3=available_current_phase3,
        )

    async def service_handle_debug_data_dump(service_call):
        _LOGGER.debug("debug data dump")
        redacted = service_call.data["redacted"]
        path = os.path.join(hass.config.config_dir, 'www', 'zaptec')
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, 'api_data.txt'), 'w') as f:
            async for text in acc.data_dump(redacted=redacted):
                f.write(text)
        _LOGGER.warning("Dumped Zaptec debug info. Restart HA and download from <URL>/local/zaptec/api_data.txt")

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
        DOMAIN, "limit_current", service_handle_limit_current, schema=has_limit_current_schema
    )

    hass.services.async_register(
        DOMAIN, "debug_data_dump", service_handle_debug_data_dump, schema=has_redacted_schema
    )
