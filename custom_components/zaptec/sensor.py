import asyncio
import logging
from datetime import timedelta

import aiohttp
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.dispatcher import (async_dispatcher_connect,
                                              async_dispatcher_send)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from .const import *
from .misc import to_under

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=60)


async def _update_remaps() -> None:
    wanted = ["Observations"]
    async with aiohttp.request("GET", CONST_URL) as resp:
        if resp.status == 200:
            data = await resp.json()
            for k, v in data.items():
                if k in wanted:
                    OBSERVATIONS_REMAPS.update(v)
                    # Add names.
                    OBSERVATIONS_REMAPS.update({value: key for key, value in v.items()})


async def _dry_setup(hass, config, async_add_entities, discovery_info=None):
    sensors = []
    acc = hass.data[DOMAIN]["api"]

    async def cb(data):
        """Callback thats executed when a new message from the message bus is in."""
        acc.update(data)
        # Tell the sensor that htere is a update.
        async_dispatcher_send(hass, EVENT_NEW_DATA)

    for ins in acc.installs:
        # _LOGGER.debug("Building install %s", ins._attrs)
        await ins.stream(cb=cb)
        # _LOGGER.debug("%s", vars(ins))
        for circuit in ins.circuits:
            # _LOGGER.debug("Building circuit %s", circuit)
            c = CircuitSensor(circuit)
            sensors.append(c)
            for charger in circuit.chargers:
                # _LOGGER.debug("Building charger %s", charger)
                # Force a update before its added.
                await charger.state()
                chs = ChargerSensor(charger, hass)
                sensors.append(chs)
        sensors.append(InstallationSensor(ins))

    for charger in acc.stand_alone_chargers:
        # _LOGGER.debug("Building charger %s", charger)
        # Force an update before its added.
        await charger.state()
        chs = ChargerSensor(charger, hass)
        sensors.append(chs)


    async_add_entities(sensors, False)

    return True


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
) -> None:
    return True


async def async_setup_entry(hass, config_entry, async_add_devices):
    return await _dry_setup(hass, config_entry.data, async_add_devices)


class ZapMixin:
    async def _real_update(self):
        _LOGGER.debug("Called _real_update")
        # The api already updated and have new data available.
        self.async_write_ha_state()


class CircuitSensor(Entity):
    def __init__(self, circuit):
        self._api = circuit
        self._attrs = circuit._attrs

    @property
    def name(self) -> str:
        return "zaptec_circuit_%s" % self._api._attrs["id"]

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    @property
    def unique_id(self):
        return f"zaptec_{self._attrs['id']}".lower()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "manufacturer": DOMAIN,
        }

    @property
    def state(self):
        return self._attrs["active"]

    async def async_update(self) -> None:
        """Update the attributes"""
        _LOGGER.debug("Called async_update on InstallationSensor")
        await self._api._account.map[self._attrs["id"]].state()


class InstallationSensor(Entity):
    def __init__(self, api):
        self._api = api
        self._attrs = api._attrs

    @property
    def name(self) -> str:
        return "zaptec_installation_%s" % self._attrs["id"]

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    @property
    def unique_id(self):
        return f"zaptec_{self._attrs['id']}".lower()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "manufacturer": DOMAIN,
        }

    @property
    def state(self):
        return self._attrs["active"]

    @property
    def should_pull(self):
        return True

    async def async_update(self) -> None:
        """Update the attributes"""
        _LOGGER.debug("Called async_update on InstallationSensor")
        await self._api._account.map[self._attrs["id"]].state()


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
        return f"zaptec_charger_{self._api.mid}".lower()

    @property
    def icon(self) -> str:
        return "mdi:ev-station"

    @property
    def entity_picture(self) -> str:
        return CHARGE_MODE_MAP[self._attrs["charger_operation_mode"]][1]

    @property
    def state(self) -> str:
        try:
            return CHARGE_MODE_MAP[self._attrs["charger_operation_mode"]][0]
        except KeyError:
            # This seems to happen when it starts up.
            return "unknown"

    @property
    def unique_id(self):
        return f"zaptec_{self._attrs['id']}".lower()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "manufacturer": DOMAIN,
        }

    @property
    def extra_state_attributes(self) -> dict:
        return self._attrs

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        await super().async_added_to_hass()
        _LOGGER.debug("called async_added_to_hass %s", self.name)
        async_dispatcher_connect(self._hass, EVENT_NEW_DATA, self._real_update)
