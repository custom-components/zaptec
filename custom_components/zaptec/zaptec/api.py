"""Main API for Zaptec."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Mapping
from contextlib import aclosing
from http import HTTPStatus
import itertools
import json
import logging
import random
import time
from typing import Any, ClassVar, Protocol, Self

import aiohttp
from aiolimiter import AsyncLimiter
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError
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
    MAX_DEBUG_TEXT_LEN_ON_500,
    MISSING,
    TOKEN_URL,
    TRUTHY,
)
from .exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestDataError,
    RequestError,
    RequestRetryError,
    RequestTimeoutError,
)
from .redact import Redactor
from .utils import mc_nbfx_decoder, to_under
from .validate import validate
from .zconst import ZCONST, CommandType

_LOGGER = logging.getLogger(__name__)

# API debug flags
DEBUG_API_CALLS = True
DEBUG_API_DATA = False
DEBUG_API_EXCEPTIONS = False

# Type definitions
TValue = str | int | float | bool
TDict = dict[str, TValue]


class TLogExc(Protocol):
    """Protocol for logging exceptions."""

    def __call__(self, exc: Exception) -> Exception: ...


class ZaptecBase(Mapping[str, TValue]):
    """Base class for Zaptec objects."""

    # Type definitions and convertions on the attributes
    ATTR_TYPES: ClassVar[dict[str, Callable]] = {}

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
    def qual_id(self) -> str:
        """Return a qualified name for the object."""
        qn = self.__class__.__qualname__
        if "id" not in self._attrs:
            return qn
        return f"{qn}[{self.id[-6:]}]"

    @property
    def model(self) -> str:
        """Return the model of the object."""
        return f"Zaptec {self.__class__.__qualname__}"

    def asdict(self):
        """Return the attributes as a dict."""
        return self._attrs

    # =======================================================================
    #   UPDATE METHODS

    async def poll_info(self) -> None:
        """Poll information about the object."""

    async def poll_state(self) -> None:
        """Poll the state of the object."""

    def set_attributes(self, data: TDict) -> None:
        """Set the class attributes from the given data."""
        redact = self.zaptec.redact
        for k, v in data.items():
            # Cast the value to the correct type
            new_key = to_under(k)
            try:
                # Get the type conversion function and apply it
                type_fn = self.ATTR_TYPES.get(new_key, lambda x: x)
                new_v = type_fn(v)
            except Exception as err:
                _LOGGER.error(
                    "Failed to convert attribute %s (%s) value <%s> %r: %s",
                    k,
                    new_key,
                    type(v).__qualname__,
                    redact(v, key=k),
                    err,
                )
                new_v = v
            new_vt = type(new_v).__qualname__
            if new_key not in self._attrs:
                _LOGGER.debug(
                    ">>>  Adding   %s.%s (%s)  =  <%s> %r",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    redact(new_v, key=k),
                )
            elif self._attrs[new_key] != new_v:
                _LOGGER.debug(
                    ">>>  Updating %s.%s (%s)  =  <%s> %r  (was %r)",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    redact(new_v, key=k),
                    redact(self._attrs[new_key], key=k),
                )
            elif self.zaptec.show_all_updates:
                _LOGGER.debug(
                    ">>>  Ignoring %s.%s (%s)  =  <%s> %r  (no change)",
                    self.qual_id,
                    new_key,
                    k,
                    new_vt,
                    redact(new_v, key=k),
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
                    _LOGGER.debug("Duplicate key %s. Is '%s', new '%s'", kv, out[kv], value)
                out[kv] = value
        return out


class Installation(ZaptecBase):
    """Represents an installation."""

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES: ClassVar[dict[str, Callable]] = {
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
            if not hierarchy:
                # 2025-09-19: It appears Zaptec started returning HTTPStatus.NO_CONTENT instead of
                # HTTPStatus.FORBIDDEN when user doesn't have access.
                _LOGGER.warning(
                    ("No hierarchy returned for installation %s. The user might not have access"),
                    self.qual_id,
                )
                self.chargers = []
                return
        except RequestError as err:
            if err.error_code == HTTPStatus.FORBIDDEN:
                _LOGGER.warning(
                    (
                        "Access denied to installation hierarchy of %s. "
                        "The user might not have access"
                    ),
                    self.qual_id,
                )
                self.chargers = []
                return
            raise

        redact = self.zaptec.redact

        self.chargers = []
        for circuit in hierarchy["Circuits"]:
            ctid = circuit["Id"]
            redact.add_uid(ctid, "Circuit")
            _LOGGER.debug("    Circuit %s", redact(ctid))

            for charger_item in circuit["Chargers"]:
                chgid = charger_item["Id"]
                redact.add_uid(chgid, "Charger")

                # Inject additional attributes
                charger_item["InstallationId"] = self.id
                charger_item["CircuitId"] = ctid
                charger_item["CircuitName"] = circuit["Name"]
                charger_item["CircuitMaxCurrent"] = circuit["MaxCurrent"]

                # Add or update the charger
                if chgid in self.zaptec:
                    _LOGGER.debug("      Charger %s  (existing)", redact(chgid))
                    charger: Charger = self.zaptec[chgid]
                    charger.set_attributes(charger_item)
                else:
                    _LOGGER.debug("      Charger %s  (adding)", redact(chgid))
                    charger = Charger(charger_item, self.zaptec, installation=self)
                    self.zaptec.register(chgid, charger)

                self.chargers.append(charger)

    async def poll_info(self) -> None:
        """Update the installation info."""
        _LOGGER.debug("Poll info from %s (%s)", self.qual_id, self.get("Name"))

        # Get the installation data
        data = await self.zaptec.request(f"installation/{self.id}")

        # Remove data fields with excessive data, making it bigger than the
        # HA database appreciates for the size of attributes.
        # FIXME: SupportGroup is sub dict. This is not within the declared type
        supportgroup = data.get("SupportGroup")
        if supportgroup is not None and "LogoBase64" in supportgroup:
            logo = supportgroup["LogoBase64"]
            supportgroup["LogoBase64"] = f"<Removed, was {len(logo)} bytes>"

        # Set the attributes
        self.set_attributes(data)

    async def poll_firmware_info(self) -> None:
        """Update the installation firmware info."""
        _LOGGER.debug("Poll firmware info from %s (%s)", self.qual_id, self.get("Name"))

        try:
            firmware_info = await self.zaptec.request(f"chargerFirmware/installation/{self.id}")
            for fm in firmware_info:
                charger = self.zaptec.get(fm["ChargerId"])
                if charger is None:
                    continue
                if (
                    fm.get("CurrentVersion") is None
                    or fm.get("AvailableVersion") is None
                    or fm.get("IsUpToDate") is None
                ):
                    # If the charger is already added to the Zaptec platform but not yet
                    # initialized, these fields are not available.
                    _LOGGER.warning(
                        "Missing firmware info for charger %s because the charger hasn't been initialized yet. Safe to ignore.",  # noqa: E501
                        charger.qual_id,
                    )
                    continue

                charger.set_attributes(
                    {
                        "firmware_current_version": fm["CurrentVersion"],
                        "firmware_available_version": fm["AvailableVersion"],
                        "firmware_update_to_date": fm["IsUpToDate"],
                    }
                )
        except RequestError as err:
            if err.error_code != HTTPStatus.FORBIDDEN:
                raise
            _LOGGER.debug("Access denied to installation %s firmware info", self.qual_id)

    #   STREAM METHODS
    # =======================================================================

    async def live_stream_connection_details(self):
        """Get the live stream connection details for the installation."""
        # NOTE: API call deprecated
        data = await self.zaptec.request(f"installation/{self.id}/messagingConnectionDetails")
        self.connection_details = data
        return data

    async def stream(self, cb=None, ssl_context=None) -> asyncio.Task | None:
        """Kickoff the steam in the background."""
        await self.cancel_stream()
        self._stream_task = asyncio.create_task(self.stream_main(cb=cb, ssl_context=ssl_context))
        return self._stream_task

    def _stream_log(self, data: dict[str, Any]) -> None:
        """Log a stream message."""
        if not DEBUG_API_CALLS:
            return
        if isinstance(data, dict):
            if "StateId" in data:
                data["StateId"] = (
                    f"{data['StateId']} ({ZCONST.observations.get(data['StateId'])})"
                )
            # Silenty delete these from logging. They are never used
            data.pop("DeviceId", None)
            data.pop("DeviceType", None)
        _LOGGER.debug("@@@  EVENT %s", self.zaptec.redact(data))

    async def stream_main(self, cb=None, ssl_context=None) -> None:
        """Main stream handler."""
        try:
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
                if err.error_code != HTTPStatus.FORBIDDEN:
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
            servicebus_client = ServiceBusClient.from_connection_string(conn_str=constr, **kw)
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

                            # Log the message
                            self._stream_log(json_result.copy())

                            # Send result to the stream update method.
                            self.stream_update(json_result.copy())

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
            # Cleanup
            self._stream_receiver = None
            self._stream_running = False
            _LOGGER.info("Servicebus stream stopped for %s", self.qual_id)

    async def stream_close(self) -> None:
        """Close the stream receiver."""
        try:
            if self._stream_receiver is not None:
                await self._stream_receiver.close()
        except ServiceBusError:
            # This happens if the receiver is in the process of setting up
            # or closing when are trying to close it.
            pass

    async def cancel_stream(self) -> None:
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

    def stream_update(self, data: TDict) -> None:
        """Stream event callback."""

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

    async def set_limit_current(self, **kwargs):
        """Set current limit for the installation.

        Set a limit now how many amps the installation can use
        Use availableCurrent for setting all phases at once. Use
        availableCurrentPhase* to set each phase individually.
        """
        has_availablecurrent = kwargs.get("availableCurrent") is not None
        has_availablecurrentphases = [
            kwargs.get(k) is not None
            for k in (
                "availableCurrentPhase1",
                "availableCurrentPhase2",
                "availableCurrentPhase3",
            )
        ]
        if not (has_availablecurrent ^ all(has_availablecurrentphases)):
            raise ValueError(
                "Either availableCurrent or all of availableCurrentPhase1, "
                "availableCurrentPhase2, availableCurrentPhase3 must be set"
            )
        if any(has_availablecurrentphases) and not all(has_availablecurrentphases):
            raise ValueError(
                "If any of availableCurrentPhase1, availableCurrentPhase2 and "
                "availableCurrentPhase3 are set, then all of them must be set"
            )

        # Use 32 as default if missing or invalid value.
        try:
            max_current = float(self.get("max_current", 32.0))
        except (TypeError, ValueError):
            max_current = 32.0
        # Make sure the arguments and values are valid
        for k, v in kwargs.items():
            if k not in (
                "availableCurrent",
                "availableCurrentPhase1",
                "availableCurrentPhase2",
                "availableCurrentPhase3",
            ):
                raise TypeError(f"Invalid argument {k!r}")
            if v is None:
                raise ValueError(f"{k} cannot be None")
            if not (0 <= v <= max_current):
                raise ValueError(f"{k} must be between 0 and {max_current:.0f} amps")
        data = await self.zaptec.request(
            f"installation/{self.id}/update", method="post", data=kwargs
        )
        return data

    async def set_three_to_one_phase_switch_current(self, current: float):
        """Set the 3 to 1-phase switch current."""
        if not (0 <= current <= 32):
            raise ValueError("Current must be between 0 and 32 amps")
        data = await self.zaptec.request(
            f"installation/{self.id}/update",
            method="post",
            data={"threeToOnePhaseSwitchCurrent": current},
        )
        return data


class Charger(ZaptecBase):
    """Represents a charger."""

    # Type conversions for the named attributes (keep sorted)
    ATTR_TYPES: ClassVar[dict[str, Callable]] = {
        "active": bool,
        "authentication_required": lambda x: x in TRUTHY,
        "authentication_type": ZCONST.type_authentication_type,
        "charge_current_installation_max_limit": float,
        "charge_current_set": float,
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
        "humidity": float,
        "is_authorization_required": lambda x: x in TRUTHY,
        "is_online": lambda x: x in TRUTHY,
        "network_type": ZCONST.type_network_type,
        "operating_mode": ZCONST.type_charger_operation_mode,
        "permanent_cable_lock": lambda x: x in TRUTHY,
        "signed_meter_value": ZCONST.type_ocmf,
        "temperature_internal5": float,
        "total_charge_power": float,
        "total_charge_power_session": float,
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

    async def poll_info(self) -> None:
        """Refresh the charger data."""
        _LOGGER.debug("Poll info from %s (%s)", self.qual_id, self.get("Name"))

        try:
            # Get the main charger info
            charger = await self.zaptec.request(f"chargers/{self.id}")
            self.set_attributes(charger)
        except RequestError as err:
            # An unprivileged user will get a 403 error, but the user is able
            # to get _some_ info about the charger by getting a list of
            # chargers.
            if err.error_code != HTTPStatus.FORBIDDEN:
                raise
            _LOGGER.debug("Access denied to charger %s, attempting list", self.qual_id)
            chargers = await self.zaptec.request("chargers")
            for chg in chargers["Data"]:
                if chg["Id"] == self.id:
                    self.set_attributes(chg)
                    break

    async def poll_state(self) -> None:
        """Update the charger state."""
        _LOGGER.debug("Poll state from %s (%s)", self.qual_id, self.get("Name"))

        # Get the state from the charger
        try:
            state = await self.zaptec.request(f"chargers/{self.id}/state")
            data = self.state_to_attrs(
                state, "StateId", ZCONST.observations, excludes=CHARGER_EXCLUDES
            )
            self.set_attributes(data)
        except RequestError as err:
            if err.error_code != HTTPStatus.FORBIDDEN:
                raise
            _LOGGER.debug("Access denied to charger %s state", self.qual_id)

    #   API METHODS
    # =======================================================================

    async def command(self, command: str | int | CommandType):
        """Send a command to the charger.

        Any command or command id can be used. Zaptec supports a number of
        commands, which is found https://api.zaptec.com/help/index.html
        under CommandId shema. The most used commands are:

        - deauthorize_and_stop: Deauthorize the charger and stop it
        - restart_charger: Restart the charger
        - resume_charging: Resume charging
        - stop_charging_final: Stop charging and set final stop
        - upgrade_firmware: Upgrade the firmware

        Special commands which is special to this implementation:
        - authorize_charge: Authorize the charger to charge
        """

        if command in ("authorize_charge", "AuthorizeCharge"):
            return await self.authorize_charge()

        # Look up the command and its command id
        if isinstance(command, int):
            # If int, look up the command name
            cmdid = command
            command = ZCONST.commands.get(cmdid)
        else:
            # Support using the CommandName as a string
            cmdid = ZCONST.commands.get(to_under(command))

        # Make sure we have a valid command
        if not cmdid or not command:
            raise ValueError(f"Unknown command {command!r}")

        # Check that we can run the command at this time
        self.is_command_valid(command, raise_value_error_if_invalid=True)

        _LOGGER.debug("Command %s (%s)", command, cmdid)
        data = await self.zaptec.request(f"chargers/{self.id}/SendCommand/{cmdid}", method="post")
        return data

    def is_command_valid(self, command: str, raise_value_error_if_invalid: bool = False) -> bool:
        """Check if the command is valid."""

        valid_command = True
        msg = ""
        if command in ["resume_charging", "stop_charging_final"]:
            # Pause/stop or resume charging are only allowed in certain states, see comments on
            # commands 506+507 in https://api.zaptec.com/help/index.html#/Charger/Charger_SendCommand_POST
            operation_mode = self.get("ChargerOperationMode")
            final_stop_active = self.get("FinalStopActive")
            paused = operation_mode == "Connected_Finished" and int(final_stop_active) == 1
            if command == "stop_charging_final" and (paused or operation_mode == "Disconnected"):
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

    async def authorize_charge(self):
        """Authorize the charger to charge."""
        _LOGGER.debug("Authorize charge")
        # NOTE: Undocumented API call
        data = await self.zaptec.request(f"chargers/{self.id}/authorizecharge", method="post")
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

    def is_charging(self) -> bool:
        """Check if the charger is charging."""
        return self.get("ChargerOperationMode") == "Connected_Charging"

    @property
    def model_prefix(self) -> str:
        """Return the model prefix of the charger.

        In Zaptec charger this is the first 3 characters in the DeviceId.
        """
        device_id: str = self.get("DeviceId", "")
        return device_id[0:3].upper()

    @property
    def model(self) -> str:
        """Return the model of the charger."""
        return ZCONST.serial_to_model.get(self.model_prefix, super().model)


class Zaptec(Mapping[str, ZaptecBase]):
    """Represents a Zaptec account."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        client: aiohttp.ClientSession | None = None,
        max_time: float = API_RETRY_MAXTIME,
        show_all_updates: bool = False,
        redact_logs: bool = True,
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

        self.redact = Redactor(redact_logs)
        """Redactor for sensitive data in logs."""

        self.is_built: bool = False
        """Flag to indicate if the structure of objectes is built and ready to use."""

        self.show_all_updates: bool = show_all_updates
        """Flag to indicate if all updates should be logged, even if no changes."""

    async def __aenter__(self) -> Self:
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
            return any(obj is key for obj in self._map.values())
        return key in self._map

    def register(self, id: str, data: ZaptecBase) -> None:
        """Register an object data with id."""
        if id in self._map:
            raise ValueError(
                f"Object with id {id} already registered. Use unregister() to remove it first."
            )
        self._map[id] = data

    def unregister(self, id: str) -> None:
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

    def qual_id(self, id: str) -> str:
        """Get the qualified id of an object.

        If the object is not found, return the id as is.
        """
        obj = self._map.get(id)
        if obj is None:
            return id
        return obj.qual_id

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
                headers = kwargs["headers"].copy()
                # Remove the Authorization header from the log
                if "Authorization" in headers:
                    headers["Authorization"] = "<Removed for security>"
                yield f"     headers {dict((k, v) for k, v in headers.items())}"
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
            if resp.status != HTTPStatus.OK:
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
        sleep_delay: float = 0.0
        start_time: float = time.perf_counter()
        iteration = 0
        for iteration in range(1, retries + 1):
            try:
                # Sleep before retrying the request
                if sleep_delay > 0:
                    if DEBUG_API_CALLS:
                        _LOGGER.debug("@@@  SLEEP for %.1f seconds", sleep_delay)
                    await asyncio.sleep(sleep_delay)

                # Log the request
                log_req = list(self._request_log(self.redact(url), method, iteration, **kwargs))
                if DEBUG_API_CALLS:
                    for msg in log_req:
                        _LOGGER.debug(msg)

                # Capture the current time
                start_time = time.perf_counter()

                # Make the request
                async with (
                    self._ratelimiter,
                    self._client.request(method=method, url=url, **kwargs) as response,
                ):
                    # Log the response
                    log_resp = [m async for m in self._response_log(response)]
                    if DEBUG_API_CALLS:
                        for msg in log_resp:
                            _LOGGER.debug(msg)

                    # Prepare the exception handler
                    def log_exc(exc: Exception) -> Exception:
                        """Log the exception and return it."""
                        if DEBUG_API_EXCEPTIONS:
                            if not DEBUG_API_CALLS:
                                for msg in log_req + log_resp:
                                    _LOGGER.debug(msg)
                            _LOGGER.error(str(exc), exc_info=exc)
                        return exc

                    # Let the caller handle the response. If the caller
                    # calls __next__ on the generator the request will be
                    # retried.
                    yield response, log_exc

            # Exceptions that can be retried
            except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as err:
                error = err  # Capture tha last error
                if DEBUG_API_EXCEPTIONS:
                    _LOGGER.error(
                        "Request to %s failed (attempt %s): %s",
                        url,
                        iteration,
                        type(err).__qualname__,
                        exc_info=err,
                    )

            finally:
                # Calculate the next exponential backoff delay with jitter
                delay = delay * API_RETRY_FACTOR
                delay = random.normalvariate(delay, delay * API_RETRY_JITTER)
                delay = min(delay, self._max_time)

                # If the sleep time is negative, it means the request took
                # longer than the calculated delay, so we don't need to sleep.
                sleep_delay = delay - time.perf_counter() + start_time

        if isinstance(error, asyncio.TimeoutError):
            raise RequestTimeoutError(
                f"Request to {url} timed out after {iteration} retries"
            ) from None

        if isinstance(error, aiohttp.ClientConnectionError):
            raise RequestConnectionError(
                f"Request to {url} failed after {iteration} retries: {error}"
            ) from None

        raise RequestRetryError(f"Request to {url} failed after {iteration} retries") from None

    async def login(self) -> None:
        """Login to the Zaptec API and get an access token."""
        await self._refresh_token()

    async def _refresh_token(self) -> None:
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
                if response.status == HTTPStatus.OK:
                    data = await response.json()
                    # The data includes the time the access token expires
                    # atm we just ignore it and refresh token when needed.
                    self._token_info.update(data)
                    self._access_token = data.get("access_token")
                    if DEBUG_API_CALLS:
                        _LOGGER.debug("     TOKEN OK")
                    return

                if response.status == HTTPStatus.BAD_REQUEST:
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

    async def request(self, url: str, *, method: str = "get", data=None, base_url: str = API_URL):
        """Make a request to the API."""

        full_url = base_url + url
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
                if response.status == HTTPStatus.UNAUTHORIZED:
                    await self._refresh_token()
                    kwargs["headers"]["Authorization"] = f"Bearer {self._access_token}"
                    continue  # Retry request

                if response.status in (HTTPStatus.CREATED, HTTPStatus.NO_CONTENT):
                    return await response.read()

                if response.status == HTTPStatus.OK:
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

                # Internal server error handling. Zaptec is observed to return
                # this error in varous cases, so we handled it specially here.
                # GET request gets logged and then retried, while POST and
                # PUT requests are not retried.
                if response.status == HTTPStatus.INTERNAL_SERVER_ERROR:
                    log_exc(error)  # Log the error
                    if DEBUG_API_CALLS:
                        # There are additional details in the response that Zaptec
                        # provides on 500. Let's log it.
                        text = await response.text()
                        if len(text) > MAX_DEBUG_TEXT_LEN_ON_500:
                            text = text[:MAX_DEBUG_TEXT_LEN_ON_500] + "..."
                        _LOGGER.debug("     PAYLOAD %r", text)
                    if method.lower() == "get":
                        continue  # GET: Retry request
                    raise error  # POST/PUT: Raise error

                # All other error codes will be raised
                raise log_exc(error)
            return None  # Should not happen, but the linter likes it.

    # =======================================================================
    #   UPDATE METHODS

    async def build(self) -> None:
        """Make the python interface."""
        _LOGGER.debug("Discover and build hierarchy")

        # Get the API constants
        const: dict = await self.request("constants")
        ZCONST.clear()
        ZCONST.update(const)
        ZCONST.update_ids_from_schema(None)

        # Update the redactor
        for objid, obj in self._map.items():
            self.redact.add(objid, replace_by=f"<--{obj.qual_id}-->")
        redact = self.redact

        # Get list of installations
        installations = await self.request("installation")

        for inst_item in installations["Data"]:
            instid = inst_item["Id"]
            self.redact.add_uid(instid, "Inst")

            # Add or update the installation object.
            if instid in self:
                _LOGGER.debug("  Installation %s  (existing)", redact(instid))
                installation: Installation = self[instid]
                installation.set_attributes(inst_item)
            else:
                _LOGGER.debug("  Installation %s  (adding)", redact(instid))
                installation = Installation(inst_item, self)
                self.register(instid, installation)

            await installation.build()

        # Check for installations that are no longer available
        new_installations = {d["Id"] for d in installations["Data"]}
        have_installations = {o.id for o in self.installations}
        if missing_installations := (have_installations - new_installations):
            _LOGGER.warning(
                "These installations are no longer available but remain in use: %s",
                redact(missing_installations),
            )
            _LOGGER.warning("To remove them, please restart the integration.")

        # Check for standalone charger or chargers that are not available any more.
        chargers = await self.request("chargers")
        new_chargers = {d["Id"] for d in chargers["Data"]}
        have_chargers = {c.id for c in self.chargers}
        if missing_chargers := (have_chargers - new_chargers):
            _LOGGER.warning(
                "These chargers are no longer available: %s", redact(missing_chargers)
            )
            _LOGGER.warning("To remove them, please restart the integration.")

        # Find the installation based chargers
        installation_chargers = set(
            itertools.chain.from_iterable(
                (c.id for c in inst.chargers) for inst in self.installations
            )
        )

        # Add standalone chargers that are not part of any installation.
        # Users without service access right does not have access to the installation
        # object, so we need to add all the object at this point.
        for charger_item in chargers["Data"]:
            chgid = charger_item["Id"]
            self.redact.add_uid(chgid, "Charger")

            if chgid in installation_chargers:
                continue  # Skip the chargers which have already been found in installations
            if chgid in self:
                _LOGGER.debug("  Standalone charger %s  (existing)", redact(chgid))
                charger: Charger = self[chgid]
                charger.set_attributes(charger_item)
            else:
                _LOGGER.debug("  Standalone charger %s  (adding)", redact(chgid))
                charger = Charger(charger_item, self, installation=None)
                self.register(chgid, charger)

            # The charger update might have provided enough information that the
            # charger can be assosciated with the installation.
            installation_id = charger_item.get("InstallationId")
            if installation_id in self:
                installation: Installation = self[installation_id]
                _LOGGER.debug(
                    "Able to associate %s with %s",
                    charger.qual_id,
                    installation.qual_id,
                )
                charger.installation = installation
                installation.chargers.append(charger)

        # Update the observation, settings and commands ids based on the
        # discovered device types.
        ZCONST.update_ids_from_schema({str(chg["DeviceType"]) for chg in self.chargers})

        self.is_built = True

    async def poll(
        self,
        objs: Iterable[str] | None = None,
        *,
        info: bool = False,
        state: bool = True,
        firmware: bool = False,
    ) -> None:
        """Update the info and state from Zaptec."""
        if objs is None:
            objs = iter(self)

        for objid in objs:
            obj = self.get(objid)
            if obj is None:
                raise ValueError(f"Object with id {objid} not found")
            if info:
                await obj.poll_info()
            if state:
                await obj.poll_state()
            if firmware and isinstance(obj, Installation):
                await obj.poll_firmware_info()
