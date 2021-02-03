import logging

import aiohttp
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import SENSOR_SCHEMA_ATTRS
from .const import *

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(SENSOR_SCHEMA_ATTRS)


def to_under(word) -> str:
    """helper to convert TurnOnThisButton to turn_on_this_button."""
    result = ''
    for i, char in enumerate(word):
        if char.isupper():
            if i != 0:
                result += '_%s' % char.lower()
            else:
                result += char.lower()
        else:
            result += char.lower()

    return result


async def _update_remaps() -> None:
    wanted = ['Observations']
    async with aiohttp.request('GET', CONST_URL) as resp:
        if resp.status == 200:
            data = await resp.json()
            for k, v in data.items():
                if k in wanted:
                    OBSERVATIONS_REMAPS.update(v)
                    # Add names.
                    OBSERVATIONS_REMAPS.update({value: key for key, value in v.items()})

async def async_setup_platform(hass: HomeAssistantType,
                               config: ConfigType,
                               async_add_entities,
                               discovery_info=None) -> None:
    if not config:
        # This means there is no info from the sensor yaml or configuration yaml.
        # We support config under the component this is added as discovery info.
        if discovery_info:
            config = discovery_info.copy()

    if not config:
        _LOGGER.debug('Missing config, stopped setting platform')
        return

    global WANTED_ATTRIBUTES
    # Should we pass the wanted attrs to sensors directly?
    WANTED_ATTRIBUTES = config.get('wanted_attributes', [])
    # Make sure 710 is there since it's state we track.
    if 710 not in WANTED_ATTRIBUTES:
        _LOGGER.debug('Attribute 710 was missing from wanted_attributes'
                      'this was automatically added')
        WANTED_ATTRIBUTES.append(710)

    sensors = []
    acc = hass.data[DOMAIN]['api']
    devs = await acc.chargers()

    for dev in devs:
        sensors.append(ChargerSensor(dev))

    async_add_entities(sensors, True)

    return True


class ChargerSensor(Entity):
    def __init__(self, api) -> None:
        self._api = api
        self._attrs = api._attrs.copy()

    @property
    def name(self) -> str:
        return 'zaptec_%s' % self._api._mid

    @property
    def icon(self) -> str:
        return 'mdi:ev-station'

    @property
    def entity_picture(self) -> str:
        return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][1]

    @property
    def state(self) -> str:
        return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][0]

    @property
    def device_state_attributes(self) -> dict:
        return self._attrs

    async def async_update(self) -> None:
        """Update the attributes"""
        if not OBSERVATIONS_REMAPS:
            await _update_remaps()
        data = await self._api.account._request('chargers/%s/state' % self._api._id)
        for row in data:
            # Make sure we only get
            # the attributes we are interested in.
            # use the const_url to find all the possible
            # attributes under observers
            if row['StateId'] in WANTED_ATTRIBUTES:
                try:
                    name = to_under(OBSERVATIONS_REMAPS[row['StateId']])
                    self._attrs[name] = row.get('ValueAsString', 0)
                except KeyError:
                    _LOGGER.debug('%s is not int %r' % (row, OBSERVATIONS_REMAPS))
