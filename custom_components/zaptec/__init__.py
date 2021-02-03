"""Support for zaptec."""
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import api
from .const import CONF_ENABLED, CONF_NAME, CONF_SENSOR, CONF_SWITCH, STARTUP
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)
DOMAIN = 'zaptec'

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
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN]['api'] = api.Account(username, password, async_get_clientsession(hass))

    # This part has not been tested as i have only used
    # the sensor.yaml method for now.
    for platform in ['sensor', 'switch']:
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
