import asyncio
from datetime import timedelta

import logging
import async_timeout
import aiohttp

import voluptuous as vol
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.entity import Entity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

OBSERVATIONS_REMAPS = {}
WANTED_ATTRIBUTES = []
CHARGE_MODE_MAP = {'0': ['unknown', 'mdi:help-rhombus-outline'],
                   '1': ['disconnected', 'mdi:power-plug-off'],
                   '2': ['waiting', 'mdi:power-sleep'],
                   '3': ['charging', 'mdi:power-plug'],
                   '5': ['charge_done', 'mdi:battery-charging-100']}

TOKEN_URL = 'https://api.zaptec.com/oauth/token'
API_URL = 'https://api.zaptec.com/api/'
CONST_URL = 'https://api.zaptec.com/api/constants'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional('wanted_attributes', default=[710]): cv.ensure_list
})


def to_under(word):
    """helper to convert TunOnThisButton to turn_on_this_button."""
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


async def _update_remaps():
    wanted = ['Observations']
    async with aiohttp.request('GET', CONST_URL) as resp:
        if resp.status == 200:
            data = await resp.json()
            for k, v in data.items():
                if k in wanted:
                    OBSERVATIONS_REMAPS.update(v)
                    # Add names.
                    OBSERVATIONS_REMAPS.update({value: key for key, value in v.items()})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    global WANTED_ATTRIBUTES
    # Should we pass the wanted attrs to sensors directly?
    username = config.get('username', '')
    password = config.get('password', '')
    WANTED_ATTRIBUTES = config.get('wanted_attributes')
    # Make sure 710 is there since it's state we track.
    if 710 not in WANTED_ATTRIBUTES:
        _LOGGER.debug('Attribute 710 was missing from wanted_attributes'
                      'this was automatically added')
        WANTED_ATTRIBUTES.append(710)

    if not username or not password:
        _LOGGER.debug('Missing username and password')
        return False

    sensors = []
    acc = Account(username, password, async_get_clientsession(hass))
    devs = await acc.chargers()
    hass.data[DOMAIN] = acc

    for dev in devs:
        sensors.append(ChargerSensor(dev))

    async_add_entities(sensors, True)

    return True


class Account:
    def __init__(self, username, password, client):
        self._username = username
        self._password = password
        self._client = client
        self._token_info = {}
        self._access_token = None

    async def _refresh_token(self):
        # So for some reason they used grant_type password..
        # what the point with oauth then? Anyway this is valid for 24 hour
        p = {'username': self._username,
             'password': self._password,
             'grant_type': 'password'}
        async with aiohttp.request('POST',
                                   TOKEN_URL,
                                   data=p
                                   ) as resp:

            if resp.status == 200:
                data = await resp.json()
                # The data includes the time the access token expires
                # atm we just ignore it and refresh token when needed.
                self._token_info.update(data)
                self._access_token = data.get('access_token')
            else:
                _LOGGER.debug('Failed to refresh token, check your credentials.')

    async def _request(self, url):
        header = {'Authorization': 'Bearer %s' % self._access_token,
                  'Accept': 'application/json'}
        full_url = API_URL + url
        try:
            with async_timeout.timeout(10):
                async with self._client.get(full_url, headers=header) as resp:
                    if resp.status == 401:
                        await self._refresh_token()
                        return await self._request(url)
                    else:
                        return await resp.json()
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Could not get info from %s: %s", full_url, err)

    async def chargers(self):
        charg = await self._request('chargers')

        return [Charger(chrg, self)
                for chrg in charg.get('Data', [])
                if chrg]


class Charger:
    def __init__(self, data, account):
        self._id = data.get('Id')
        self._mid = data.get('MID')
        self._device_id = data.get('DeviceId')
        self._name = data.get('SerialNo')
        self._created_on_date = data.get('CreatedOnDate')
        self._circuit_id = data.get('CircuitId')
        self._active = data.get('Active')
        self._current_user_roles = data.get('CurrentUserRoles')
        self._pin = data.get('Pin')
        self.account = account
        self._attrs = {}

    # Add the commands i found but i assume some
    # of them need some kind if input.
    # untested..

    async def restart_charger(self):
        return await self._send_command(102)

    async def restart_mcu(self):
        return await self._send_command(103)

    async def update_settings(self):
        return await self._send_command(104)

    async def restart_ntp(self):
        return await self._send_command(105)

    async def exit_app_with_code(self):
        return await self._send_command(106)

    async def upgrade_firmware(self):
        return await self._send_command(200)

    async def upgrade_firmware_forced(self):
        return await self._send_command(201)

    async def reset_com_errors(self):
        return await self._send_command(260)

    async def reset_notifications(self):
        return await self._send_command(261)

    async def reset_com_warnings(self):
        return await self._send_command(262)

    async def local_settings(self):
        return await self._send_command(260)

    async def set_plc_npw(self):
        return await self._send_command(320)

    async def set_plc_cocode(self):
        return await self._send_command(321)

    async def set_plc_nmk(self):
        return await self._send_command(322)

    async def set_remote_plc_nmk(self):
        return await self._send_command(323)

    async def set_remote_plc_npw(self):
        return await self._send_command(324)

    async def start_charging(self):
        return await self._send_command(501)

    async def stop_charging(self):
        return await self._send_command(502)

    async def report_charging_state(self):
        return await self._send_command(503)

    async def set_session_id(self):
        return await self._send_command(504)

    async def set_user_uuid(self):
        return await self._send_command(505)

    async def show_granted(self):
        return await self._send_command(601)

    async def show_denied(self):
        return await self._send_command(602)

    async def indicate_app_connect(self):
        return await self._send_command(603)

    async def confirm_charge_card_added(self):
        return await self._send_command(750)

    async def set_authentication_list(self):
        return await self._send_command(751)

    async def debug(self):
        return await self._send_command(800)

    async def get_plc_topology(self):
        return await self._send_command(801)

    async def reset_plc(self):
        return await self._send_command(802)

    async def remote_command(self):
        return await self._send_command(803)

    async def run_grid_test(self):
        return await self._send_command(804)

    async def run_post_production_test(self):
        return await self._send_command(901)

    async def combined_min(self):
        return await self._send_command(10000)

    async def deauthorize_stop(self):
        return await self._send_command(10001)

    async def combined_max(self):
        return await self._send_command(10999)

    async def _send_command(self, id_):
        await self.account._request('chargers/%s/SendCommand/%s' % (self._id, id_))


class ChargerSensor(Entity):
    def __init__(self, api):
        self._api = api
        self._attrs = api._attrs.copy()

    @property
    def name(self):
        return 'zaptec_%s' % self._api._mid

    @property
    def icon(self):
        return 'mdi:ev-station'

    @property
    def entity_picture(self):
        return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][1]

    @property
    def state(self):
        return CHARGE_MODE_MAP[self._attrs['charger_operation_mode']][0]

    @property
    def device_state_attributes(self):
        return self._attrs

    async def async_update(self):
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
