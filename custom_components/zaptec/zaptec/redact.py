"""Helper class to redact sensitive data from the API logging."""

from __future__ import annotations

from pprint import pformat
from typing import ClassVar, TypeVar, cast

from .zconst import ZCONST

T = TypeVar("T")


class Redactor:
    """Class to handle redaction of sensitive data."""

    # Data fields that must be redacted from the output
    REDACT_KEYS: ClassVar[list[str]] = [
        "Address",
        "ChargerId",
        "ChargerCurrentUserUuid",
        "CircuitId",
        "City",
        "DeviceId",
        "Id",
        "ID",
        "InstallationId",
        "InstallationName",
        "Latitude",
        "LogoBase64",
        "Longitude",
        "LteIccid",
        "LteImei",
        "LteImsi",
        "MacWiFi",
        "MacMain",
        "MacPlcModuleGrid",
        "MID",
        "Name",
        "NewChargeCard",
        "PilotTestResults",
        "Pin",
        "ProductionTestResults",
        "SerialNo",
        "SupportGroup",
        "ZipCode",
    ]

    # Never redact these words
    NEVER_REDACT: ClassVar[list] = [
        None,
        True,
        False,
        "true",
        "false",
        "0",
        "0.",
        "0.0",
        0,
        0.0,
        "1",
        "1.",
        "1.0",
        1,
        1.0,
        "",
    ]

    # Keys that will be looked up into the observer id dict
    OBS_KEYS: ClassVar[list[str]] = ["SettingId", "StateId"]

    # Key names that will be redacted if they the dict has a OBS_KEY entry
    # and it is in the REDACT_KEYS list.
    VALUES: ClassVar[list[str]] = [
        "Value",
        "ValueAsString",
    ]

    def __init__(self, do_redact: bool) -> None:
        """Initialize redactor."""
        self.do_redact = do_redact
        self.redacts = {}
        self.redact_info = {}

    def dumps(self) -> str:
        """Dump the redaction database in a readable format."""
        return pformat(
            {k: v["text"] for k, v in self.redact_info.items()},
        )

    def add(self, obj, *, key=None, replace_by=None, ctx=None) -> str:
        """Add a new redaction to the list."""
        if not replace_by:
            replace_by = f"<--Redact #{len(self.redacts) + 1}-->"
        self.redacts[obj] = replace_by
        self.redact_info[replace_by] = {  # For statistics only
            "text": obj,
            "from": f"{key} in {ctx}" if key else ctx,
        }
        return replace_by

    def add_uid(self, uid, name, *, ctx=None) -> str:
        """Add a new redaction for a UID."""
        return self.add(uid, replace_by=f"<--{name}[{uid[-6:]}]-->", ctx=ctx)

    def __call__(self, obj: T, *, key=None, second_pass=False, ctx=None) -> T:
        """Redact the object if it is present in the redacted dict.

        A new redaction is created if make_new is not None. ctx is
        for logging output.
        """

        if not self.do_redact:
            return obj

        if isinstance(obj, (tuple, list, set)):
            # Redact each element in the list
            return cast(
                T,
                [self(k, key=key, second_pass=second_pass, ctx=ctx) for k in obj],
            )

        if isinstance(obj, dict):
            # Redact each value in the dict. Unless secondpass is set, the keys
            # are checked if they are in the REDACT_KEYS list.
            return cast(
                T,
                {
                    k: self(
                        v,
                        key=k if not second_pass else key,
                        second_pass=second_pass,
                        ctx=ctx,
                    )
                    for k, v in obj.items()
                },
            )

        # Check if the object is already redacted
        if obj in self.redacts:
            return self.redacts[obj]

        # Check if new redaction is needed
        if key and key in self.REDACT_KEYS and obj and obj not in self.NEVER_REDACT:
            return cast(T, self.add(obj, key=key, ctx=ctx))

        # Check if the string contains a redacted string
        if isinstance(obj, str):
            for k, v in self.redacts.items():
                if isinstance(k, str) and k in obj:
                    obj = obj.replace(k, v)

        return obj

    def redact_statelist(self, objs, ctx=None):
        """Redact the special state list objects."""
        for obj in objs:
            for key in self.OBS_KEYS:
                if key not in obj:
                    continue
                keyv = ZCONST.observations.get(obj[key])
                if keyv is not None:
                    obj[key] = f"{obj[key]} ({keyv})"
                if keyv not in self.REDACT_KEYS:
                    continue
                for value in self.VALUES:
                    if value not in obj:
                        continue
                    obj[value] = self(obj[value], key=obj[key], ctx=ctx)
        return objs
