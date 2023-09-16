"""Main API for Zaptec."""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from concurrent.futures import CancelledError
from functools import partial
from typing import Any, Callable

import aiohttp
import async_timeout

# pylint: disable=missing-function-docstring

# Type definitions
TValue = str | int | float | bool
TDict = dict[str, TValue]

_LOGGER = logging.getLogger(__name__)

# Set to True to debug log all API calls
DEBUG_API_CALLS = False

# Set to True to debug log all API errors
DEBUG_API_ERRORS = True

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
    from validate import validate

    # remove me later
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

else:
    from .const import (API_RETRIES, API_URL, CONST_URL, FALSY, MISSING,
                        TOKEN_URL, TRUTHY)
    from .misc import mc_nbfx_decoder, to_under
    from .validate import validate


class ZaptecApiError(Exception):
    '''Base exception for all Zaptec API errors'''


class AuthorizationError(ZaptecApiError):
    '''Authenatication failed'''


class RequestError(ZaptecApiError):
    '''Failed to get the results from the API'''

    def __init__(self, message, error_code):
        super().__init__(message)
        self.error_code = error_code


class RequestTimeoutError(ZaptecApiError):
    '''Failed to get the results from the API'''


class RequestRetryError(ZaptecApiError):
    '''Retries too many times'''


class ZaptecBase(ABC):
    """Base class for Zaptec objects"""

    id: str
    name: str
    _account: "Account"
    _attrs: TDict

    # Type definitions and convertions on the attributes
    ATTR_TYPES: dict[str, Callable] = {}

    def __init__(self, data: TDict, account: "Account") -> None:
        self._account = account
        self._attrs = {}
        self.set_attributes(data)

    def set_attributes(self, data: TDict) -> bool:
        """Set the class attributes from the given data"""""
        for k, v in data.items():
            # Cast the value to the correct type
            new_key = to_under(k)
            new_v = self.ATTR_TYPES.get(new_key, lambda x: x)(v)
            new_vt = type(new_v).__qualname__
            if new_key not in self._attrs:
                qn = self.__class__.__qualname__
                _LOGGER.debug(">>>   Adding %s.%s (%s)  =  <%s> %s", qn, new_key, k, new_vt, new_v)
            elif self._attrs[new_key] != new_v:
                qn = self.__class__.__qualname__
                _LOGGER.debug(">>>   Updating %s.%s (%s)  =  <%s> %s  (was %s)", qn, new_key, k, new_vt, new_v, self._attrs[new_key])
            self._attrs[new_key] = new_v

    def __getattr__(self, key):
        try:
            return self._attrs[to_under(key)]
        except KeyError as exc:
            raise AttributeError(exc) from exc

    def get(self, key, default=MISSING):
        if default is MISSING:
            return self._attrs[to_under(key)]
        else:
            return self._attrs.get(to_under(key), default)

    def asdict(self):
        """Return the attributes as a dict"""
        return self._attrs

    @abstractmethod
    async def build(self) -> None:
        """Build the object"""

    @abstractmethod
    async def state(self) -> None:
        """Update the state of the object"""


