import asyncio
import json
import logging

import aiohttp
import async_timeout

from .const import API_URL, TOKEN_URL

_LOGGER = logging.getLogger(__name__)


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
        p = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
        }
        async with aiohttp.request("POST", TOKEN_URL, data=p) as resp:

            if resp.status == 200:
                data = await resp.json()
                # The data includes the time the access token expires
                # atm we just ignore it and refresh token when needed.
                self._token_info.update(data)
                self._access_token = data.get("access_token")
            else:
                _LOGGER.debug("Failed to refresh token, check your credentials.")

    async def _request(self, url, method="get"):
        header = {
            "Authorization": "Bearer %s" % self._access_token,
            "Accept": "application/json",
        }
        full_url = API_URL + url
        _LOGGER.debug("calling %s", full_url)
        try:
            with async_timeout.timeout(30):
                call = getattr(self._client, method)
                async with call(full_url, headers=header) as resp:
                    if resp.status == 401:
                        await self._refresh_token()
                        return await self._request(url)
                    elif resp.status == 204:
                        # Seems to return this on commands.. like method post
                        content = await resp.read()
                        _LOGGER.debug("content %s", content)
                        return content
                    else:
                        json_result = await resp.json()
                        _LOGGER.debug(json.dumps(json_result, indent=4))
                        return json_result

        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Could not get info from %s: %s", full_url, err)

    async def chargers(self):
        charg = await self._request("chargers")
        return [Charger(chrg, self) for chrg in charg.get("Data", []) if chrg]


class Charger:
    def __init__(self, data, account):
        self._id = data.get("Id")
        self._mid = data.get("MID")
        self._device_id = data.get("DeviceId")
        self._name = data.get("SerialNo")
        self._created_on_date = data.get("CreatedOnDate")
        self._circuit_id = data.get("CircuitId")
        self._active = data.get("Active")
        self._current_user_roles = data.get("CurrentUserRoles")
        self._pin = data.get("Pin")
        self.account = account
        self._attrs = {}

    # All methods need to be checked again.
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

    #  require firmware > 3.2
    async def stop_pause(self):
        return await self._send_command(506)

    #  require firmware > 3.2
    async def resume_charging(self):
        return await self._send_command(507)

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

    async def state(self):
        return await self.account._request("chargers/%s/state" % self._id)

    async def _send_command(self, id_):
        cmd = "chargers/%s/SendCommand/%s" % (self._id, id_)
        return await self.account._request(cmd, method="post")
