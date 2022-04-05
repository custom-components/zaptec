"""Switch platform for blueprint."""
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import SWITCH_SCHEMA_ATTRS
from .const import CHARGE_MODE_MAP, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Are there some other shit we are supposed to use instead?
# PLATFORM_SCHEMA.extend(SWITCH_SCHEMA_ATTRS)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
) -> bool:  # pylint: disable=unused-argument
    """Setup switch platform."""
    acc = hass.data[DOMAIN]["api"]
    if acc is None:
        _LOGGER.debug("Didn't setup switch the api wasnt ready")
        return False

    switches = []
    chargers = acc.all_chargers

    for c in chargers:
        switches.append(Switch(c))

    async_add_entities(switches, False)
    return True


class Switch(SwitchEntity):
    """switch class."""

    def __init__(self, api) -> None:
        self._api = api
        self._attr = {}
        self._status = False
        # wft is this supposed to be?
        self._name = "zaptec_%s_switch" % api._id
        self._mode = ""

    async def async_update(self) -> None:
        """Update the switch."""
        data = await self._api.state()
        for row in data:
            if row["StateId"] == 710:
                self._mode = CHARGE_MODE_MAP[row["ValueAsString"]][0]

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        self._status = True
        return await self._api.start_charging()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        self._status = False
        return await self._api.stop_charging()

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return self._name

    @property
    def icon(self) -> str:
        """Return the icon of this switch."""
        return ""  # <-- what should this be?

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return True if self._mode == "charging" else False

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return self._attr
