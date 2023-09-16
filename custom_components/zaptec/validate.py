"""Data validator for Zaptec API data."""
from __future__ import annotations

import logging
import re

try:
    from pydantic.v1 import BaseModel, ConfigDict, ValidationError
except ImportError:
    from pydantic import BaseModel, ConfigDict, ValidationError

_LOGGER = logging.getLogger(__name__)


class TypeWrapper:
    """Workaround class for v1 pydantic"""


class Installation(BaseModel):
    model_config = ConfigDict(extra="allow")
    Id: str


class Installations(BaseModel):
    model_config = ConfigDict(extra="allow")
    Data: list[Installation]


class Charger(BaseModel):
    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str


class ChargerState(BaseModel):
    model_config = ConfigDict(extra="allow")
    StateId: int
    ValueAsString: str


# pydantic v2
# ChargerStates = TypeAdapter[list[ChargerState]]
class ChargerStates(TypeWrapper, BaseModel):
    _data: list[ChargerState]


class ChargerSetting(BaseModel):
    model_config = ConfigDict(extra="allow")
    SettingsId: int
    Value: str = ''


# pydantic v2
# ChargerSettings = TypeAdapter[dict[str, ChargerSetting]]
class ChargerSettings(TypeWrapper, BaseModel):
    _data: dict[str, ChargerSetting]


class Chargers(BaseModel):
    model_config = ConfigDict(extra="allow")
    Data: list[Charger]


class Circuit(BaseModel):
    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str


class CircuitHierarchy(BaseModel):
    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str
    Chargers: list[Charger]


class Hierarchy(BaseModel):
    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str
    Circuits: list[CircuitHierarchy]


class ChargerFirmware(BaseModel):
    model_config = ConfigDict(extra="allow")
    ChargerId: str
    CurrentVersion: str
    AvailableVersion: str
    IsUpToDate: bool


# pydantic v2
# ChargerFirmwares = TypeAdapter[list[ChargerFirmware]]
class ChargerFirmwares(TypeWrapper, BaseModel):
    _data: list[ChargerFirmware]


class InstallationConnectionDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    Host: str
    Password: str
    # Port: int
    Subscription: str
    # Type: int
    Username: str
    Topic: str


# Mapping of URL to pydantic model
URLS = {
    'installation': Installations,
    'chargers': Chargers,
    'constants': None,
    r'installation/[0-9a-f\-]+': Installation,
    r'installation/[0-9a-f\-]+/hierarchy': Hierarchy,
    r'installation/[0-9a-f\-]+/update': None,
    r'installation/[0-9a-f\-]+/messagingConnectionDetails': InstallationConnectionDetails,
    r'circuits/[0-9a-f\-]+': Circuit,
    r'chargers/[0-9a-f\-]+': Charger,
    r'chargers/[0-9a-f\-]+/state': ChargerStates,
    r'chargers/[0-9a-f\-]+/settings': ChargerSettings,
    r'chargers/[0-9a-f\-]+/authorizecharge': None,
    r'chargers/[0-9a-f\-]+/SendCommand/[0-9]+': None,
    r'chargerFirmware/installation/[0-9a-f\-]+': ChargerFirmwares,
}

_URLS = [
    (k, re.compile(k), v) for k, v in URLS.items()
]

def validate(data, url):
    """Validate the data."""

    for pat, re_pat, model in _URLS:

        # Mathes either the exact string or its regexp
        if url == pat or re_pat.fullmatch(url):

            try:
                d = data

                # pydantic v1
                if isinstance(model, TypeWrapper):
                    d = {'_data': data}
 
                if isinstance(model, BaseModel):
                    # pydantic v1
                    model.parse_obj(d)

                    # pydantic v2
                    # model.model_validate(data, strict=True)

                # pydantic v2
                # elif isinstance(model, TypeAdapter):
                #     model.validate_python(data, strict=True)
 
            except ValidationError as err:
                _LOGGER.error("Failed to validate %s (pattern %s): %s", url, pat, err)
                raise

            return

    _LOGGER.warning("Missing validator for url %s", url)
    _LOGGER.warning("Data: %s", data)
