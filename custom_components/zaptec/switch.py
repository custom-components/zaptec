"""Switch platform for blueprint."""
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

# from . import SWITCH_SCHEMA_ATTRS
from .const import CHARGE_MODE_MAP, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Are there some other shit we are supposed to use instead?
# PLATFORM_SCHEMA.extend(SWITCH_SCHEMA_ATTRS)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
) -> bool:  # pylint: disable=unused-argument
    """Setup switch platform."""
    return True


async def async_setup_entry(hass, config_entry, async_add_devices):
    """ "Setup the switch using ui."""
    _LOGGER.debug("Setup switch for zaptec")
    acc = hass.data.get(DOMAIN, {}).get("api")
    if acc is None:
        _LOGGER.debug("Didn't setup switch the api wasnt ready")
        return False

    switches = []
    chargers = [c for c in acc.map.values() if c and c.__class__.__name__ == "Charger"]

    for c in chargers:
        switches.append(Switch(c, hass))

    async_add_devices(switches, False)
    return True


class Switch(SwitchEntity):
    """switch class."""

    def __init__(self, api, hass) -> None:
        self._api = api
        self._status = False
        # wft is this supposed to be?
        self._name = "zaptec_%s_switch" % api.id
        self._mode = ""
        self._hass = hass

    async def async_update(self) -> None:
        """Update the switch."""

        try:
            value = CHARGE_MODE_MAP[self._api._attrs["operating_mode"]][0]
            _LOGGER.info(
                "Trying to update the switch raw value %s %s",
                self._api._attrs["operating_mode"],
                CHARGE_MODE_MAP[self._api._attrs["operating_mode"]][0],
            )
            return value
        except KeyError:
            # This seems to happen when it starts up.
            _LOGGER.debug("Switch value is unknowns")
            return "unknown"

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return await self._api.resume_charging()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return await self._api.stop_pause()

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
        if self._api._attrs.get("operating_mode") in [3]:
            return True
        return False

    # @property
    # def state(self) -> bool:
    #    return self.is_on

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return self._api._attrs
