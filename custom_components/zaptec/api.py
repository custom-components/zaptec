"""Main API for Zaptec."""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from concurrent.futures import CancelledError
from typing import Any, AsyncGenerator, Callable, Protocol

import aiohttp
import pydantic

from .const import API_RETRIES, API_TIMEOUT, API_URL, MISSING, TOKEN_URL, TRUTHY
from .misc import mc_nbfx_decoder, to_under
from .validate import validate

"""
stuff are missing from the api docs compared to what the portal uses.
circuits/{self.id}/live
circuits/{self.id}/
https://api.zaptec.com/api/dashboard/activechargersforowner?limit=250
/dashbord
signalr is used by the website.
"""

# pylint: disable=missing-function-docstring

# Type definitions
TValue = str | int | float | bool
TDict = dict[str, TValue]


class TLogExc(Protocol):
    """Protocol for logging exceptions"""

    def __call__(self, exc: Exception) -> Exception:
        ...


_LOGGER = logging.getLogger(__name__)

# Set to True to debug log all API calls
DEBUG_API_CALLS = False

# Set to True to debug log all API errors
DEBUG_API_ERRORS = True


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


#
# Attribute type converters
#
def type_ocmf(data):
    """Open Charge Metering Format (OCMF) type"""
    # https://github.com/SAFE-eV/OCMF-Open-Charge-Metering-Format/blob/master/OCMF-en.md
    sects = data.split("|")
    if len(sects) not in (2, 3) or sects[0] != "OCMF":
        raise ValueError(f"Invalid OCMF data: {data}")
    data = json.loads(sects[1])
    return data


def type_completed_session(data):
    """Convert the CompletedSession to a dict"""
    data = json.loads(data)
    if "SignedSession" in data:
        data["SignedSession"] = type_ocmf(data["SignedSession"])
    return data


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
                new_v = self.ATTR_TYPES.get(new_key, lambda x: x)(v)
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
                qn = self.__class__.__qualname__
                _LOGGER.debug(
                    ">>>   Adding %s.%s (%s)  =  <%s> %s", qn, new_key, k, new_vt, new_v
                )
            elif self._attrs[new_key] != new_v:
                qn = self.__class__.__qualname__
                _LOGGER.debug(
                    ">>>   Updating %s.%s (%s)  =  <%s> %s  (was %s)",
                    qn,
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
            "Polling state for %s installation (%s)", self.id, self._attrs.get("name")
        )
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
                _LOGGER.warning(
                    "Azure Service bus is not available. Resolving to polling"
                )
                # https://github.com/custom-components/zaptec/issues
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
            servicebus_client = ServiceBusClient.from_connection_string(conn_str=constr)
            _LOGGER.debug("Connecting to servicebus using %s", constr)

            self._stream_receiver = None
            async with servicebus_client:
                receiver = servicebus_client.get_subscription_receiver(
                    topic_name=conf["Topic"], subscription_name=conf["Subscription"]
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
                                ] = f"{json_log['StateId']} ({self._account._obs_ids.get(json_log['StateId'])})"
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

    async def set_authenication_required(self, required: bool):
        """Set if authorization is required for charging"""

        # Undocumented feature, but the WEB API uses it. It fetches the
        # installation data and updates IsAuthorizationRequired field
        data = {
            "Id": self.id,
            "IsRequiredAuthentication": required,
        }
        result = await self._account._request(
            f"installation/{self.id}", method="put", data=data
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
            "Polling state for %s cicuit (%s)", self.id, self._attrs.get("name")
        )
        data = await self.circuit_info()
        self.set_attributes(data)

    # -----------------
    # API methods
    # -----------------

    async def circuit_info(self) -> TDict:
        """Raw request for circuit data"""
        data = await self._account._request(f"circuits/{self.id}")
        return data


