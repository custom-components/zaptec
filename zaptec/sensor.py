from datetime import timedelta

import logging
import async_timeout
import aiohttp

import voluptuous as vol
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle
from homeassistant.helpers.entity import Entity

from . import DOMAIN

observations_remaps = {}

_LOGGER = logging.getLogger(__name__)

# This should probable a config option,
# Just grab what we need for now..
to_remap = [-2, 201, 202, 270, 501, 507, 513, 553, 708, 710, 804, 809, 911]

charge_mode_map = {1: 'disconnected',
                   2: 'waiting',
                   3: 'charging',
                   4: 'charge_done'}

token_url = 'https://api.zaptec.com/oauth/token'
api_url = 'https://api.zaptec.com/api/'
const_url = 'https://api.zaptec.com/api/constants'


# Add some platform config validation.

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
    async with aiohttp.request('GET', const_url) as resp:
        if resp.status == 200:
            data = await resp.json()
            for k, v in data.items():
                if k in wanted:
                    observations_remaps.update(v)
                    # Add names.
                    observations_remaps.update({value: key for key, value in v.items()})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    username = config.get('username', '')
    password = config.get('password', '')

    if not username or not password:
        _LOGGER.debug('Missing username and password')
        return False

    acc = Account(username, password, async_get_clientsession(hass))

    devs = await acc.chargers()

    async_add_entities(devs)
    return True


class Account(Entity):
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
                                   token_url,
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

        with async_timeout.timeout(10):
            _LOGGER.debug('full url is %s' % api_url + url)

            async with self._client.get(api_url + url, headers=header) as resp:
                if resp.status == 401:
                    await self._refresh_token()
                    return await self._request(url)
                else:
                    return await resp.json()

    async def chargers(self):
        charg = await self._request('chargers')
        sensors = []

        for chrg in charg['Data']:
            c = Charger(chrg, self)
            sensors.append(c)

        for s in sensors:
            await s.async_update()
        return sensors


class Charger(Entity):
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

    @property
    def name(self):
        # Should this be the id/mid in addition.
        # What if a user has more then one charger?
        # TODO
        return 'zaptec_%s' % self._mid

    @property
    def icon(self):
        return 'mdi:ev-station'

    @property
    def state(self):
        # State seems to logged in some graph
        # Why check if the charger is online, wouldn't
        # the mode be more interesting? charging, waiting etc.
        return charge_mode_map[self.attrs['charger_operation_mode']]
       
    @property
    def device_state_attributes(self):
        return self._attrs
    
    async def _send_command(self, id_):
        """ Supported command Ids are: 102 (restart charger), 502 (stop charging), 200 (upgrade firmware), 10001 (deauthorize and stop charging).""" 
        await self.account._request('chargers/%s/SendCommand/%s' % (self._id, id_))

    async def async_update(self):
        """Update the attributes"""
        if not observations_remaps:
            await _update_remaps()
        data = await self.account._request('chargers/%s/state' % self._id)
        for row in data:
            # Make sure we only get the attributes we
            # are interested in.
            if row['StateId'] in to_remap:
                try:
                    name = to_under(observations_remaps[row['StateId']])
                    self._attrs[name] = row.get('ValueAsString', 0)
                except KeyError:
                    _LOGGER('%s is not int %r' % (row, observations_remaps))


"""
async def test(username, password):
    await _update_remaps()
    x = Account(username, password)
    t = await x.chargers()
    print(t)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test())
"""
