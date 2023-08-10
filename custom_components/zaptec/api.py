"""Main API for Zaptec."""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from concurrent.futures import CancelledError
from functools import partial

import aiohttp
import async_timeout

# pylint: disable=missing-function-docstring

# Type definitions
TValue = str | int | float | bool
TDict = dict[str, TValue]

_LOGGER = logging.getLogger(__name__)

"""
stuff are missing from the api docs compared to what the portal uses.
circuits/{self.id}/live
circuits/{self.id}/
https://api.zaptec.com/api/dashboard/activechargersforowner?limit=250
/dashbord
signalr is used by the website.
"""

# to Support running this as a script.
if __name__ == "__main__":
    from const import (API_RETRIES, API_URL, CONST_URL, FALSY, MISSING,
                       TOKEN_URL, TRUTHY)
    from misc import mc_nbfx_decoder, to_under

    # remove me later
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

else:
    from .const import (API_RETRIES, API_URL, CONST_URL, FALSY, MISSING,
                        TOKEN_URL, TRUTHY)
    from .misc import mc_nbfx_decoder, to_under


class ZaptecApiError(Exception):
    '''Base exception for all Zaptec API errors'''


class AuthorizationError(ZaptecApiError):
    '''Authenatication failed'''


class RequestError(ZaptecApiError):
    '''Failed to get the results from the API'''


class RequestRetryError(ZaptecApiError):
    '''Retries too many times'''


class ZaptecBase(ABC):

    id: str
    name: str
    _account: "Account"
    _attrs: TDict

    def __init__(self, data: TDict, account: "Account") -> None:
        self._account = account
        self._attrs = {}
        self.set_attributes(data)

    def set_attributes(self, data: TDict) -> bool:
        newdata = False
        for k, v in data.items():
            new_key = to_under(k)
            if new_key not in self._attrs:
                _LOGGER.debug(">>>   Adding %s.%s (%s) = %s", self.__class__.__qualname__, new_key, k, v)
                newdata = True
            elif self._attrs[new_key] != v:
                _LOGGER.debug(">>>   Updating %s.%s (%s) = %s  (was %s)", self.__class__.__qualname__, new_key, k, v, self._attrs[new_key])
                newdata = True
            self._attrs[new_key] = v
        return newdata

    def __getattr__(self, key):
        try:
            return self._attrs[to_under(key)]
        except KeyError as exc:
            raise AttributeError(exc) from exc

    @abstractmethod
    async def build(self) -> None:
        """Build the object"""

    @abstractmethod
    async def state(self) -> None:
        """Update the state of the object"""


