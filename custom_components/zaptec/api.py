"""Main API for Zaptec."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Mapping
from contextlib import aclosing
import json
import logging
import random
import time
from typing import Any, Protocol

import aiohttp
from aiolimiter import AsyncLimiter
import pydantic

from .const import (
    API_RATELIMIT_MAX_REQUEST_RATE,
    API_RATELIMIT_PERIOD,
    API_RETRIES,
    API_RETRY_FACTOR,
    API_RETRY_INIT_DELAY,
    API_RETRY_JITTER,
    API_RETRY_MAXTIME,
    API_TIMEOUT,
    API_URL,
    CHARGER_EXCLUDES,
    MISSING,
    TOKEN_URL,
    TRUTHY,
)
from .misc import mc_nbfx_decoder, to_under
from .validate import validate
from .zconst import ZConst

_LOGGER = logging.getLogger(__name__)

# Set to True to debug log all API calls
DEBUG_API_CALLS = False
DEBUG_API_DATA = False

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
    """Protocol for logging exceptions."""

    def __call__(self, exc: Exception) -> Exception: ...


class ZaptecApiError(Exception):
    """Base exception for all Zaptec API errors."""


class AuthenticationError(ZaptecApiError):
    """Authenatication failed."""


class RequestError(ZaptecApiError):
    """Failed to get the results from the API."""

    def __init__(self, message, error_code) -> None:
        """Initialize the RequestError."""
        super().__init__(message)
        self.error_code = error_code


class RequestConnectionError(ZaptecApiError):
    """Failed to make the request to the API."""


class RequestTimeoutError(ZaptecApiError):
    """Failed to get the results from the API."""


class RequestRetryError(ZaptecApiError):
    """Retries too many times."""


class RequestDataError(ZaptecApiError):
    """Data is not valid."""


class ZaptecBase(Mapping[str, TValue]):
    """Base class for Zaptec objects."""

    # Type definitions and convertions on the attributes
    ATTR_TYPES: dict[str, Callable] = {}

    def __init__(self, data: TDict, zaptec: Zaptec) -> None:
        """Initialize the ZaptecBase object."""
        self.zaptec: Zaptec = zaptec
        self._attrs: TDict = {}
        self.set_attributes(data)

    # =======================================================================
    #   MAPPING METHODS

    def __getitem__(self, key: str) -> TValue:
        """Get an attribute by name."""
        return self._attrs[to_under(key)]

    def __len__(self) -> int:
        """Return the number of attributes."""
        return len(self._attrs)

    def __iter__(self) -> Iterator[str]:
        """Iterate over the attribute names."""
        return iter(self._attrs)

    @property
    def id(self) -> str:
        """Return the id of the object."""
        return self._attrs["id"]

    @property
    def name(self) -> str:
        """Return the name of the object."""
        return self._attrs["name"]

    @property
    def qual_id(self):
        """Return a qualified name for the object."""
        qn = self.__class__.__qualname__
        if "id" not in self._attrs:
            return qn
        return f"{qn}[{self.id[-6:]}]"

    def asdict(self):
        """Return the attributes as a dict."""
        return self._attrs

    # =======================================================================
    #   UPDATE METHODS

    async def state(self) -> None:
        """Update the state of the object."""

    def set_attributes(self, data: TDict) -> bool:
        """Set the class attributes from the given data."""
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
                    ">>>     Adding %s.%s (%s)  =  <%s> %s",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    new_v,
                )
            elif self._attrs[new_key] != new_v:
                _LOGGER.debug(
                    ">>>     Updating %s.%s (%s)  =  <%s> %s  (was %s)",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    new_v,
                    self._attrs[new_key],
                )
            self._attrs[new_key] = new_v

    @staticmethod
    def state_to_attrs(
        data: Iterable[dict[str, str]],
        key: str,
        keydict: dict[str, str],
        excludes: set[str] = set(),
    ):
        """Convert a list of state data into a dict of attributes.

        `key` is the key that specifies the attribute name. `keydict` is a
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
    """Represents an installation."""

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES = {
        "active": bool,
        "authentication_type": ZCONST.type_authentication_type,
        "current_user_roles": ZCONST.type_user_roles,
        "installation_type": ZCONST.type_installation_type,
        "network_type": ZCONST.type_network_type,
    }

    def __init__(self, data: TDict, zaptec: Zaptec) -> None:
        """Initialize the installation object."""
        super().__init__(data, zaptec)
        self.connection_details = None
        self.chargers: list[Charger] = []
        self._stream_task = None
        self._stream_receiver = None
        self._stream_running = False

    async def build(self):
        """Build the installation object hierarchy."""

        # Get the hierarchy of circurits and chargers
        try:
            hierarchy = await self.zaptec.request(f"installation/{self.id}/hierarchy")
        except RequestError as err:
            if err.error_code == 403:
                _LOGGER.warning(
                    "Access denied to installation hierarchy of %s. The user might not have access",
                    self.id,
                )
                self.chargers = []
                return
            raise

        self.chargers = []
        for circuit in hierarchy["Circuits"]:
            _LOGGER.debug("    Circuit %s", circuit["Id"])
            for charger_item in circuit["Chargers"]:

                # Inject additional attributes
                charger_item["InstallationId"] = self.id  # So the relationship is ready at build
                charger_item["CircuitId"] = circuit["Id"]
                charger_item["CircuitName"] = circuit["Name"]
                charger_item["CircuitMaxCurrent"] = circuit["MaxCurrent"]

                # Add or update the charger
                if charger_item["Id"] in self.zaptec:
                    _LOGGER.debug("      Charger %s  (existing)", charger_item["Id"])
                    charger: Charger = self.zaptec[charger_item["Id"]]
                    charger.set_attributes(charger_item)
                else:
                    _LOGGER.debug("      Charger %s", charger_item["Id"])
                    charger = Charger(charger_item, self.zaptec, installation=self)
                    self.zaptec.register(charger_item["Id"], charger)

                self.chargers.append(charger)

    async def state(self):
        """Update the installation state."""
        _LOGGER.debug(
            "Polling state for %s (%s)", self.qual_id, self._attrs.get("name")
        )
        data = await self.installation_info()
        self.set_attributes(data)

    #   STREAM METHODS
    # =======================================================================

    async def live_stream_connection_details(self):
        """Get the live stream connection details for the installation."""
        # NOTE: API call deprecated
        data = await self.zaptec.request(
            f"installation/{self.id}/messagingConnectionDetails"
        )
        self.connection_details = data
        return data

    async def stream(self, cb=None, ssl_context=None) -> asyncio.Task | None:
        """Kickoff the steam in the background."""
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus.exceptions import ServiceBusError
        except ImportError:
            _LOGGER.debug("Azure Service bus is not available. Resolving to polling")
            return None

        await self.cancel_stream()
        self._stream_task = asyncio.create_task(
            self.stream_main(cb=cb, ssl_context=ssl_context)
        )
        return self._stream_task

    async def stream_main(self, cb=None, ssl_context=None):
        """Main stream handler."""
        try:
            try:
                from azure.servicebus.aio import ServiceBusClient
            except ImportError:
                _LOGGER.warning(
                    "Azure Service bus is not available. Resolving to polling"
                )
                return

            # Already running?
            if self._stream_running:
                raise RuntimeError(
                    "Stream already running. Call cancel_stream() before starting a new stream."
                )
            self._stream_running = True

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
                f"Endpoint=sb://{conf['Host']}/;"
                f"SharedAccessKeyName={conf['Username']};"
                f"SharedAccessKey={conf['Password']}"
            )
            kw = {}
            if ssl_context:
                kw["ssl_context"] = ssl_context
            servicebus_client = ServiceBusClient.from_connection_string(
                conn_str=constr, **kw
            )
            obfuscated = constr.replace(conf["Password"], "********").replace(
                conf["Username"], "********"
            )
            _LOGGER.debug("Connecting to servicebus using %s", obfuscated)

            self._stream_receiver = None
            async with servicebus_client:
                receiver = await asyncio.to_thread(
                    servicebus_client.get_subscription_receiver,
                    topic_name=conf["Topic"],
                    subscription_name=conf["Subscription"],
                )
                _LOGGER.info("Running service bus stream for %s", self.qual_id)
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
                                json_log["StateId"] = (
                                    f"{json_log['StateId']} ({ZCONST.observations.get(json_log['StateId'])})"
                                )
                            _LOGGER.debug("---   Subscription: %s", json_log)

                            # Send result to the stream update method.
                            self.stream_update(json_result)

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
            # Cleanup
            self._stream_receiver = None
            self._stream_running = False
            _LOGGER.info("Servicebus stream stopped for %s", self.qual_id)

    async def stream_close(self):
        """Close the stream receiver."""
        from azure.servicebus.exceptions import ServiceBusError

        try:
            if self._stream_receiver is not None:
                await self._stream_receiver.close()
        except ServiceBusError:
            # This happens if the receiver is in the process of setting up
            # or closing when are trying to close it.
            pass

    async def cancel_stream(self):
        """Cancel the running stream task."""
        if self._stream_task is not None:
            await self.stream_close()
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            finally:
                self._stream_task = None

    def stream_update(self, data: TDict):
        """Streamm event callback."""

        charger_id = data.pop("ChargerId", None)
        if charger_id is None:
            _LOGGER.warning("Unknown update message %s", data)
            return

        if charger_id == "00000000-0000-0000-0000-000000000000":
            _LOGGER.debug("Ignoring charger with id %s", charger_id)
            return

        try:
            # Assumes that the stream only contain chargers that belong to
            # this installation.
            charger = next(chg for chg in self.chargers if chg.id == charger_id)
        except StopIteration:
            _LOGGER.warning("Got update for unknown charger, id %s", charger_id)
            return

        d = ZaptecBase.state_to_attrs([data], "StateId", ZCONST.observations)
        charger.set_attributes(d)

    #   API METHODS
    # =======================================================================

    async def installation_info(self) -> TDict:
        """Raw request for installation data."""

        # Get the installation data
        data = await self.zaptec.request(f"installation/{self.id}")

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
        """Set current limit for the installation.

        Set a limit now how many amps the installation can use
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

        data = await self.zaptec.request(
            f"installation/{self.id}/update", method="post", data=kwargs
        )
        return data

    async def set_authentication_required(self, required: bool):
        """Set if authorization is required for charging."""

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
        result = await self.zaptec.request(
            f"installation/{self.id}", method="put", data=data
        )
        return result


class Charger(ZaptecBase):
    """Represents a charger."""

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES = {
        "active": bool,
        "authentication_required": lambda x: x in TRUTHY,
        "authentication_type": ZCONST.type_authentication_type,
        "charge_current_installation_max_limit": float,
        "charger_max_current": float,
        "charger_min_current": float,
        "charger_operation_mode": ZCONST.type_charger_operation_mode,
        "circuit_id": str,
        "circuit_max_current": float,
        "circuit_name": str,
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
        self, data: TDict, zaptec: Zaptec, installation: Installation | None = None
    ) -> None:
        """Initialize the Charger object."""
        super().__init__(data, zaptec)

        self.installation = installation

    async def state(self):
        """Update the charger state."""
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
            chargers = await self.zaptec.request("chargers")
            for chg in chargers["Data"]:
                if chg["Id"] == self.id:
                    self.set_attributes(chg)
                    break

        # Get the state from the charger
        try:
            state = await self.zaptec.request(f"chargers/{self.id}/state")
            data = self.state_to_attrs(
                state, "StateId", ZCONST.observations, excludes=CHARGER_EXCLUDES
            )
            self.set_attributes(data)
        except RequestError as err:
            if err.error_code != 403:
                raise
            _LOGGER.debug("Access denied to charger %s state", self.id)

        # Firmware version is called. SmartMainboardSoftwareApplicationVersion,
        # stateid 908
        # I couldn't find a way to see if it was up to date..
        # maybe remove this later if it dont interest ppl.

        if (installation_id := self["installation_id"]) in self.zaptec:
            try:
                firmware_info = await self.zaptec.request(
                    f"chargerFirmware/installation/{installation_id}"
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

    #   API METHODS
    # =======================================================================

    async def charger_info(self) -> TDict:
        """Get the charger info."""
        data = await self.zaptec.request(f"chargers/{self.id}")
        return data

    async def command(self, command: str | int):
        """Send a command to the charger."""

        if command == "authorize_charge":
            return await self.authorize_charge()

        self.is_command_valid(command, raise_value_error_if_invalid=True)

        if isinstance(command, int):
            # If int, look up the command name
            cmdid = command
            command = ZCONST.commands.get(command)
        else:
            cmdid = ZCONST.commands.get(command)

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        data = await self.zaptec.request(
            f"chargers/{self.id}/SendCommand/{cmdid}", method="post"
        )
        return data

    def is_command_valid(
        self, command: str | int, raise_value_error_if_invalid=False
    ) -> bool:
        """Check if the command is valid."""

        # Fetching the name from the ZCONST is perhaps not a good idea if Zaptec is changing them.
        if command not in ZCONST.commands and command != "authorize_charge":
            if raise_value_error_if_invalid:
                raise ValueError(f"Unknown command '{command}'")
            return False

        if isinstance(command, int):
            # If int, look up the command name
            command = ZCONST.commands.get(command)

        valid_command = True
        msg = ""
        if command in ["resume_charging", "stop_charging_final"]:
            # Pause/stop or resume charging are only allowed in certain states, see comments on
            # commands 506+507 in https://api.zaptec.com/help/index.html#/Charger/Charger_SendCommand_POST
            operation_mode = self.get("ChargerOperationMode")
            final_stop_active = self.get("FinalStopActive")
            paused = (
                operation_mode == "Connected_Finished" and int(final_stop_active) == 1
            )
            if command == "stop_charging_final" and (
                paused or operation_mode == "Disconnected"
            ):
                msg = "Pause/stop charging is not allowed if charging is already paused or disconnected"
                valid_command = False
            elif command == "resume_charging" and not paused:
                # should also check for NextScheduleEvent, but API doc is difficult to interpret
                msg = "Resume charging is not allowed if charger is not paused"
                valid_command = False

        if valid_command:
            return True
        if raise_value_error_if_invalid:
            _LOGGER.warning(msg)
            _LOGGER.debug(
                "operation_mode: %s, final_stop_active: %s",
                operation_mode,
                final_stop_active,
            )
            raise ValueError(msg)
        return False

    async def set_settings(self, settings: dict[str, Any]):
        """Set settings on the charger."""

        if any(key not in ZCONST.update_params for key in settings.keys()):
            raise ValueError(f"Unknown setting '{settings}'")

        _LOGGER.debug("Settings %s", settings)
        data = await self.zaptec.request(
            f"chargers/{self.id}/update", method="post", data=settings
        )
        return data

    async def stop_charging_final(self):
        """Send stop charging command."""
        return await self.command("stop_charging_final")

    async def resume_charging(self):
        """Send resume charging command."""
        return await self.command("resume_charging")

    async def deauthorize_and_stop(self):
        """Deauthorize the charger and stop it."""
        return await self.command("deauthorize_and_stop")

    async def restart_charger(self):
        """Restart the charger."""
        return await self.command("restart_charger")

    async def upgrade_firmware(self):
        """Send command to upgrade firmware."""
        return await self.command("upgrade_firmware")

    async def authorize_charge(self):
        """Authorize the charger to charge."""
        _LOGGER.debug("Authorize charge")
        # NOTE: Undocumented API call
        data = await self.zaptec.request(
            f"chargers/{self.id}/authorizecharge", method="post"
        )
        return data

    async def set_permanent_cable_lock(self, lock: bool):
        """Set the permanent cable lock on the charger."""
        _LOGGER.debug("Set permanent cable lock %s", lock)
        data = {
            "Cable": {
                "PermanentLock": lock,
            },
        }
        # NOTE: Undocumented API call
        result = await self.zaptec.request(
            f"chargers/{self.id}/localSettings", method="post", data=data
        )
        return result

    async def set_hmi_brightness(self, brightness: float):
        """Set the HMI brightness."""
        _LOGGER.debug("Set HMI brightness %s", brightness)
        data = {
            "Device": {
                "HmiBrightness": brightness,
            },
        }
        # NOTE: Undocumented API call
        result = await self.zaptec.request(
            f"chargers/{self.id}/localSettings", method="post", data=data
        )
        return result


class Zaptec(Mapping[str, ZaptecBase]):
    """This class represent a Zaptec account."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        client: aiohttp.ClientSession | None = None,
        max_time: float = API_RETRY_MAXTIME,
    ) -> None:
        """Initialize the Zaptec account handler."""
        self._username = username
        self._password = password
        self._client = client or aiohttp.ClientSession()
        self._client_internal = client is None
        self._token_info = {}
        self._access_token = None
        self._map: dict[str, ZaptecBase] = {}
        self._timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        self._max_time = max_time
        self._ratelimiter = AsyncLimiter(
            max_rate=API_RATELIMIT_MAX_REQUEST_RATE, time_period=API_RATELIMIT_PERIOD
        )

        self.is_built: bool = False
        """Flag to indicate if the structure of objectes is built and ready to use."""

    async def __aenter__(self) -> Zaptec:
        """Enter the context manager."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Exit the context manager."""
        if self._client_internal:
            # If the client was created internally, close it
            await self._client.close()
        return False

    # =======================================================================
    #   MAPPING METHODS

    def __getitem__(self, id: str) -> ZaptecBase:
        """Get an object data by id."""
        return self._map[id]

    def __iter__(self) -> Iterator[str]:
        """Return an iterator over the object ids."""
        return iter(self._map)

    def __len__(self) -> int:
        """Return the number of registered objects."""
        return len(self._map)

    def __contains__(self, key: str | ZaptecBase) -> bool:
        """Check if an object with the given id is registered."""
        # Overload the default implementation to support checking of objects using "in" operator.
        if isinstance(key, ZaptecBase):
            for obj in self._map.values():
                if obj is key:
                    return True
            return False
        return key in self._map

    def register(self, id: str, data: ZaptecBase):
        """Register an object data with id."""
        if id in self._map:
            raise ValueError(
                f"Object with id {id} already registered. "
                "Use unregister() to remove it first."
            )
        self._map[id] = data

    def unregister(self, id: str):
        """Unregister an object data with id."""
        del self._map[id]

    def objects(self) -> Iterable[ZaptecBase]:
        """Return an iterable of all registered objects."""
        return self._map.values()

    @property
    def installations(self) -> Iterable[Installation]:
        """Return a list of all installations."""
        return [v for v in self._map.values() if isinstance(v, Installation)]

    @property
    def chargers(self) -> Iterable[Charger]:
        """Return a list of all chargers."""
        return [v for v in self._map.values() if isinstance(v, Charger)]

    # =======================================================================
    #   REQUEST METHODS

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
            if not DEBUG_API_DATA:
                return
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
            if not DEBUG_API_DATA:
                return
            yield f"     headers {dict((k, v) for k, v in resp.headers.items())}"
            if not contents:
                return
            if resp.status != 200:
                yield f"     data '{await resp.text()}'"
            else:
                yield f"     json '{await resp.json(content_type=None)}'"
        except Exception:
            _LOGGER.exception("Failed to log response (ignored exception)")

    async def _request_worker(
        self, url: str, method="get", retries=API_RETRIES, **kwargs
    ) -> AsyncGenerator[tuple[aiohttp.ClientResponse, TLogExc], None]:
        """API request generator that handles retries.

        This function handles logging and error handling. The generator will
        yield responses. If the request needs to be retried, the caller must
        call __next__.
        """

        error: Exception | None = None
        delay: float = API_RETRY_INIT_DELAY
        iteration = 0
        for iteration in range(1, retries + 1):
            try:
                # Log the request
                log_req = list(self._request_log(url, method, iteration, **kwargs))
                if DEBUG_API_CALLS:
                    for msg in log_req:
                        _LOGGER.debug(msg)

                # Capture the current time
                start_time = time.perf_counter()

                # Make the request
                async with self._ratelimiter:
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

                # If the sleep time is negative, it means the request took
                # longer than the wanted delay, so we don't need to sleep.
                sleep_delay = delay - time.perf_counter() + start_time
                if sleep_delay > 0:
                    if DEBUG_API_CALLS:
                        _LOGGER.debug("Sleeping for %1.1f seconds", sleep_delay)
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

    async def login(self) -> None:
        """Login to the Zaptec API and get an access token."""
        await self._refresh_token()

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

        # Run the _request_worker() in a context manager that will close the
        # generator when the context is exited, ensuring the request and
        # connection is closed when done.
        async with aclosing(
            self._request_worker(
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

    async def request(self, url: str, method="get", data=None):
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

        # Run the _request_worker() in a context manager that will close the
        # generator when the context is exited, ensuring the request and
        # connection is closed when done.
        async with aclosing(
            self._request_worker(
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

    # =======================================================================
    #   UPDATE METHODS

    async def build(self):
        """Make the python interface."""
        _LOGGER.debug("Discover and build hierarchy")

        # Get the API constants
        const = await self.request("constants")
        ZCONST.clear()
        ZCONST.update(const)

        # Get list of installations
        installations = await self.request("installation")

        for inst_item in installations["Data"]:
            # Add or update the installation object.
            if inst_item["Id"] in self:
                _LOGGER.debug("  Installation %s  (existing)", inst_item["Id"])
                installation = self[inst_item["Id"]]
                installation.set_attributes(inst_item)
            else:
                _LOGGER.debug("  Installation %s", inst_item["Id"])
                installation = Installation(inst_item, self)
                self.register(inst_item["Id"], installation)

            await installation.build()

        # Check for installations that are no longer available
        new_installations = {d["Id"] for d in installations["Data"]}
        have_installations = {o.id for o in self.installations}
        if missing_installations := (have_installations - new_installations):
            _LOGGER.warning(
                "These installations are no longer available but remain in use: %s",
                missing_installations,
            )
            _LOGGER.warning("To remove them, please restart the integration.")

        # Check for standalone charger or chargers that are not available any more.
        chargers = await self.request("chargers")
        new_chargers = {d["Id"] for d in chargers["Data"]}
        have_chargers = {c.id for c in self.chargers}
        if missing_chargers := (have_chargers - new_chargers):
            _LOGGER.warning(
                "These chargers are no longer available: %s", missing_chargers
            )
            _LOGGER.warning("To remove them, please restart the integration.")
        if extra_chargers := (new_chargers - have_chargers):
            _LOGGER.warning(
                "These standalone chargers will not be added: %s", extra_chargers
            )

        # Update the observation, settings and commands ids based on the
        # discovered device types.
        ZCONST.update_ids_from_schema({chg["device_type"] for chg in self.chargers})

        self.is_built = True

    async def update_states(self, id: str | None = None):
        """Update the state for the given id or update all."""
        for obj in self.objects():
            if id is None or obj.id == id:
                await obj.state()


if __name__ == "__main__":
    # Just to execute the script manually with "python -m custom_components.zaptec.api"
    import os
    from pprint import pprint

    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("azure").setLevel(logging.WARNING)

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")

        async with Zaptec(username, password) as zaptec:
            # Builds the interface.
            await zaptec.login()
            await zaptec.build()
            await zaptec.update_states()

            # Print all the attributes.
            for obj in zaptec.objects():
                pprint(obj.asdict())

    asyncio.run(gogo())