class Installation(ZaptecBase):
    """Represents an installation"""

    circuits: list[Circuit]

    # Type conversions for the named attributes
    ATTR_TYPES = {
        "active": bool,
    }

    def __init__(self, data, account):
        super().__init__(data, account)
        self.connection_details = None
        self.circuits = []

        self._stream_task = None
        self._stream_receiver = None

    async def build(self):
        """Build the installation object hierarchy."""

        # Get state to ensure we have the full and updated data
        await self.state()

        # Get the hierarchy of circurits and chargers
        try:
            hierarchy = await self._account._request(f"installation/{self.id}/hierarchy")
        except RequestError as err:
            if err.error_code == 403:
                _LOGGER.warning("Access denied to installation hierarchy of %s. The user might not have access.", self.id)
                self.circuits = []
                return
            raise

        circuits = []
        for item in hierarchy["Circuits"]:
            _LOGGER.debug("    Circuit %s", item["Id"])
            circ = Circuit(item, self._account)
            self._account.register(item["Id"], circ)
            await circ.build()
            circuits.append(circ)

        self.circuits = circuits

    async def state(self):
        _LOGGER.debug("Polling state for %s installation (%s)", self.id, self._attrs.get('name'))
        data = await self.installation_info()
        self.set_attributes(data)

    async def live_stream_connection_details(self):
        data = await self._account._request(
            f"installation/{self.id}/messagingConnectionDetails"
        )
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
        """Main stream handler"""
        try:
            try:
                from azure.servicebus.aio import ServiceBusClient
                from azure.servicebus.exceptions import ServiceBusError
            except ImportError:
                _LOGGER.warning("Azure Service bus is not available. Resolving to polling")
                # https://github.com/custom-components/zaptec/issues
                return

            # Get connection details
            try:
                conf = await self.live_stream_connection_details()
            except RequestError as err:
                if err.error_code != 403:
                    raise
                _LOGGER.warning("Failed to get live stream info. Check if user have access in the zaptec portal")
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
                        # For the exception in case it fails before setting the value
                        binmsg = "<unknown>"
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

                            json_log = json_result.copy()
                            if "StateId" in json_log:
                                json_log["StateId"] = f"{json_log['StateId']} ({self._account._obs_ids.get(json_log['StateId'])})"
                            _LOGGER.debug("---   Subscription: %s", json_log)

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

    #-----------------
    # API methods
    #-----------------

    async def installation_info(self) -> TDict:
        '''Raw request for installation data'''

        # Get the installation data
        data = await self._account._request(f"installation/{self.id}")

        # Remove data fields with excessive data, making it bigger than the
        # HA database appreciates for the size of attributes.
        # FIXME: SupportGroup is sub dict. This is not within the declared type
        supportgroup = data.get('SupportGroup')
        if supportgroup is not None:
            if "LogoBase64" in supportgroup:
                logo = supportgroup["LogoBase64"]
                supportgroup["LogoBase64"] = f"<Removed, was {len(logo)} bytes>"

        return data

    async def set_limit_current(self, **kwargs):
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
            raise ValueError(
                "Either availableCurrent or all of availableCurrentPhase1, "
                "availableCurrentPhase2, availableCurrentPhase3 must be set"
            )

        data = await self._account._request(
            f"installation/{self.id}/update",
            method="post", data=kwargs
        )
        return data

    async def set_authenication_required(self, required: bool):
        """Set if authorization is required for charging"""

        # Undocumented feature, but the WEB API uses it. It fetches the
        # installation data and updates IsAuthorizationRequired field
        data = {
            "Id": self.id,
            "IsRequiredAuthentication": required,
        }
        result = await self._account._request(
            f"installation/{self.id}",
            method="put", data=data
        )
        return result

class Circuit(ZaptecBase):
    """Represents a circuits"""

    chargers: list["Charger"]

    # Type conversions for the named attributes
    ATTR_TYPES = {
        "is_active": bool,
        "is_authorisation_required": bool,
    }

    def __init__(self, data, account):
        super().__init__(data, account)
        self.chargers = []

    async def build(self):
        """Build the python interface."""

        # Get state to ensure we have the full and updated data
        await self.state()

        chargers = []
        for item in self._attrs['chargers']:
            _LOGGER.debug("      Charger %s", item["Id"])
            chg = Charger(item, self._account)
            self._account.register(item["Id"], chg)
            await chg.build()
            chargers.append(chg)

        self.chargers = chargers

    async def state(self):
        _LOGGER.debug("Polling state for %s cicuit (%s)", self.id, self._attrs.get('name'))
        data = await self.circuit_info()
        self.set_attributes(data)

    #-----------------
    # API methods
    #-----------------

    async def circuit_info(self) -> TDict:
        '''Raw request for circuit data'''
        data = await self._account._request(f"circuits/{self.id}")
        return data