class Installation(ZaptecBase):
    """Represents an installation"""

    circuits: list[Circuit]

    def __init__(self, data, account):
        super().__init__(data, account)
        self.connection_details = None
        self.circuits = []

        self._stream_task = None
        self._stream_receiver = None

    async def build(self):
        data = await self._account._req_hierarchy(self.id)

        circuits = []
        for item in data["Circuits"]:
            circ = Circuit(item, self._account)
            _LOGGER.debug("    Circuit %s", item["Id"])
            self._account.register(item["Id"], circ)
            await circ.build()
            circuits.append(circ)
        self.circuits = circuits

    async def state(self):
        _LOGGER.debug("Polling state for %s installation (%s)", self.id, self._attrs.get('name'))
        data = await self._account._req_installation(self.id)
        self.set_attributes(data)

    async def limit_current(self, **kwargs):
        """Set a limit now how many amps the installation can use
        Use availableCurrent for setting all phases at once. Use
        availableCurrentPhase* to set each phase individually.
        """
        has_availablecurrent = "availableCurrent" in kwargs
        has_availablecurrentphases = all(
            k in kwargs for k in (
              "availableCurrentPhase1",
              "availableCurrentPhase2",
              "availableCurrentPhase3",
            )
        )

        if not (has_availablecurrent ^ has_availablecurrentphases):
            raise ValueError("Either availableCurrent or all of availableCurrentPhase1, availableCurrentPhase2, availableCurrentPhase3 must be set")

        data = await self._account._request(
            f"installation/{self.id}/update", method="post", data=kwargs
        )
        # FIXME: Verify assumed data structure
        return data

    async def live_stream_connection_details(self):
        data = await self._account._request(
            f"installation/{self.id}/messagingConnectionDetails"
        )
        # FIXME: Verify assumed data structure
        self.connection_details = data
        return data

    async def stream(self, cb=None) -> asyncio.Task:
        """Kickoff the steam in the background."""
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
            # https://github.com/custom-components/zaptec/issues
            return

        await self.cancel_stream()
        self._stream_task = asyncio.create_task(self._stream(cb=cb))
        return self._stream_task

    async def _stream(self, cb=None):
        try:
            try:
                from azure.servicebus.aio import ServiceBusClient
                from azure.servicebus.exceptions import ServiceBusError
            except ImportError:
                _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
                # https://github.com/custom-components/zaptec/issues
                return

            # Get connection details
            conf = await self.live_stream_connection_details()

            # Check if we can use it.
            if any(True for i in ["Password", "Username", "Host"] if conf.get(i) == ""):
                _LOGGER.warning(
                    "Cant enable live update using the servicebus, enable it in the zaptec portal"
                )
                return

            # Open the connection
            constr = (f'Endpoint=sb://{conf["Host"]}/;'
                      f'SharedAccessKeyName={conf["Username"]};'
                      f'SharedAccessKey={conf["Password"]}')
            servicebus_client = ServiceBusClient.from_connection_string(conn_str=constr)
            _LOGGER.debug("Connecting to servicebus using %s", constr)

            self._stream_receiver = None
            async with servicebus_client:
                receiver = servicebus_client.get_subscription_receiver(
                    topic_name=conf["Topic"],
                    subscription_name=conf["Subscription"]
                )
                # Store the receiver in order to close it and cancel this stream
                self._stream_receiver = receiver
                async with receiver:
                    async for msg in receiver:
                        binmsg = "<unknown>"  # For the exception in case it fails before setting the value
                        try:
                            # After some blind research it seems the messages
                            # are encoded with .NET binary xml format (MC-NBFX)
                            # https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-nbfx
                            # Surprisingly there doesn't seem to be any py libries
                            # for that, so a small scaled down version is added
                            # here.
                            binmsg = b"".join(msg.body)
                            # _LOGGER.debug("Received message %s", binmsg)

                            # Decode MC-NBFX message
                            obj = mc_nbfx_decoder(binmsg)
                            #  _LOGGER.debug("Unecoded message: %s", obj)

                            # Convert the json payload
                            json_result = json.loads(obj[0]["text"])
                            _LOGGER.debug("---   Subscription: %s", json_result)

                            # Send result to account that will update the objects
                            self._account.update(json_result)

                            # Execute the callback.
                            if cb:
                                await cb(json_result)

                        except Exception as err:
                            _LOGGER.exception("Couldn't process stream message: %s", err)
                            _LOGGER.debug("Message: %s", binmsg)
                            # Pass the message as the stream must continue.

                        # remove the msg from the "queue"
                        await receiver.complete_message(msg)

        except Exception as err:
            # Do this in order to show the error in the log.
            _LOGGER.exception("Stream failed: %s", err)

        finally:
            # To ensure its not set if not active
            self._stream_receiver = None

    async def cancel_stream(self):
        try:
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            return

        if self._stream_task is not None:
            try:
                if self._stream_receiver is not None:
                    await self._stream_receiver.close()
                self._stream_task.cancel()
                await self._stream_task
                _LOGGER.debug("Canceled stream")
            except (ServiceBusError, CancelledError):
                pass
                # this will still raise a exception, I think its a 3.7 issue.
                # recheck this when the i have updated to 3.9

            finally:
                self._stream_task = None


