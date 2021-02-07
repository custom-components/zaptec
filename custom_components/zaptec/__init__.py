"""Support for zaptec."""
import logging
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (async_dispatcher_connect,
                                              async_dispatcher_send)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import api
from .const import (CONF_ENABLED, CONF_NAME, CONF_SENSOR, CONF_SWITCH, DOMAIN,
                    EVENT_NEW_DATA, EVENT_NEW_DATA_HOURLY, STARTUP)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)


SENSOR_SCHEMA_ATTRS = {
    vol.Optional('wanted_attributes', default=[710]): cv.ensure_list,
}

SENSOR_SCHEMA = vol.Schema(SENSOR_SCHEMA_ATTRS)

# The atts is added solo so we can use both
# ways to setup the sensors/switch.
SWITCH_SCHEMA_ATTRS = {
        vol.Optional(CONF_NAME, default='zomg_kek'): cv.string,
}

SWITCH_SCHEMA = vol.Schema(SWITCH_SCHEMA_ATTRS)


CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SENSOR): vol.All(cv.ensure_list, [SENSOR_SCHEMA]),
        vol.Optional(CONF_SWITCH): vol.All(cv.ensure_list, [SWITCH_SCHEMA]),

    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistantType,
                      config: ConfigType,) -> bool:

    _LOGGER.info(STARTUP)
    username = config[DOMAIN][CONF_USERNAME]
    password = config[DOMAIN][CONF_PASSWORD]

    if not username or not password:
        _LOGGER.debug('Missing username and password')
        # Add persistent notification too?
        return

    # Add the account to a so it can be shared.
    # between the sensor and the switch.

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
        acc = api.Account(username, password, async_get_clientsession(hass))
        hass.data[DOMAIN]['api'] = acc
    else:
        acc = hass.data[DOMAIN]["api"]

    if acc.is_built is False:
        await acc.build()


    async def push_update_to_charger(t):
        _LOGGER.debug("hourly_update %s", t)
        # this does not exist.
        for ins in acc.installs:
            for circuits in ins.circuits:
                for charger in circuits.chargers:
                    await charger.state()
        async_dispatcher_send(hass, EVENT_NEW_DATA)

    async_track_time_interval(hass, push_update_to_charger, timedelta(seconds=120))

    for platform in ['sensor']:
    #for platform in ['sensor', 'switch']:
        _LOGGER.debug('Checking %s', platform)
        # Get platform specific configuration
        platform_config = config[DOMAIN].get(platform, {})

        # If platform is not enabled, skip.
        if not platform_config:
            _LOGGER.debug('%s has no config, skipping it.', platform)
            continue

        for entry in platform_config:
            entry_config = entry

            hass.async_create_task(
                discovery.async_load_platform(
                    hass, platform, DOMAIN, entry_config, config
                )
            )

    await async_setup_services(hass)

    return True
