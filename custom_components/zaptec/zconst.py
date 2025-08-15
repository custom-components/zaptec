"""Main API for Zaptec."""

from __future__ import annotations

from collections import UserDict
import json
import logging
from typing import Any, ClassVar, Literal

from .misc import to_under

_LOGGER = logging.getLogger(__name__)

type CommandType = Literal[
    "RestartCharger",
    "UpgradeFirmware",
    "StopChargingFinal",
    "ResumeCharging",
    "DeauthorizeAndStop",
    "AuthorizeCharge",  # Not official command, used by the integration
]
"""Approved API commands."""


#
# Helper wrapper for reading constants from the API
#
class ZConst(UserDict):
    """Zaptec constants wrapper class."""

    observations: dict[str, int]
    settings: dict[str, int]
    commands: dict[str, int]
    """Mapping of command name to command id and id to name."""

    update_params: ClassVar[list[str]] = [
        "maxChargeCurrent",
        "maxChargePhases",
        "minChargeCurrent",
        "offlineChargeCurrent",
        "offlineChargePhase",
        "meterValueInterval",
    ]
    """Valid parameters for charger settings (`chargers/{id}/update`)."""

    # Copied from the Zaptec API documentation
    # https://docs.zaptec.com/docs/identify-device-types
    charger_models: ClassVar[dict[str, set[str]]] = {
        "Zaptec Pro": {"ZCS", "ZPR", "ZCH", "ZPG"},
        "Zaptec Go": {"ZAP", "ZGB", "ZAG"},
        "Zaptec Go2": {"GPN", "GPG"},
        "Zaptec Sense:": {"APH", "APG", "APM"},
    }
    """Mapping of charger models to device serial number prefixes."""

    def get_remap(
        self, wanted: list[str], device_types: set[str] | None = None
    ) -> dict[str, int]:
        """Parse the Zaptec constants and return a remap dict.

        Parse the given zaptec constants record `CONST` and generate
        a remap dict for the given `wanted` keys. If `device_types` is
        specified, the entries for these device schemas will be merged
        with the main remap dict.

        Example:
            get_remap(["Observations", "ObservationIds"], [4])

        """
        ids = {}
        for k, v in self.items():
            if k in wanted:
                ids.update(v)

            if device_types and k == "Schema":
                for name, schema in v.items():
                    for want in wanted:
                        v2 = schema.get(want)
                        if v2 is None:
                            continue
                        if name in device_types or schema.get("DeviceType") in device_types:
                            ids.update(v2)

        # make the reverse lookup
        ids.update({v: k for k, v in ids.items()})
        return ids

    def update_ids_from_schema(self, device_types: set[str] | None) -> None:
        """Update the id from a schema.

        Read observations, settings and command ids from the
        given device types. This is used to update the ids when the
        schema is updated.
        """

        # Define the remaps
        self.observations = self.get_remap(["Observations", "ObservationIds"], device_types)
        self.settings = self.get_remap(["Settings", "SettingIds"], device_types)
        self.commands = self.get_remap(["Commands", "CommandIds"], device_types)

        # Commands can also be specified as lower case strings
        self.commands.update(
            {to_under(k): v for k, v in self.commands.items() if isinstance(k, str)}
        )

    #
    # DATA EXTRACTION
    #
    @property
    def charger_operation_modes_list(self) -> list[str]:
        """Return a list of all charger operation modes."""
        return list(self.get("ChargerOperationModes", {}))

    @property
    def device_types_list(self) -> list[str]:
        """Return a list of all device types."""
        return list(self.get("DeviceTypes", {}))

    @property
    def installation_authentication_type_list(self) -> list[str]:
        """Return a list of all installation authentication types."""
        return list(self.get("InstallationAuthenticationType", {}))

    @property
    def installation_types_list(self) -> list[str]:
        """Return a list of all installation types."""
        return list(self.get("InstallationTypes", {}))

    @property
    def network_types_list(self) -> list[str]:
        """Return a list of all electrical network types."""
        return list(self.get("NetworkTypes", {}))

    @property
    def serial_to_model(self) -> dict[str, str]:
        """Return a mapping of serial number prefixes to charger models."""
        return {
            prefix: model
            for model, prefixes in self.charger_models.items()
            for prefix in prefixes
        }

    #
    # ATTRIBUTE TYPE CONVERTERS
    # Want these to be methods so they can be used for type convertions of
    # attributes in the API responses.
    #
    def type_authentication_type(self, val: int) -> str:
        """Convert the authentication type to a string."""
        modes = {str(v): k for k, v in self.get("InstallationAuthenticationType", {}).items()}
        return modes.get(str(val), str(val))

    def type_completed_session(self, val: str) -> dict[str, Any]:
        """Convert the CompletedSession to a dict."""
        data = json.loads(val)
        if "SignedSession" in data:
            data["SignedSession"] = self.type_ocmf(data["SignedSession"])
        return data

    def type_device_type(self, val: int) -> str:
        """Convert the device type to a string."""
        modes = {str(v): k for k, v in self.get("DeviceTypes", {}).items()}
        return modes.get(str(val), str(val))

    def type_installation_type(self, val: int) -> str:
        """Convert the installation type to a string."""
        modes = {
            str(v.get("Id")): v.get("Name") for v in self.get("InstallationTypes", {}).values()
        }
        return modes.get(str(val), str(val))

    def type_network_type(self, val: int) -> str:
        """Convert the network type to a string."""
        modes = {str(v): k for k, v in self.get("NetworkTypes", {}).items()}
        return modes.get(str(val), str(val))

    def type_ocmf(self, data: str) -> dict[str, Any]:
        """Open Charge Metering Format (OCMF) type."""
        # https://github.com/SAFE-eV/OCMF-Open-Charge-Metering-Format/blob/master/OCMF-en.md
        sects = data.split("|")
        if len(sects) not in (2, 3) or sects[0] != "OCMF":
            raise ValueError(f"Invalid OCMF data: {data}")
        return json.loads(sects[1])

    def type_charger_operation_mode(self, val: int) -> str:
        """Convert the operation mode to a string."""
        modes = {str(v): k for k, v in self.get("ChargerOperationModes", {}).items()}
        return modes.get(str(val), str(val))

    def type_user_roles(self, val: int) -> str:
        """Convert the user roles to a string."""
        if val == 0:
            return "None"
        # v != 0 is needed to avoid the 0 == 0 case leading to None always being included
        roles = {k for k, v in self.get("UserRoles", {}).items() if v != 0 and (v & val) == v}
        return ", ".join(roles)