class Circuit(ZaptecBase):
    """Represents a circuits"""

    chargers: list["Charger"]
    _chargers: list[TDict]

    def __init__(self, data, account):
        super().__init__(data, account)
        self.chargers = []
        self._chargers = data.get("Chargers", []) or []

    async def build(self):
        """Build the python interface."""
        chargers = []
        for item in self._chargers:
            data = await self._account._req_charger(item["Id"])
            chg = Charger(data, self._account)
            _LOGGER.debug("      Charger %s", item["Id"])
            self._account.register(item["Id"], chg)
            await chg.build()
            chargers.append(chg)
        self.chargers = chargers

    async def state(self):
        _LOGGER.debug("Polling state for %s cicuit (%s)", self.id, self._attrs.get('name'))
        data = await self._account._req_circuit(self.id)
        self.set_attributes(data)


class Charger(ZaptecBase):
    """Represents a charger"""

    async def build(self) -> None:
        '''Build the object'''

    async def state(self):
        '''Update the charger state'''
        _LOGGER.debug("Polling state for %s charger (%s)", self.id, self._attrs.get('name'))

        # FIXME: This has multiple set_attributes that might compete for the same key. Fix this.
        # FIXME: E.g. is_online is ambulating between True and 1

        data = await self._account._req_charger(self.id)
        self.set_attributes(data)

        data = await self._account._req_charger_state(self.id)
        data = Account._state_to_attrs(data, 'StateId', self._account._obs_ids)
        self.set_attributes(data)

        # Firmware version is called. SmartMainboardSoftwareApplicationVersion,
        # stateid 908
        # I couldn't find a way to see if it was up to date..
        # maybe remove this later if it dont interest ppl.

        # Fetch some additional attributes from settings
        data = await self._account._req_charger_settings(self.id)
        data = Account._state_to_attrs(data.values(), 'SettingId', self._account._set_ids)
        self.set_attributes(data)

        if self.installation_id in self._account.map:
            firmware_info = await self._account._req_charger_firmware(self.installation_id)
            for fm in firmware_info:
                if fm["ChargerId"] == self.id:
                    self.set_attributes({
                        "current_firmware_version": fm["CurrentVersion"],
                        "available_firmware_version": fm["AvailableVersion"],
                        "firmware_update_to_date": fm["IsUpToDate"],
                    })

    async def command(self, command:str):

        # FIXME: Use the names from the constant json?

        # All methods needs to be checked again
        COMMANDS = {
            "restart_charger": 102,
            "restart_mcu": 103,
            "update_settings": 104,
            "restart_ntp": 105,
            "exit_app_with_code": 106,
            "upgrade_firmware": 200,
            "upgrade_firmware_forced": 201,
            "reset_com_errors": 260,
            "reset_notifications": 261,
            "reset_com_warnings": 262,
            "local_settings": 260,
            "set_plc_npw": 320,
            "set_plc_cocode": 321,
            "set_plc_nmk": 322,
            "set_remote_plc_nmk": 323,
            "set_remote_plc_npw": 324,
            "start_charging": 501,
            "stop_charging": 502,
            "report_charging_state": 503,
            "set_session_id": 504,
            "set_user_uuid": 505,
            "stop_pause": 506,  # Require firmware > 3.2
            "resume_charging": 507,  # Require firmware > 3.2
            "show_granted": 601,
            "show_denied": 602,
            "indicate_app_connect": 603,
            "confirm_charge_card_added": 750,
            "set_authentication_list": 751,
            "debug": 800,
            "get_plc_topology": 801,
            "reset_plc": 802,
            "remote_command": 803,
            "run_grid_test": 804,
            "run_post_production_test": 901,
            "combined_min": 10000,
            "deauthorize_stop": 10001,
            "combined_max": 10999,
            "authorize_charge": None,  # Special case
        }

        if command not in COMMANDS:
            raise ValueError(f"Unknown command {command}")

        if command == "authorize_charge":
            data = await self._account._request(f"chargers/{self.id}/authorizecharge", method="post")
            # FIXME: Verify assumed data structure
            return data

        _LOGGER.debug("Command %s", command)
        cmd = f"chargers/{self.id}/SendCommand/{COMMANDS[command]}"
        _LOGGER.debug("Calling %s", cmd)
        data = await self._account._request(cmd, method="post")
        # FIXME: Verify assumed data structure
        return data

    async def live(self):
        # This don't seems to be documented but the portal uses it
        # TODO check what it returns and parse it to attributes
        data = await self._account._request("chargers/%s/live" % self.id)
        # FIXME: Verify assumed data structure
        return data

    async def settings(self):
        # TODO check what it returns and parse it to attributes
        data = await self._account._request("chargers/%s/settings" % self.id)
        # FIXME: Verify assumed data structure
        return data

    async def update(self, data):
        # https://api.zaptec.com/help/index.html#/Charger/post_api_chargers__id__update
        # Not really sure this should be added as ppl might use it wrong
        cmd = f"chargers/{self.id}/update"
        default = {
            # nullable: true
            # Adjustable between 0 and 32A. If charge current is below the charger minimum charge current (usually 6A), no charge current will be allocated.
            "maxChargeCurrent": 0,
            # MaxPhaseinteger($int32) # Enum 1,3
            "maxChargePhases": "",
            # The minimum allocated charge current. If there is not enough current available to provide the
            # chargers minimum current it will not be able to charge.
            # Usually set to match the vehicle minimum current for charging (defaults to 6A)
            "minChargeCurrent": None,
            # Adjustable between 0 and 32A. If offline charge current is below the charger minimum charge current (usually 6A),
            # no charge current will be allocated when offline.
            # Offline current override should only be done in special cases where charging stations should not automatically optimize offline current.
            # In most cases this setting should be set to -1 to allow ZapCloud to optimise offline current. If -1, offline current will be automatically allocated.
            "offlineChargeCurrent": None,
            # Phasesinteger($int32) ENUM
            # 0 = None
            # 1 = Phase_1
            # 2 = Phase_2
            # 4 = Phase_3
            # 7 = All
            "offlineChargePhase": None,
            # nullable
            # The interval in seconds for a charger to report meter values. Defaults to 900 seconds for Pro and 3600 seconds for Go
            "meterValueInterval": None,
        }

        pass

        # return await self._account.request(cmd, data=data, method="post")

    @property
    def is_authorization_required(self):
        return self._attrs["is_authorization_required"] in TRUTHY

    @property
    def permanent_cable_lock(self):
        return self._attrs["permanent_cable_lock"] in TRUTHY

    @property
    def operating_mode(self):
        modes = {str(v): k for k, v in self._account._const["ChargerOperationModes"].items()}
        v = self._attrs["operating_mode"]
        return modes.get(str(v), str(v))

    @property
    def charger_operation_mode(self):
        modes = {str(v): k for k, v in self._account._const["ChargerOperationModes"].items()}
        v = self._attrs["charger_operation_mode"]
        return modes.get(str(v), str(v))