class Charger(ZaptecBase):
    """Represents a charger"""

    # Type conversions for the named attributes
    ATTR_TYPES = {
        "active": bool,
        "charge_current_installation_max_limit": float,
        "charger_max_current": float,
        "charger_min_current": float,
        "completed_session": type_completed_session,
        "current_phase1": float,
        "current_phase2": float,
        "current_phase3": float,
        "is_authorization_required": lambda x: x in TRUTHY,
        "is_online": lambda x: x in TRUTHY,
        "permanent_cable_lock": lambda x: x in TRUTHY,
        "signed_meter_value": type_ocmf,
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

        # Append the attr types that depends on self
        attr_types = self.ATTR_TYPES.copy()
        attr_types.update(
            {
                "operating_mode": self.type_operation_mode,
                "charger_operation_mode": self.type_operation_mode,
            }
        )
        self.ATTR_TYPES = attr_types

    async def build(self) -> None:
        """Build the object"""

        # Don't update state at build, because the state and settings ids
        # is not loaded yet.

    async def state(self):
        """Update the charger state"""
        _LOGGER.debug(
            "Polling state for %s charger (%s)", self.id, self._attrs.get("name")
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
            data = Account._state_to_attrs(state, "StateId", self._account._obs_ids)
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
            data = Account._state_to_attrs(
                settings.values(), "SettingId", self._account._set_ids
            )
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
        data = await self._account._request(f"chargers/{self.id}/live")
        # FIXME: Missing validator (see validate)
        return data

    def type_operation_mode(self, v):
        modes = {
            str(v): k for k, v in self._account._const["ChargerOperationModes"].items()
        }
        return modes.get(str(v), str(v))

    # -----------------
    # API methods
    # -----------------

    async def charger_info(self) -> TDict:
        data = await self._account._request(f"chargers/{self.id}")
        return data

    async def command(self, command: str):
        """Send a command to the charger"""

        if command == "authorize_charge":
            return await self.authorize_charge()

        # Fetching the name from the const is perhaps not a good idea
        # if Zaptec is changing them.
        cmdid = self._account._cmd_ids.get(command)
        if cmdid is None:
            raise ValueError(f"Unknown command {command}")

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        data = await self._account._request(
            f"chargers/{self.id}/SendCommand/{cmdid}", method="post"
        )
        return data

    async def set_settings(self, settings: dict[str, Any]):
        """Set settings on the charger"""

        set_ids = self._account._set_ids
        values = [{"id": set_ids.get(k), "value": v} for k, v in settings.items()]

        if any(d for d in values if d["id"] is None):
            raise ValueError(f"Unknown setting '{settings}'")

        _LOGGER.debug("Settings %s", settings)
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
        data = await self._account._request(
            f"chargers/{self.id}/authorizecharge", method="post"
        )
        return data

    async def set_current_in_minimum(self, value):
        return await self.set_settings({"current_in_minimum": value})

    async def set_current_in_maxium(self, value):
        return await self.set_settings({"current_in_maximum": value})


class Account:
    """This class represent an zaptec account"""

    def __init__(
        self, username: str, password: str, client: aiohttp.ClientSession | None = None
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
        self._const = {}
        self._obs_ids = {}
        self._set_ids = {}
        self._cmd_ids = {}
        self.is_built = False
        self._timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)

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
            _LOGGER.error("Authentication timeout")
            raise RequestTimeoutError("Authenticaton timed out") from err
        except aiohttp.ClientConnectionError as err:
            _LOGGER.error("Authentication request failed: %s", err)
            raise RequestConnectionError("Authentication request failed") from err

    async def _retry_request(
        self, url: str, method="get", **kwargs
    ) -> AsyncGenerator[tuple[aiohttp.ClientResponse, TLogExc], None]:
        """API request generator that handles retries. This function handles
        logging and error handling. The generator will yield responses. If the
        request needs to be retried, the caller must call __next__."""

        error: Exception | None = None
        iteration = 0
        for iteration in range(1, API_RETRIES + 1):
            try:
                # Log the request
                log_req = list(self._request_log(url, method, iteration, **kwargs))
                if DEBUG_API_CALLS:
                    for msg in log_req:
                        _LOGGER.debug(msg)

                # Make the request
                async with self._client.request(
                    method=method, url=url, **kwargs
                ) as resp:
                    # Log the response
                    log_resp = [m async for m in self._response_log(resp)]
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
                    yield resp, log_exc

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
            ) from error

        if isinstance(error, aiohttp.ClientConnectionError):
            raise RequestConnectionError(
                f"Request to {url} failed after {iteration} retries: {error}"
            ) from error

        raise RequestRetryError(
            f"Request to {url} failed after {iteration} retries"
        ) from error

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

        async for resp, log_exc in self._retry_request(
            TOKEN_URL, method="post", data=p, timeout=self._timeout
        ):
            if resp.status == 200:
                data = await resp.json()
                # The data includes the time the access token expires
                # atm we just ignore it and refresh token when needed.
                self._token_info.update(data)
                self._access_token = data.get("access_token")
                if DEBUG_API_CALLS:
                    _LOGGER.debug("     TOKEN OK")
                return

            elif resp.status == 400:
                data = await resp.json()
                raise log_exc(
                    AuthenticationError(
                        f"Failed to authenticate. {data.get('error_description', '')}"
                    )
                )

            raise log_exc(
                RequestError(
                    f"POST request to {TOKEN_URL} failed with status {resp.status}: {resp}",
                    resp.status,
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

        async for resp, log_exc in self._retry_request(
            full_url, method=method, **kwargs
        ):
            # The log_exc callback is a helper that will log the failing request

            if resp.status == 401:  # Unauthorized
                await self._refresh_token()
                kwargs["headers"]["Authorization"] = f"Bearer {self._access_token}"
                continue  # Retry request

            elif resp.status == 204:  # No content
                content = await resp.read()
                return content

            elif resp.status == 200:  # OK
                # Read the JSON payload
                try:
                    json_result = await resp.json(content_type=None)
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

            raise log_exc(
                RequestError(
                    f"{method.upper()} request to {full_url} failed with status {resp.status}: {resp}",
                    resp.status,
                )
            )

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

        if not self._const:
            # Get the API constants
            self._const = await self._request("constants")

            # Find the chargers device types
            device_types = set(
                chg.device_type for chg in self.map.values() if isinstance(chg, Charger)
            )

            # Define the remaps
            self._obs_ids = Account._get_remap(
                self._const, ["Observations", "ObservationIds"], device_types
            )
            self._set_ids = Account._get_remap(
                self._const, ["Settings", "SettingIds"], device_types
            )
            self._cmd_ids = Account._get_remap(
                self._const, ["Commands", "CommandIds"], device_types
            )

            # Commands can also be specified as lower case strings
            self._cmd_ids.update(
                {to_under(k): v for k, v in self._cmd_ids.items() if isinstance(k, str)}
            )

        self.is_built = True

    async def update_states(self, id: str | None = None):
        """Update the state for the given id. If id is None, all"""
        for data in self.map.values():
            if id is None or data.id == id:
                await data.state()

    def update(self, data: TDict):
        """update for the stream. Note build has to called first."""

        cls_id = data.pop("ChargerId", None)
        if cls_id is not None:
            klass = self.map.get(cls_id)
            if klass:
                d = Account._state_to_attrs([data], "StateId", self._obs_ids)
                klass.set_attributes(d)
            else:
                _LOGGER.warning("Got update for unknown charger id %s", cls_id)
        else:
            _LOGGER.warning("Unknown update message %s", data)

    def get_chargers(self):
        """Return a list of all chargers"""
        return [v for v in self.map.values() if isinstance(v, Charger)]

    @staticmethod
    def _get_remap(const, wanted, device_types=None) -> dict:
        """Parse the given zaptec constants record `const` and generate
        a remap dict for the given `wanted` keys. If `device_types` is
        specified, the entries for these device schemas will be merged
        with the main remap dict.
        Example:
            _get_remap(const, ["Observations", "ObservationIds"], [4])
        """
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
    def _state_to_attrs(
        data: Iterable[dict[str, str]], key: str, keydict: dict[str, str]
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
            value = item.get("Value", item.get("ValueAsString", MISSING))
            if value is not MISSING:
                kv = keydict.get(skey, f"{key} {skey}")
                if kv in out:
                    _LOGGER.debug(
                        "Duplicate key %s. Is '%s', new '%s'", kv, out[kv], value
                    )
                out[kv] = value
        return out


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

            #     for ins in acc.installations:
            #         await ins._stream(cb=cb)

        finally:
            await acc._client.close()

    asyncio.run(gogo())
