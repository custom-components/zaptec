"""Main API for Zaptec."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from collections import UserDict
from collections.abc import Iterable
from concurrent.futures import CancelledError
from contextlib import aclosing
from typing import Any, AsyncGenerator, Callable, Protocol, cast

import aiohttp
import pydantic

from .const import (
    API_RETRIES, API_RETRY_FACTOR, API_RETRY_JITTER, API_RETRY_MAXTIME,
    API_TIMEOUT, API_URL, MISSING, TOKEN_URL, TRUTHY, CHARGER_EXCLUDES)
from .misc import mc_nbfx_decoder, to_under
from .validate import validate
from .zconst import ZConst

"""
stuff are missing from the api docs compared to what the portal uses.
circuits/{self.id}/live
circuits/{self.id}/
https://api.zaptec.com/api/dashboard/activechargersforowner?limit=250
/dashbord
signalr is used by the website.
"""

# pylint: disable=missing-function-docstring

_LOGGER = logging.getLogger(__name__)

# Set to True to debug log all API calls
DEBUG_API_CALLS = False

# Set to True to debug log all API errors
# Setting this to False because the error messages are very verbose and will
# flood the log in Home Assistant.
DEBUG_API_ERRORS = False

# Global var for the API constants from Zaptec
ZCONST: ZConst = ZConst()

# Type definitions
TValue = str | int | float | bool
TDict = dict[str, TValue]


class TLogExc(Protocol):
    """Protocol for logging exceptions"""

    def __call__(self, exc: Exception) -> Exception:
        ...


class ZaptecApiError(Exception):
    """Base exception for all Zaptec API errors"""


class AuthenticationError(ZaptecApiError):
    """Authenatication failed"""


class RequestError(ZaptecApiError):
    """Failed to get the results from the API"""

    def __init__(self, message, error_code):
        super().__init__(message)
        self.error_code = error_code


class RequestConnectionError(ZaptecApiError):
    """Failed to make the request to the API"""


class RequestTimeoutError(ZaptecApiError):
    """Failed to get the results from the API"""


class RequestRetryError(ZaptecApiError):
    """Retries too many times"""


class RequestDataError(ZaptecApiError):
    """Data is not valid"""


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
        """Set the class attributes from the given data"""
        for k, v in data.items():
            # Cast the value to the correct type
            new_key = to_under(k)
            try:
                # Get the type conversion function and apply it
                type_fn = self.ATTR_TYPES.get(new_key, lambda x: x)
                new_v = type_fn(v)
            except Exception as err:
                _LOGGER.error(
                    "Failed to convert attribute %s (%s) value <%s> %s: %s",
                    k,
                    new_key,
                    type(v).__qualname__,
                    v,
                    err,
                )
                new_v = v
            new_vt = type(new_v).__qualname__
            if new_key not in self._attrs:
                _LOGGER.debug(
                    ">>>   Adding %s.%s (%s)  =  <%s> %s",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    new_v,
                )
            elif self._attrs[new_key] != new_v:
                _LOGGER.debug(
                    ">>>   Updating %s.%s (%s)  =  <%s> %s  (was %s)",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    new_v,
                    self._attrs[new_key],
                )
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

    @property
    def qual_id(self):
        qn = self.__class__.__qualname__
        if "id" not in self._attrs:
            return qn
        return f"{qn}[{self.id[-6:]}]"

    def asdict(self):
        """Return the attributes as a dict"""
        return self._attrs

    @abstractmethod
    async def build(self) -> None:
        """Build the object"""

    @abstractmethod
    async def state(self) -> None:
        """Update the state of the object"""

    @staticmethod
    def state_to_attrs(
        data: Iterable[dict[str, str]],
        key: str,
        keydict: dict[str, str],
        excludes: set[str] = set(), 
    ):
        """Convert a list of state data into a dict of attributes. `key`
        is the key that specifies the attribute name. `keydict` is a
        dict that maps the key value to an attribute name.
        """
        out = {}
        for item in data:
            skey = item.get(key)
            if skey is None:
                _LOGGER.debug("Missing key %s in %s", key, item)
                continue
            if str(skey) in excludes:
                _LOGGER.debug("Excluding key %s entry: %s", skey, item)
                continue
            value = item.get("Value", item.get("ValueAsString", MISSING))
            if value is not MISSING:
                kv = keydict.get(skey, f"{key} {skey}")
                if kv in out:
                    _LOGGER.debug(
                        "Duplicate key %s. Is '%s', new '%s'", kv, out[kv], value
                    )
                out[kv] = value
        return out


class Installation(ZaptecBase):
    """Represents an installation"""

    circuits: list[Circuit]

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES = {
        "active": bool,
        "authentication_type": ZCONST.type_authentication_type,
        "current_user_roles": ZCONST.type_user_roles,
        "installation_type": ZCONST.type_installation_type,
        "network_type": ZCONST.type_network_type,
    }

    def __init__(self, data, account):
        super().__init__(data, account)
        self.connection_details = None
        self.circuits = []

        self._stream_task = None
        self._stream_receiver = None

    async def build(self):
        """Build the installation object hierarchy."""

        # Get the hierarchy of circurits and chargers
        try:
            hierarchy = await self._account._request(
                f"installation/{self.id}/hierarchy"
            )
        except RequestError as err:
            if err.error_code == 403:
                _LOGGER.warning(
                    "Access denied to installation hierarchy of %s. The user might not have access.",
                    self.id,
                )
                self.circuits = []
                return
            raise

        circuits = []
        for item in hierarchy["Circuits"]:
            _LOGGER.debug("    Circuit %s", item["Id"])
            circ = Circuit(item, self._account, installation=self)
            self._account.register(item["Id"], circ)
            await circ.build()
            circuits.append(circ)

        self.circuits = circuits

    async def state(self):
        _LOGGER.debug(
            "Polling state for %s (%s)", self.qual_id, self._attrs.get("name")
        )
        data = await self.installation_info()
        self.set_attributes(data)

    async def live_stream_connection_details(self):
        # NOTE: API call deprecated
        data = await self._account._request(
            f"installation/{self.id}/messagingConnectionDetails"
        )
        self.connection_details = data
        return data

    async def stream(self, cb=None, ssl_context=None) -> asyncio.Task|None:
        """Kickoff the steam in the background."""
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
            return None

        await self.cancel_stream()
        self._stream_task = asyncio.create_task(self._stream(cb=cb, ssl_context=ssl_context))
        return self._stream_task

    async def _stream(self, cb=None, ssl_context=None):
        """Main stream handler"""
        try:
            try:
                from azure.servicebus.aio import ServiceBusClient
            except ImportError:
                _LOGGER.warning(
                    "Azure Service bus is not available. Resolving to polling"
                )
                return

            # Get connection details
            try:
                conf = await self.live_stream_connection_details()
            except RequestError as err:
                if err.error_code != 403:
                    raise
                _LOGGER.warning(
                    "Failed to get live stream info. Check if user have access in the zaptec portal"
                )
                return

            # Open the connection
            constr = (
                f'Endpoint=sb://{conf["Host"]}/;'
                f'SharedAccessKeyName={conf["Username"]};'
                f'SharedAccessKey={conf["Password"]}'
            )
            kw = {}
            if ssl_context:
                kw["ssl_context"] = ssl_context
            servicebus_client = ServiceBusClient.from_connection_string(conn_str=constr, **kw)
            _LOGGER.debug("Connecting to servicebus using %s", constr)

            self._stream_receiver = None
            async with servicebus_client:
                receiver = await asyncio.to_thread(
                    servicebus_client.get_subscription_receiver,
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
                                json_log[
                                    "StateId"
                                ] = f"{json_log['StateId']} ({ZCONST.observations.get(json_log['StateId'])})"
                            _LOGGER.debug("---   Subscription: %s", json_log)

                            # Send result to account that will update the objects
                            self._account.update(json_result)

                            # Execute the callback.
                            if cb:
                                await cb(json_result)

                        except Exception as err:
                            _LOGGER.exception(
                                "Couldn't process stream message: %s", err
                            )
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

    # -----------------
    # API methods
    # -----------------

    async def installation_info(self) -> TDict:
        """Raw request for installation data"""

        # Get the installation data
        data = await self._account._request(f"installation/{self.id}")

        # Remove data fields with excessive data, making it bigger than the
        # HA database appreciates for the size of attributes.
        # FIXME: SupportGroup is sub dict. This is not within the declared type
        supportgroup = data.get("SupportGroup")
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
        has_availablecurrent = kwargs.get("availableCurrent") is not None
        has_availablecurrentphases = all(
            kwargs.get(k) is not None
            for k in (
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
            f"installation/{self.id}/update", method="post", data=kwargs
        )
        return data

    async def set_authentication_required(self, required: bool):
        """Set if authorization is required for charging"""

        # The naming of this function is ambigous. The Zaptec API is inconsistent
        # on its use of the terms authorization and authentication. In the
        # GUI this setting is termed "authorisation".

        # Undocumented feature, but the WEB API uses it. It fetches the
        # installation data and updates IsAuthorizationRequired field
        data = {
            "Id": self.id,
            "IsRequiredAuthentication": required,
        }
        # NOTE: Undocumented API call
        result = await self._account._request(
            f"installation/{self.id}", method="put", data=data
        )
        return result


class Circuit(ZaptecBase):
    """Represents a circuits"""

    chargers: list["Charger"]

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES = {
        "active": bool,
    }

    def __init__(
        self, data: TDict, account: Account, installation: Installation | None = None
    ):
        super().__init__(data, account)
        self.chargers = []
        self.installation = installation

    async def build(self):
        """Build the python interface."""

        chargers = []
        for item in self._attrs["chargers"]:
            _LOGGER.debug("      Charger %s", item["Id"])
            chg = Charger(item, self._account, circuit=self)
            self._account.register(item["Id"], chg)
            await chg.build()
            chargers.append(chg)

        self.chargers = chargers

    async def state(self):
        _LOGGER.debug(
            "Polling state for %s (%s)", self.qual_id, self._attrs.get("name")
        )
        data = await self.circuit_info()
        self.set_attributes(data)

    # -----------------
    # API methods
    # -----------------

    async def circuit_info(self) -> TDict:
        """Raw request for circuit data"""
        # NOTE: Undocumented API call. circuit is no longer part of the official docs
        data = await self._account._request(f"circuits/{self.id}")
        return data


class Charger(ZaptecBase):
    """Represents a charger"""

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES = {
        "active": bool,
        "authentication_required": lambda x: x in TRUTHY,
        "authentication_type": ZCONST.type_authentication_type,
        "charge_current_installation_max_limit": float,
        "charger_max_current": float,
        "charger_min_current": float,
        "charger_operation_mode": ZCONST.type_charger_operation_mode,
        "completed_session": ZCONST.type_completed_session,
        "current_phase1": float,
        "current_phase2": float,
        "current_phase3": float,
        "current_user_roles": ZCONST.type_user_roles,
        "device_type": ZCONST.type_device_type,
        "is_authorization_required": lambda x: x in TRUTHY,
        "is_online": lambda x: x in TRUTHY,
        "network_type": ZCONST.type_network_type,
        "operating_mode": ZCONST.type_charger_operation_mode,
        "permanent_cable_lock": lambda x: x in TRUTHY,
        "signed_meter_value": ZCONST.type_ocmf,
        "total_charge_power": float,
        "voltage_phase1": float,
        "voltage_phase2": float,
        "voltage_phase3": float,
    }

    def __init__(
        self, data: TDict, account: "Account", circuit: Circuit | None = None
    ) -> None:
        super().__init__(data, account)

        self.circuit = circuit

    async def build(self) -> None:
        """Build the object"""

        # Don't update state at build, because the state and settings ids
        # is not loaded yet.

    async def state(self):
        """Update the charger state"""
        _LOGGER.debug(
            "Polling state for %s (%s)", self.qual_id, self._attrs.get("name")
        )

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
            data = self.state_to_attrs(state, "StateId", ZCONST.observations,
                                       excludes=CHARGER_EXCLUDES)
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
            # NOTE: Undocumented API call
            settings = await self._account._request(f"chargers/{self.id}/settings")
            data = self.state_to_attrs(settings.values(), "SettingId", ZCONST.settings)
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
                        self.set_attributes(
                            {
                                "current_firmware_version": fm["CurrentVersion"],
                                "available_firmware_version": fm["AvailableVersion"],
                                "firmware_update_to_date": fm["IsUpToDate"],
                            }
                        )
            except RequestError as err:
                if err.error_code != 403:
                    raise
                _LOGGER.debug("Access denied to charger %s firmware info", self.id)

    async def live(self):
        # FIXME: Is this an experiment? Omit?

        # This don't seems to be documented but the portal uses it
        # FIXME check what it returns and parse it to attributes
        # NOTE: Undocumented API call
        data = await self._account._request(f"chargers/{self.id}/live")
        # FIXME: Missing validator (see validate)
        return data

    # -----------------
    # API methods
    # -----------------

    async def charger_info(self) -> TDict:
        data = await self._account._request(f"chargers/{self.id}")
        return data

    async def command(self, command: str | int):
        """Send a command to the charger"""

        if command == "authorize_charge":
            return await self.authorize_charge()

        # Fetching the name from the ZCONST is perhaps not a good idea
        # if Zaptec is changing them.
        if command not in ZCONST.commands:
            raise ValueError(f"Unknown command '{command}'")

        if isinstance(command, int):
            # If int, look up the command name
            cmdid = command
            command = ZCONST.commands.get(command)
        else:
            cmdid = ZCONST.commands.get(command)

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        data = await self._account._request(
            f"chargers/{self.id}/SendCommand/{cmdid}", method="post"
        )
        return data

    async def set_settings(self, settings: dict[str, Any]):
        """Set settings on the charger"""

        values = [
            {"id": ZCONST.settings.get(k), "value": v} for k, v in settings.items()
        ]

        if any(d for d in values if d["id"] is None):
            raise ValueError(f"Unknown setting '{settings}'")

        _LOGGER.debug("Settings %s", settings)
        # NOTE: Undocumented API call
        data = await self._account._request(
            f"chargers/{self.id}/settings", method="post", data=values
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
        # NOTE: Undocumented API call
        data = await self._account._request(
            f"chargers/{self.id}/authorizecharge", method="post"
        )
        return data

    async def set_current_in_minimum(self, value):
        return await self.set_settings({"current_in_minimum": value})

    async def set_current_in_maxium(self, value):
        return await self.set_settings({"current_in_maximum": value})

    async def set_permanent_cable_lock(self, lock: bool):
        """Set if the cable lock is permanent"""
        _LOGGER.debug("Set permanent cable lock %s", lock)
        data = {
            "Cable": {
                "PermanentLock": lock,
            },
        }
        # NOTE: Undocumented API call
        result = await self._account._request(
            f"chargers/{self.id}/localSettings", method="post", data=data
        )
        return result
    
    async def set_hmi_brightness(self, brightness: float):
        """Set the HMI brightness"""
        _LOGGER.debug("Set HMI brightness %s", brightness)
        data = {
            "Device": {
                "HmiBrightness": brightness,
            },
        }
        # NOTE: Undocumented API call
        result = await self._account._request(
            f"chargers/{self.id}/localSettings", method="post", data=data
        )
        return result


class Account:
    """This class represent an zaptec account"""

    def __init__(
        self, username: str, password: str, *, 
        client: aiohttp.ClientSession | None = None,
        max_time: float = API_RETRY_MAXTIME,
    ) -> None:
        _LOGGER.debug("Account init")
        self._username = username
        self._password = password
        self._client = client or aiohttp.ClientSession()
        self._token_info = {}
        self._access_token = None
        self.installations: list[Installation] = []
        self.stand_alone_chargers: list[Charger] = []
        self.map: dict[str, ZaptecBase] = {}
        self.is_built = False
        self._timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        self._max_time = max_time

    def register(self, id: str, data: ZaptecBase):
        """Register an object data with id"""
        self.map[id] = data

    def unregister(self, id: str):
        """Unregister an object data with id"""
        del self.map[id]

    # =======================================================================
    #   API METHODS

    @staticmethod
    def _request_log(url, method, iteration, **kwargs):
        """Helper that yields request log entries."""
        try:
            data = kwargs.get("data", "")
            jdata = kwargs.get("json", "")
            dlength = f" data length {len(data)}" if "data" in kwargs else ""
            jlength = f" json length {len(jdata)}" if "json" in kwargs else ""
            attempt = f" (attempt {iteration})" if iteration > 1 else ""
            yield f"@@@  REQUEST {method.upper()} to '{url}'{dlength}{jlength}{attempt}"
            if "headers" in kwargs:
                yield f"     headers {dict((k, v) for k, v in kwargs['headers'].items())}"
            if "data" in kwargs:
                yield f"     data '{kwargs['data']}'"
            if "json" in kwargs:
                yield f"     json '{kwargs['json']}'"
        except Exception:
            _LOGGER.exception("Failed to log request (ignored exception)")

    @staticmethod
    async def _response_log(resp: aiohttp.ClientResponse):
        """Helper that yield response log entries."""
        try:
            contents = await resp.read()
            yield f"@@@  RESPONSE {resp.status} length {len(contents)}"
            yield f"     headers {dict((k, v) for k, v in resp.headers.items())}"
            if not contents:
                return
            if resp.status != 200:
                yield f"     data '{await resp.text()}'"
            else:
                yield f"     json '{await resp.json(content_type=None)}'"
        except Exception:
            _LOGGER.exception("Failed to log response (ignored exception)")

    @staticmethod
    async def check_login(
        username: str, password: str, client: aiohttp.ClientSession | None = None
    ) -> bool:
        """Check if the login is valid."""
        client = client or aiohttp.ClientSession()
        p = {
            "username": username,
            "password": password,
            "grant_type": "password",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            async with client.post(TOKEN_URL, data=p, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return True
                else:
                    raise AuthenticationError(
                        f"Failed to authenticate. Got status {resp.status}"
                    )
        except asyncio.TimeoutError as err:
            if DEBUG_API_ERRORS:
                _LOGGER.error("Authentication timeout")
            raise RequestTimeoutError("Authenticaton timed out") from err
        except aiohttp.ClientConnectionError as err:
            if DEBUG_API_ERRORS:
                _LOGGER.error("Authentication request failed: %s", err)
            raise RequestConnectionError("Authentication request failed") from err

    async def _retry_request(
        self, url: str, method="get", retries=API_RETRIES, **kwargs
    ) -> AsyncGenerator[tuple[aiohttp.ClientResponse, TLogExc], None]:
        """API request generator that handles retries. This function handles
        logging and error handling. The generator will yield responses. If the
        request needs to be retried, the caller must call __next__."""

        error: Exception | None = None
        delay: float = 1
        iteration = 0
        for iteration in range(1, retries + 1):
            try:
                # Log the request
                log_req = list(self._request_log(url, method, iteration, **kwargs))
                if DEBUG_API_CALLS:
                    for msg in log_req:
                        _LOGGER.debug(msg)

                # Make the request
                async with self._client.request(
                    method=method, url=url, **kwargs
                ) as response:
                    # Log the response
                    log_resp = [m async for m in self._response_log(response)]
                    if DEBUG_API_CALLS:
                        for msg in log_resp:
                            _LOGGER.debug(msg)

                    # Prepare the exception handler
                    def log_exc(exc: Exception) -> Exception:
                        """Log the exception and return it."""
                        if DEBUG_API_ERRORS:
                            if not DEBUG_API_CALLS:
                                for msg in log_req + log_resp:
                                    _LOGGER.debug(msg)
                            _LOGGER.error(exc)
                        return exc

                    # Let the caller handle the response. If the caller
                    # calls __next__ on the generator the request will be
                    # retried.
                    yield response, log_exc

                # Implement exponential backoff with jitter and sleep before
                # retying the request.
                delay = delay * API_RETRY_FACTOR
                delay = random.normalvariate(delay, delay * API_RETRY_JITTER)
                delay = min(delay, self._max_time)
                if DEBUG_API_CALLS:
                    _LOGGER.debug("Sleeping for %s seconds", delay)
                await asyncio.sleep(delay)

            # Exceptions that can be retried
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as err:
                error = err  # Capture tha last error
                if DEBUG_API_ERRORS:
                    _LOGGER.error(
                        "Request to %s failed (attempt %s): %s: %s",
                        url,
                        iteration,
                        type(err).__qualname__,
                        err,
                    )

        # Arriving after retrying too many times.

        if isinstance(error, asyncio.TimeoutError):
            raise RequestTimeoutError(
                f"Request to {url} timed out after {iteration} retries"
            ) from None

        if isinstance(error, aiohttp.ClientConnectionError):
            raise RequestConnectionError(
                f"Request to {url} failed after {iteration} retries: {error}"
            ) from None

        raise RequestRetryError(
            f"Request to {url} failed after {iteration} retries"
        ) from None

    async def _refresh_token(self):
        # So for some reason they used grant_type password..
        # what the point with oauth then? Anyway this is valid for 24 hour
        p = {
            "username": self._username,
            "password": self._password,
            "grant_type": "password",
        }
        if DEBUG_API_CALLS:
            _LOGGER.debug("@@@  REFRESH TOKEN")

        # Run the _retry_request() in a context manager that will close the
        # generator when the context is exited, ensuring the request and
        # connection is closed when done.
        async with aclosing(
            self._retry_request(
                TOKEN_URL,
                method="post",
                data=p,
                retries=API_RETRIES,
                timeout=self._timeout,
            )
        ) as ctx:
            # Each iteration is a new request. resp is the response object, while
            # log_exc is a callback that will log the exception if the request
            # fails.
            async for response, log_exc in ctx:
                if response.status == 200:
                    data = await response.json()
                    # The data includes the time the access token expires
                    # atm we just ignore it and refresh token when needed.
                    self._token_info.update(data)
                    self._access_token = data.get("access_token")
                    if DEBUG_API_CALLS:
                        _LOGGER.debug("     TOKEN OK")
                    return

                elif response.status == 400:
                    data = await response.json()
                    raise log_exc(
                        AuthenticationError(
                            f"Failed to authenticate. {data.get('error_description', '')}"
                        )
                    )

                raise log_exc(
                    RequestError(
                        f"POST request to {TOKEN_URL} failed with status {response.status}: {response}",
                        response.status,
                    )
                )

    async def _request(self, url: str, method="get", data=None):
        """Make a request to the API."""

        full_url = API_URL + url
        kwargs = {
            "timeout": self._timeout,
            "headers": {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        }
        if data is not None:
            kwargs["json"] = data

        # Run the _retry_request() in a context manager that will close the
        # generator when the context is exited, ensuring the request and
        # connection is closed when done.
        async with aclosing(
            self._retry_request(
                full_url,
                method=method,
                retries=API_RETRIES,
                **kwargs,
            )
        ) as ctx:
            # Each iteration is a new request. resp is the response object, while
            # log_exc is a callback that will log the exception if the request
            # fails.
            async for response, log_exc in ctx:
                if response.status == 401:  # Unauthorized
                    await self._refresh_token()
                    kwargs["headers"]["Authorization"] = f"Bearer {self._access_token}"
                    continue  # Retry request

                elif response.status == 204:  # No content
                    content = await response.read()
                    return content

                elif response.status == 200:  # OK
                    # Read the JSON payload
                    try:
                        json_result = await response.json(content_type=None)
                    except json.JSONDecodeError as err:
                        raise log_exc(
                            RequestDataError(f"Failed to decode json: {err}"),
                        ) from err

                    # Validate the incoming json data
                    try:
                        validate(json_result, url=url)
                    except pydantic.ValidationError as err:
                        raise log_exc(
                            RequestDataError(f"Failed to validate data: {err}"),
                        ) from err

                    return json_result

                error = RequestError(
                        f"{method.upper()} request to {full_url} failed with status {response.status}: {response}",
                        response.status,
                )

                if response.status == 500:  # Internal server error
                    # Zaptec cloud often delivers this error code.
                    log_exc(error)  # Error is not raised, this for logging
                    continue  # Retry request

                # All other error codes will be raised
                raise log_exc(error)

    #   API METHODS DONE
    # =======================================================================

    async def build(self):
        """Make the python interface."""
        _LOGGER.debug("Discover and build hierarchy")

        # Get the API constants
        const = await self._request("constants")
        ZCONST.clear()
        ZCONST.update(const)

        # Get list of installations
        installations = await self._request("installation")

        installs = []
        for data in installations["Data"]:
            _LOGGER.debug("  Installation %s", data["Id"])
            inst = Installation(data, self)
            self.register(data["Id"], inst)
            await inst.build()
            installs.append(inst)

        self.installations = installs

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

        # Find the charger device types
        device_types = set(
            chg.device_type for chg in self.map.values() if isinstance(chg, Charger)
        )

        # Update the observation, settings and commands ids based on the
        # discovered device types.
        ZCONST.update_ids_from_schema(device_types)

        self.is_built = True

    async def update_states(self, id: str | None = None):
        """Update the state for the given id. If id is None, all"""
        for data in self.map.values():
            if id is None or data.id == id:
                await data.state()

    def update(self, data: TDict):
        """update for the stream. Note build has to called first."""

        cls_id = data.pop("ChargerId", None)
        if cls_id == "00000000-0000-0000-0000-000000000000":
            _LOGGER.debug("Ignoring charger with id 00000000-0000-0000-0000-000000000000")
            return
        elif cls_id is None:
            _LOGGER.warning("Unknown update message %s", data)
            return

        klass = self.map.get(cls_id)
        if klass:
            d = ZaptecBase.state_to_attrs([data], "StateId", ZCONST.observations)
            klass.set_attributes(d)
        else:
            _LOGGER.warning("Got update for unknown charger id %s", cls_id)

    def get_chargers(self):
        """Return a list of all chargers"""
        return [v for v in self.map.values() if isinstance(v, Charger)]


if __name__ == "__main__":
    # Just to execute the script manually with "python -m custom_components.zaptec.api"
    import os
    from pprint import pprint

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")
        acc = Account(
            username,
            password,
        )

        try:
            # Builds the interface.
            await acc.build()

            # Save the constant
            with open("constant.json", "w") as outfile:
                json.dump(dict(ZCONST), outfile, indent=2)

            # Update the state to get all the attributes.
            for obj in acc.map.values():
                await obj.state()
                pprint(obj.asdict())

            # with open("data.json", "w") as outfile:

            #     async def cb(data):
            #         print(data)
            #         outfile.write(json.dumps(data, indent=2) + '\n')
            #         outfile.flush()

            #     for ins in acc.installations:
            #         await ins._stream(cb=cb)

        finally:
            await acc._client.close()

    asyncio.run(gogo())
