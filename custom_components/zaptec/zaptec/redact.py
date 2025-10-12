"""Helper class to redact sensitive data from the API logging."""

from __future__ import annotations

from collections.abc import Sequence
from pprint import pformat
from typing import Any, ClassVar, TypeVar, cast

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
        self.redacts: dict[str, Any] = {}
        self.redact_info: dict[str, dict[str, str]] = {}

    def dumps(self) -> str:
        """Dump the redaction database in a readable format."""
        return pformat(
            {k: v["text"] for k, v in self.redact_info.items()},
        )

    def add(self, redact: str, *, key: str = "", replace_by: str = "", ctx: str = "") -> str:
        """Add a new redaction to the list."""
        if not replace_by:
            replace_by = f"<--Redact #{len(self.redacts) + 1}-->"
        self.redacts[redact] = replace_by
        self.redact_info[replace_by] = {  # For statistics only
            "text": redact,
            "from": f"{key} in {ctx}" if key else ctx,
        }
        return replace_by

    def add_uid(self, uid: str, name: str, *, ctx: str = "") -> str:
        """Add a new redaction for a UID."""
        return self.add(uid, replace_by=f"<--{name}[{uid[-6:]}]-->", ctx=ctx)

    def __call__(
        self, obj: T, *, key: str = "", second_pass: bool = False, ctx: str = ""
    ) -> T | str:
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

        # Convert non-string objects to string for redaction check
        str_obj = str(obj)

        # Check if the object is already redacted
        if str_obj in self.redacts:
            return self.redacts[str_obj]

        # Check if new redaction is needed
        if key and key in self.REDACT_KEYS and obj and obj not in self.NEVER_REDACT:
            return self.add(str_obj, key=key, ctx=ctx)

        # Check if the string contains a redacted string
        if isinstance(obj, str):
            for k, v in self.redacts.items():
                # This extra isinstance check is needed to keep type checker happy
                if isinstance(obj, str) and isinstance(k, str) and k in obj:
                    obj = cast(T, obj.replace(k, v))

        return obj

    def redact_statelist(
        self, objs: Sequence[dict[str, str]], ctx: str = ""
    ) -> Sequence[dict[str, str]]:
        """Redact the special state list objects."""
        for obj in objs:
            for key in self.OBS_KEYS:
                if key not in obj:
                    continue

                # Get the string for the observation key
                keyv: str = ZCONST.observations.get(obj[key], "")
                if keyv:
                    obj[key] = f"{obj[key]} ({keyv})"

                # Redact the value if needed
                for value in self.VALUES:
                    if value not in obj:
                        continue
                    obj[value] = self(obj[value], key=keyv, ctx=ctx)
        return objs