class Charger(ZaptecBase):
    """Represents a charger"""

    # Type conversions for the named attributes
    ATTR_TYPES = {
        "active": bool,
        "is_authorization_required": lambda x: x in TRUTHY,
        "is_online": lambda x: x in TRUTHY,
        "charge_current_installation_max_limit": float,
        "charger_max_current": float,
        "charger_min_current": float,
        "completed_session": json.loads,
        "current_phase1": float,
        "current_phase2": float,
        "current_phase3": float,
        "permanent_cable_lock": lambda x: x in TRUTHY,
        "total_charge_power": float,
        "voltage_phase1": float,
        "voltage_phase2": float,
        "voltage_phase3": float,
    }

    def __init__(self, data: TDict, account: "Account") -> None:
        super().__init__(data, account)

        # Append the attr types that depends on self
        attr_types = self.ATTR_TYPES.copy()
        attr_types.update({
            "operating_mode": self.type_operation_mode,
            "charger_operation_mode": self.type_operation_mode,
        })
        self.ATTR_TYPES = attr_types

    async def build(self) -> None:
        '''Build the object'''

        # Don't update state at build, because the state and settings ids
        # is not loaded yet.

    async def state(self):
        '''Update the charger state'''
        _LOGGER.debug("Polling state for %s charger (%s)", self.id, self._attrs.get('name'))

        try:
            # Get the main charger info
            charger = await self.charger_info()
            self.set_attributes(charger)
        except RequestError as err:
            # An unprivileged user will get a 403 error, but the user is able
            # to get _some_ info about the charger by getting a list of
            # chargers.
            if err.error_code != 403:
                raise
            _LOGGER.debug("Access denied to charger %s, attempting list", self.id)
            chargers = await self._account._request("chargers")
            for chg in chargers["Data"]:
                if chg["Id"] == self.id:
                    self.set_attributes(chg)
                    break

        # Get the state from the charger
        try:
            state = await self._account._request(f"chargers/{self.id}/state")
            data = Account._state_to_attrs(state, 'StateId', self._account._obs_ids)
            self.set_attributes(data)
        except RequestError as err:
            if err.error_code != 403:
                raise
            _LOGGER.debug("Access denied to charger %s state", self.id)

        # Firmware version is called. SmartMainboardSoftwareApplicationVersion,
        # stateid 908
        # I couldn't find a way to see if it was up to date..
        # maybe remove this later if it dont interest ppl.

        # Fetch some additional attributes from settings
        try:
            settings = await self._account._request(f"chargers/{self.id}/settings")
            data = Account._state_to_attrs(settings.values(), 'SettingId', self._account._set_ids)
            self.set_attributes(data)
        except RequestError as err:
            if err.error_code != 403:
                raise
            _LOGGER.debug("Access denied to charger %s settings", self.id)

        if self.installation_id in self._account.map:
            try:
                firmware_info = await self._account._request(
                    f"chargerFirmware/installation/{self.installation_id}"
                )

                for fm in firmware_info:
                    if fm["ChargerId"] == self.id:
                        self.set_attributes({
                            "current_firmware_version": fm["CurrentVersion"],
                            "available_firmware_version": fm["AvailableVersion"],
                            "firmware_update_to_date": fm["IsUpToDate"],
                        })
            except RequestError as err:
                if err.error_code != 403:
                    raise
                _LOGGER.debug("Access denied to charger %s firmware info", self.id)

    async def live(self):
        # FIXME: Is this an experiment? Omit?

        # This don't seems to be documented but the portal uses it
        # FIXME check what it returns and parse it to attributes
        data = await self._account._request(f"chargers/{self.id}/live")
        # FIXME: Missing validator (see validate)
        return data

    async def update(self, data):
        # FIXME: Is this in use or an experiment? Should it be removed from production code?

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

    def type_operation_mode(self, v):
        modes = {str(v): k for k, v in self._account._const["ChargerOperationModes"].items()}
        return modes.get(str(v), str(v))

    #-----------------
    # API methods
    #-----------------

    async def charger_info(self) -> TDict:
        data = await self._account._request(f"chargers/{self.id}")
        return data

    async def command(self, command: str):
        """Send a command to the charger"""

        if command == 'authorize_charge':
            return await self.authorize_charge()

        # Fetching the name from the const is perhaps not a good idea
        # if Zaptec is changing them.
        cmdid = self._account._cmd_ids.get(command)
        if cmdid is None:
            raise ValueError(f"Unknown command {command}")

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        data = await self._account._request(
            f"chargers/{self.id}/SendCommand/{cmdid}",
            method="post"
        )
        return data

    async def set_settings(self, settings: dict[str, Any]):
        """Set settings on the charger"""

        set_ids = self._account._set_ids
        values = [{'id': set_ids.get(k), 'value': v} for k, v in settings.items()]

        if any(d for d in values if d['id'] is None):
            raise ValueError(f"Unknown setting '{settings}'")

        _LOGGER.debug("Settings %s", settings)
        data = await self._account._request(
            f"chargers/{self.id}/settings",
            method="post", data=values
        )
        return data

    async def stop_charging_final(self):
        return await self.command("stop_charging_final")

    async def resume_charging(self):
        return await self.command("resume_charging")

    async def deauthorize_and_stop(self):
        return await self.command("deauthorize_and_stop")

    async def restart_charger(self):
        return await self.command("restart_charger")

    async def upgrade_firmware(self):
        return await self.command("upgrade_firmware")

    async def authorize_charge(self):
        _LOGGER.debug("Authorize charge")
        data = await self._account._request(
            f"chargers/{self.id}/authorizecharge",
            method="post"
        )
        return data

    async def set_current_in_minimum(self, value):
        return await self.set_settings({"current_in_minimum": value})

    async def set_current_in_maxium(self, value):
        return await self.set_settings({"current_in_maximum": value})


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
        self._cmd_ids = {}
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

        def log_request():
            try:
                _LOGGER.debug(f"@@@  REQUEST {method.upper()} to '{full_url}' length {len(data or '')}")
                if data:
                    _LOGGER.debug(f"     content {data}")
            except Exception as err:
                _LOGGER.exception("Failed to log response")

        async def log_response(resp: aiohttp.ClientResponse):
            try:
                contents = await resp.read()
                _LOGGER.debug(f"@@@  RESPONSE {resp.status} length {len(contents)}")
                _LOGGER.debug(f"     header {dict((k, v) for k, v in resp.headers.items())}")
                if not contents:
                    return
                if resp.status != 200:
                    _LOGGER.debug(f"     content {await resp.text()}")
                else:
                    _LOGGER.debug(f"     json '{await resp.json(content_type=None)}'")
            except Exception as err:
                _LOGGER.exception("Failed to log response")

        header = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        full_url = API_URL + url
        try:
            async with async_timeout.timeout(30):
                if DEBUG_API_CALLS:
                    log_request()

                call = getattr(self._client, method)
                if data is not None and method in ("post", "put"):
                    call = partial(call, json=data)

                resp: aiohttp.ClientResponse
                async with call(full_url, headers=header) as resp:
                    if DEBUG_API_CALLS:
                        await log_response(resp)

                    if resp.status == 401:  # Unauthorized
                        await self._refresh_token()
                        if iteration > API_RETRIES:
                            raise RequestRetryError(f"Request to {full_url} failed after {iteration} retries")
                        return await self._request(url, iteration=iteration + 1)

                    elif resp.status == 204:  # No content
                        content = await resp.read()
                        return content

                    elif resp.status == 200:  # OK
                        # FIXME: This will raise json error if the json is invalid. How to handle this?
                        json_result = await resp.json(content_type=None)

                        # Validate the incoming json data
                        # FIXME: This raise pydantic.ValidationError if the json is unexpected. How to handle this?
                        validate(json_result, url=url)

                        return json_result

                    else:
                        if DEBUG_API_ERRORS and not DEBUG_API_CALLS:
                            _LOGGER.debug("Failing request:")
                            log_request()
                            await log_response(resp)

                        raise RequestError(
                            f"{method} request to {full_url} failed with status {resp.status}: {resp}",
                            resp.status
                        )

        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise RequestTimeoutError(f"Request to {full_url} failed: {err}") from err

    #   API METHODS DONE
    # =======================================================================

    async def build(self):
        """Make the python interface."""
        _LOGGER.debug("Discover and build hierarchy")

        # Get list of installations
        installations = await self._request("installation")

        installs = []
        for data in installations["Data"]:
            _LOGGER.debug("  Installation %s", data["Id"])
            inst = Installation(data, self)
            self.register(data["Id"], inst)
            await inst.build()
            installs.append(inst)

        self.installs = installs

        # Get list of chargers
        # Will also report chargers listed in installation hierarchy above
        chargers = await self._request("chargers")

        so_chargers = []
        for data in chargers["Data"]:
            if data["Id"] in self.map:
                continue

            _LOGGER.debug("  Charger %s", data["Id"])
            chg = Charger(data, self)
            self.register(data["Id"], chg)
            await chg.build()
            so_chargers.append(chg)

        self.stand_alone_chargers = so_chargers

        if not self._const:

            # Get the API constants
            self._const = await self._request("constants")

            # Find the chargers device types
            device_types = set(
                chg.device_type
                for chg in self.map.values()
                if isinstance(chg, Charger)
            )

            # Define the remaps
            self._obs_ids = Account._get_remap(self._const, ["Observations", "ObservationIds"], device_types)
            self._set_ids = Account._get_remap(self._const, ["Settings", "SettingIds"], device_types)
            self._cmd_ids = Account._get_remap(self._const, ["Commands", "CommandIds"], device_types)

            # Commands can also be specified as lower case strings
            self._cmd_ids.update({
                to_under(k): v for k, v in self._cmd_ids.items() if isinstance(k, str)
            })

        # Update the state on all chargers
        for data in self.map.values():
            if isinstance(data, Charger):
                await data.state()

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
    from pprint import pprint

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
                pprint(obj.asdict())

            # with open("data.json", "w") as outfile:

            #     async def cb(data):
            #         print(data)
            #         outfile.write(json.dumps(data, indent=2) + '\n')
            #         outfile.flush()

            #     for ins in acc.installs:
            #         await ins._stream(cb=cb)

        finally:
            await acc._client.close()

    asyncio.run(gogo())
