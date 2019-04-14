"""Support for zaptec."""
import logging

import voluptuous as vol
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from . import api


_LOGGER = logging.getLogger(__name__)
DOMAIN = 'zaptec'



CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,

    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):

    #if DOMAIN not in config:
    #    return

    # Change from has own const..
    username = config[DOMAIN][CONF_USERNAME]
    password = config[DOMAIN][CONF_PASSWORD]

    if not username or not password:
        _LOGGER.debug('Missing username and password')
        # Add persistent notification too?
        return

    # Add the account to a so it can be shared.
    hass.data[DOMAIN] = {}
    hass.data[DOMAIN]['api'] = api.Account(username, password, async_get_clientsession(hass))

    return True
