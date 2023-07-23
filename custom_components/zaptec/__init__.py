"""Support for zaptec."""
import asyncio
import copy
import logging
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (async_dispatcher_connect,
                                              async_dispatcher_send)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import api
from .const import (CONF_ENABLED, CONF_NAME, CONF_SENSOR, CONF_SWITCH, DOMAIN,
                    EVENT_NEW_DATA, EVENT_NEW_DATA_HOURLY, PLATFORMS, STARTUP)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)


async def _dry_setup(hass, config):
    _LOGGER.info(STARTUP)
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]

    if not username or not password:
        _LOGGER.debug("Missing username and password")
        # Add persistent notification too?
        return

    # Add the account to a so it can be shared.
    # between the sensor and the switch.
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
        acc = api.Account(username, password, async_get_clientsession(hass))
        hass.data[DOMAIN]["api"] = acc

        if acc.is_built is False:
            await acc.build()

        async def push_update_to_charger(t):
            _LOGGER.debug("hourly_update %s", t)
            # this does not exist.
            for ins in acc.installs:
                for circuits in ins.circuits:
                    for charger in circuits.chargers:
                        await charger.state()

            for charger in acc.stand_alone_chargers:
                await charger.state()


            async_dispatcher_send(hass, EVENT_NEW_DATA)

        async_track_time_interval(hass, push_update_to_charger, timedelta(seconds=60))
        await async_setup_services(hass)


async def async_setup(hass: HomeAssistantType, config: ConfigType) -> bool:
    if DOMAIN in hass.data:
        _LOGGER.info("Delete zaptec from your yaml")

    return True


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry) -> bool:
    """Set up zaptec as config entry."""
    await _dry_setup(hass, entry.data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistantType, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        acc = hass.data[DOMAIN]["api"]
        # no need to unload stand-alone chargers
        await asyncio.gather(*[i.cancel_stream() for i in acc.installs])
        hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistantType, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
