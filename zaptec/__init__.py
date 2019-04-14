"""Support for zaptec."""
import logging

import voluptuous as vol
from homeassistant.helpers import discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME
)

from . import api
from .const import (
    STARTUP,
    CONF_ENABLED,
    CONF_NAME,
    CONF_SENSOR,
    CONF_SWITCH
)


_LOGGER = logging.getLogger(__name__)
DOMAIN = 'zaptec'

SENSOR_SCHEMA_ATTRS = {
    vol.Optional('wanted_attributes', default=[710]): cv.ensure_list,
    vol.Optional(CONF_ENABLED, default=True): cv.boolean,
}

SENSOR_SCHEMA = vol.Schema(SENSOR_SCHEMA_ATTRS)

# The atts is added solo so we can use both
# ways to setup the sensors/switch.
SWITCH_SCHEMA_ATTRS = {
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
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


async def async_setup(hass, config):
    _LOGGER.info(STARTUP)

    #if DOMAIN not in config:
    #    return

    # Change from has own const..
    username = config[DOMAIN][CONF_USERNAME]
    password = config[DOMAIN][CONF_PASSWORD]

    if not username or not password:
        _LOGGER.debug('Missing username and password')
        # Add persistent notification too?
        return
    _LOGGER.info(dict(config[DOMAIN]))
    # Add the account to a so it can be shared.
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN]['api'] = api.Account(username, password, async_get_clientsession(hass))
    #hass.data[DOMAIN]['chargers'] = []
    #hass.data[DOMAIN]['chargers'] = await api.chargers()


    # This part has not been tested as i have only used
    # the sensor.yaml method for now.
    for platform in ['sensor', 'switch']:
        _LOGGER.info('Checking %s', platform)
        # Get platform specific configuration
        platform_config = config[DOMAIN].get(platform, {})

        # If platform is not enabled, skip.
        if not platform_config:
            _LOGGER.info('%s has no config, skipping it.', platform)
            continue

        for entry in platform_config:
            entry_config = entry
            _LOGGER.critical(entry_config)

            # If entry is not enabled, skip.
            if not entry_config[CONF_ENABLED]:
                _LOGGER.info('%s isnt enabled', platform)
                continue

            hass.async_create_task(
                discovery.async_load_platform(
                    hass, platform, DOMAIN, entry_config, config
                )
            )

    return True
