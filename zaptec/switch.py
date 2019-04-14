"""Switch platform for blueprint."""
import logging

from homeassistant.components.switch import SwitchDevice
from .const import CHARGE_MODE_MAP
from . import DOMAIN, SWITCH_SCHEMA_ATTRS

_LOGGER = logging.getLogger(__name__)

# Are there some other shit we are supposed to use instead?
#PLATFORM_SCHEMA.extend(SWITCH_SCHEMA_ATTRS)

#https://github.com/custom-components/blueprint/blob/master/custom_components/blueprint/switch.py

async def async_setup_platform(
    hass, config, async_add_entities, discovery_info=None
):  # pylint: disable=unused-argument
    """Setup switch platform."""
    api = hass.data[DOMAIN]['api']

    sensors = []
    chargers = await api.chargers()
    _LOGGER.info('Adding zaptecswitch')

    for c in chargers:
        sensors.append(Switch(c))

    async_add_entities(sensors, False)


class Switch(SwitchDevice):
    """switch class."""

    def __init__(self, api):
        self._api = api
        self._attr = {}
        self._status = False
        # wft is this supposed to be?
        self._name = 'zaptec_%s_switch' % api._id
        self._mode = ''

    async def async_update(self):
        """Update the switch."""
        _LOGGER.info('called async update')

        data = await self._api.state()
        for row in data:
            if row['StateId'] == 710:
                self._mode = CHARGE_MODE_MAP[row['ValueAsString']][0]

        _LOGGER.info('mode is %s', self._mode)

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        self._status = True
        return await self._api.start_charging()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        self._status = False
        return await self._api.stop_charging()

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def icon(self):
        """Return the icon of this switch."""
        return ''

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return True if self._mode == 'charging' else False

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return self._attr
