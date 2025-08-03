"""Data validator for Zaptec API data."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

_LOGGER = logging.getLogger(__name__)


class Installation(BaseModel):
    """Pydantic model for a Zaptec installation."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Active: bool
    AuthenticationType: int
    CurrentUserRoles: int
    InstallationType: int
    NetworkType: int


class Installations(BaseModel):
    """Pydantic model for a list of Zaptec installations."""

    model_config = ConfigDict(extra="allow")
    Data: list[Installation]
    Pages: int


class Charger(BaseModel):
    """Pydantic model for a Zaptec charger."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str
    Active: bool
    DeviceType: int


class ChargerState(BaseModel):
    """Pydantic model for a single state of a Zaptec charger."""

    model_config = ConfigDict(extra="allow")
    StateId: int
    ValueAsString: str


ChargerStates = TypeAdapter[list[ChargerState]]
ChargerUpdate = TypeAdapter[dict[str, str]]


class Chargers(BaseModel):
    """Pydantic model for a list of Zaptec chargers."""

    model_config = ConfigDict(extra="allow")
    Data: list[Charger]
    Pages: int


class Circuit(BaseModel):
    """Pydantic model for a Zaptec circuit."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str
    Chargers: list[Charger]


class Hierarchy(BaseModel):
    """Pydantic model for the hierarchy of Zaptec objects in an installation."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str
    NetworkType: int
    Circuits: list[Circuit]


class ChargerFirmware(BaseModel):
    """Pydantic model for the firmware information of a Zaptec charger."""

    model_config = ConfigDict(extra="allow")
    ChargerId: str
    DeviceType: int
    IsOnline: bool
    CurrentVersion: str
    AvailableVersion: str
    IsUpToDate: bool


class ChargerLocalSettings(BaseModel):
    """
    Pydantic model for the local settings of a Zaptec charger.

    Note: This model is used in an undocumented API-call, and should be removed as soon as we have
    official API-calls that can cover the necessary functionality
    """

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str | None = None
    DeviceId: str | None = None


ChargerFirmwares = TypeAdapter[list[ChargerFirmware]]


class InstallationConnectionDetails(BaseModel):
    """Pydantic model for the servicebus connection details of a Zaptec installation."""

    model_config = ConfigDict(extra="allow")
    Host: str
    Password: str
    Port: int
    UseSSL: bool
    Subscription: str
    Type: int
    Username: str
    Topic: str


# Mapping of URL to pydantic model
URLS = {
    "installation": Installations,
    "chargers": Chargers,
    "constants": None,
    r"installation/[0-9a-f\-]+": Installation,
    r"installation/[0-9a-f\-]+/hierarchy": Hierarchy,
    r"installation/[0-9a-f\-]+/update": None,
    r"installation/[0-9a-f\-]+/messagingConnectionDetails": InstallationConnectionDetails,
    r"chargers/[0-9a-f\-]+": Charger,
    r"chargers/[0-9a-f\-]+/state": ChargerStates,
    r"chargers/[0-9a-f\-]+/authorizecharge": None,
    r"chargers/[0-9a-f\-]+/SendCommand/[0-9]+": None,
    r"chargers/[0-9a-f\-]+/localSettings": ChargerLocalSettings,
    r"chargers/[0-9a-f\-]+/update": ChargerUpdate,
    r"chargerFirmware/installation/[0-9a-f\-]+": ChargerFirmwares,
}

_URLS = [(k, re.compile(k), v) for k, v in URLS.items()]


def validate(data: Any, url: str) -> None:
    """
    Validate the data.

    Raises:
        ValidationError: If data doesn't match the pydantic model associated with the url.

    """

    for pat, re_pat, model in _URLS:
        # Mathes either the exact string or its regexp
        if url == pat or re_pat.fullmatch(url):
            try:
                if isinstance(model, BaseModel):
                    model.model_validate(data, strict=True)

                elif isinstance(model, TypeAdapter):
                    model.validate_python(data, strict=True)

            except ValidationError as err:
                _LOGGER.error("Failed to validate %s (pattern %s): %s", url, pat, err)
                raise

            return

    _LOGGER.warning("Missing validator for url %s", url)
    _LOGGER.warning("Data: %s", data)
