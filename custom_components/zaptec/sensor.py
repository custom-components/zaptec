import asyncio
import logging
from datetime import timedelta

import aiohttp
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.dispatcher import (async_dispatcher_connect,
                                              async_dispatcher_send)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import SENSOR_SCHEMA_ATTRS
from .const import *
from .misc import to_under

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(SENSOR_SCHEMA_ATTRS)


SCAN_INTERVAL = timedelta(seconds=60)


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

    async def cb(data):
        """Callback thats executed when a new message from the message bus is in."""
        acc.update(data)
        # Tell the sensor that htere is a update.
        async_dispatcher_send(hass, EVENT_NEW_DATA)


    for ins in acc.installs:
        #_LOGGER.debug("Building install %s", ins._attrs)
        await ins.stream(cb=cb)
        #_LOGGER.debug("%s", vars(ins))
        for circuit in ins.circuits:
            #_LOGGER.debug("Building circuit %s", circuit)
            c = CircuteSensor(circuit)
            sensors.append(c)
            for charger in circuit.chargers:
                #_LOGGER.debug("Building charger %s", charger)
                # Force a update before its added.
                await charger.state()
                chs = ChargerSensor(charger, hass)
                sensors.append(chs)
        sensors.append(InstallationSensor(ins))

    _LOGGER.debug(sensors)


    async_add_entities(sensors, False)

    return True


class ZapMixin():
    async def _real_update(self):
        _LOGGER.debug("Called _real_update")
        # The api already updated and have new data available.
        self.async_write_ha_state()

class CircuteSensor(Entity):
    def __init__(self, circuit):
        self._api = circuit
        self._attrs = circuit._attrs

    @property
    def name(self) -> str:
        return 'zaptec_circute_%s' % self._api._attrs["id"]

    @property
    def device_state_attributes(self) -> dict:
        return self._attrs

    @property
    def state(self):
        return self._attrs["active"]

    # add pull method


class InstallationSensor(Entity):
    def __init__(self, api):
        self._api = api
        self._attrs = api._attrs

    @property
    def name(self) -> str:
        return 'zaptec_installation_%s' % self._attrs["id"]

    @property
    def device_state_attributes(self) -> dict:
        return self._attrs

    @property
    def state(self):
        return self._attrs["active"]

    @property
    def should_pull(self):
        return True

    async def async_update(self) -> None:
        """Update the attributes"""
        _LOGGER.debug("Called async_update on InstallationSensor")
        await self._api._account.map[self.id].state()


class ChargerSensor(Entity, ZapMixin):
    def __init__(self, api, hass) -> None:
        self._api = api
        self._hass = hass
        self._attrs = api._attrs

    @property
    def should_pull(self):
        return False

    @property
    def name(self) -> str:
        return 'zaptec_%s' % self._api.mid

    @property
    def icon(self) -> str:
        return 'mdi:ev-station'

    @property
    def entity_picture(self) -> str:
        return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][1]

    @property
    def state(self) -> str:
        try:
            return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][0]
        except KeyError:
            # This seems to happen when it starts up.
            return "unknown"

    @property
    def device_state_attributes(self) -> dict:
        return self._attrs

    #async def async_update(self) -> None:
    #    """Update the attributes"""
    #    #_LOGGER.debug("Called async_update on ChargerSensor")
    #    return
    #    #if not OBSERVATIONS_REMAPS:
    #    #    await _update_remaps()
    #    await self._api.state()



    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        await super().async_added_to_hass()
        _LOGGER.debug("called async_added_to_hass %s", self.name)
        async_dispatcher_connect(self._hass, EVENT_NEW_DATA, self._real_update)