class Account:
    """This class represent an zaptec account"""

    def __init__(self, username: str, password: str, client=None) -> None:
        _LOGGER.debug("Account init")
        self._username = username
        self._password = password
        self._client = client
        self._token_info = {}
        self._access_token = None
        self.installs: list[Installation] = []
        self.stand_alone_chargers: list[Charger] = []
        self.map: dict[str, ZaptecBase] = {}
        self._const = {}
        self._obs_ids = {}
        self._set_ids = {}
        self.is_built = False

        if client is None:
            self._client = aiohttp.ClientSession()

    def register(self, id: str, data: ZaptecBase):
        '''Register an object data with id'''
        self.map[id] = data

    # =======================================================================
    #   API METHODS

    @staticmethod
    async def check_login(username: str, password: str) -> bool:
        p = {
            "username": username,
            "password": password,
            "grant_type": "password",
        }
        try:
            async with aiohttp.request("POST", TOKEN_URL, data=p) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return True
                else:
                    raise AuthorizationError(f"Failed to authenticate. Got status {resp.status}")
        except aiohttp.ClientConnectorError as err:
            _LOGGER.exception("Bad things happend while trying to authenticate :(")
            raise

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
                raise AuthorizationError("Failed to refresh token, check your credentials.")

    async def _request(self, url: str, method="get", data=None, iteration=1):
        header = {
            "Authorization": "Bearer %s" % self._access_token,
            "Accept": "application/json",
        }
        full_url = API_URL + url
        try:
            async with async_timeout.timeout(30):
                call = getattr(self._client, method)
                if data is not None and method == "post":
                    call = partial(call, json=data)
                # _LOGGER.debug(f"@@@   Req {method} to '{full_url}' payload {data}")
                resp: aiohttp.ClientResponse
                async with call(full_url, headers=header) as resp:
                    # _LOGGER.debug(f"  @   Res {resp.status} data length {resp.content_length}")
                    # _LOGGER.debug(f"  @   Res {resp.status} header {dict((k, v) for k, v in resp.headers.items())}")
                    # _LOGGER.debug(f"  @   Res {resp.status} content {await resp.text()}")
                    if resp.status == 401:  # Unauthorized
                        await self._refresh_token()
                        if iteration > API_RETRIES:
                            raise RequestRetryError(f"Request to {full_url} failed after {iteration} retries")
                        return await self._request(url, iteration=iteration + 1)
                    elif resp.status == 204:  # No content
                        content = await resp.read()
                        return content
                    elif resp.status == 200:  # OK
                        json_result = await resp.json(content_type=None)
                        return json_result
                    else:
                        raise RequestError(f"{method} request to {full_url} failed with status {resp.status}: {resp}")

        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise RequestError(f"Request to {full_url} failed: {err}") from err

    async def _req_constants(self):
        data = await self._request("constants")
        # FIXME: Verify assumed data structure
        return data

    async def _req_installations(self) -> list[TDict]:
        data = await self._request("installation")
        # FIXME: Verify assumed data structure
        return data["Data"]

    async def _req_installation(self, installation_id: str) -> TDict:
        data = await self._request(f"installation/{installation_id}")
        # FIXME: Verify assumed data structure

        # Remove data fields with excessive data, making it bigger than the
        # HA database appreciates for the size of attributes.
        # FIXME: SupportGroup is sub dict. This is not within the declared type
        supportgroup = data.get('SupportGroup')
        if supportgroup is not None:
            if "LogoBase64" in supportgroup:
                logo = supportgroup["LogoBase64"]
                supportgroup["LogoBase64"] = "<Removed, was %s bytes>" %(len(logo))

        return data

    async def _req_hierarchy(self, installation_id: str) -> dict[str, list[dict[str, TValue]]]:
        data = await self._request(f"installation/{installation_id}/hierarchy")
        # FIXME: Verify assumed data structure
        return data

    async def _req_circuit(self, circuit_id: str) -> TDict:
        data = await self._request(f"circuits/{circuit_id}")
        # FIXME: Verify assumed data structure
        return data

    async def _req_chargers(self) -> list[TDict]:
        data = await self._request("chargers")
        # FIXME: Verify assumed data structure
        return data["Data"]

    async def _req_charger(self, charger_id: str) -> TDict:
        data = await self._request(f"chargers/{charger_id}")
        # FIXME: Verify assumed data structure
        return data

    async def _req_charger_firmware(self, installation_id: str) -> TDict:
        data = await self._request(f"chargerFirmware/installation/{installation_id}")
        # FIXME: Verify assumed data structure
        return data

    async def _req_charger_state(self, charger_id: str) -> list[TDict]:
        data = await self._request(f"chargers/{charger_id}/state")
        # FIXME: Verify assumed data structure
        return data

    async def _req_charger_settings(self, charger_id: str) -> dict[str, TDict]:
        data = await self._request(f"chargers/{charger_id}/settings")
        # FIXME: Verify assumed data structure
        return data

    #   API METHODS DONE
    # =======================================================================

    async def build(self):
        """Make the python interface."""
        _LOGGER.debug("Discover and build hierarchy")

        installations = await self._req_installations()

        installs = []
        for data in installations:
            install_data = await self._req_installation(data["Id"])
            _LOGGER.debug("  Installation %s", data["Id"])
            inst = Installation(install_data, self)
            self.register(data["Id"], inst)
            await inst.build()
            installs.append(inst)

        self.installs = installs

        # Will also report chargers listed in installation hierarchy above
        chargers = await self._req_chargers()

        so_chargers = []
        for data in chargers:
            if data["Id"] in self.map:
                continue

            _LOGGER.debug("  Charger %s", data["Id"])
            chg = Charger(data, self)
            self.register(data["Id"], chg)
            so_chargers.append(chg)

        self.stand_alone_chargers = so_chargers

        if not self._const:

            # Get the API constants
            self._const = await self._req_constants()

            # Get the chargers
            device_types = set(
                chg.device_type
                for chg in self.map.values()
                if isinstance(chg, Charger)
            )

            # Define the remaps
            self._obs_ids = Account._get_remap(self._const, ["Observations", "ObservationIds"], device_types)
            self._set_ids = Account._get_remap(self._const, ["Settings", "SettingIds"], device_types)

        self.is_built = True

    def update(self, data: TDict):
        """update for the stream. Note build has to called first."""

        cls_id = data.pop("ChargerId", None)
        if cls_id is not None:
            klass = self.map.get(cls_id)
            if klass:
                d = Account._state_to_attrs([data], 'StateId', self._obs_ids)
                klass.set_attributes(d)
            else:
                _LOGGER.warning("Got update for unknown charger id %s", cls_id)
        else:
            _LOGGER.warning("Unknown update message %s", data)

    @staticmethod
    def _get_remap(const, wanted, device_types=None) -> dict:
        ''' Parse the given zaptec constants record `const` and generate
            a remap dict for the given `wanted` keys. If `device_types` is
            specified, the entries for these device schemas will be merged
            with the main remap dict.
            Example:
                _get_remap(const, ["Observations", "ObservationIds"], [4])
        '''
        ids = {}
        for k, v in const.items():
            if k in wanted:
                ids.update(v)

            if device_types and k == "Schema":
                for schema in v.values():
                    for want in wanted:
                        v2 = schema.get(want)
                        if v2 is None:
                            continue
                        if schema.get("DeviceType") in device_types:
                            ids.update(v2)

        # make the reverse lookup
        ids.update({v: k for k, v in ids.items()})
        return ids

    @staticmethod
    def _state_to_attrs(data: Iterable[dict[str, str]], key: str, keydict: dict[str, str]):
        ''' Convert a list of state data into a dict of attributes. `key`
            is the key that specifies the attribute name. `keydict` is a
            dict that maps the key value to an attribute name.
        '''
        out = {}
        for item in data:
            skey = item.get(key)
            if skey is None:
                _LOGGER.debug("Missing key %s in %s", key, item)
                continue
            value = item.get("Value", item.get("ValueAsString", MISSING))
            if value is not MISSING:
                kv = keydict.get(skey, f"{key} {skey}")
                if kv in out:
                    _LOGGER.debug("Duplicate key %s. Is '%s', new '%s'", kv, out[kv], value)
                out[kv] = value
        return out


if __name__ == "__main__":
    # Just to execute the script manually.
    import os

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")
        acc = Account(
            username,
            password,
            client=aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            ),
        )

        try:
            # Builds the interface.
            await acc.build()

            # # Save the constants
            # with open("constant.json", "w") as outfile:
            #     json.dump(acc._const, outfile, indent=2)

            # Update the state to get all the attributes.
            for obj in acc.map.values():
                await obj.state()

            with open("data.json", "w") as outfile:

                async def cb(data):
                    print(data)
                    outfile.write(json.dumps(data, indent=2) + '\n')
                    outfile.flush()

                for ins in acc.installs:
                    await ins._stream(cb=cb)

        finally:
            await acc._client.close()

    asyncio.run(gogo())
