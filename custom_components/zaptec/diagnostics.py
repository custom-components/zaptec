"""Diagnostics support for Zaptec."""
from __future__ import annotations

import traceback
from copy import deepcopy
from typing import Any, TypeVar, cast

# to Support running this as a script.
if __name__ != "__main__":
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

from . import ZaptecUpdateCoordinator
from .api import Account
from .const import DOMAIN

T = TypeVar("T")

# IF this is true, the output data will be redacted.
DO_REDACT = True

# If this is set to True, the redacted data will be included in the output.
# USE WITH CAUTION! This will include sensitive data in the output.
INCLUDE_REDACTS = False


class Redactor:
    """Class to handle redaction of sensitive data."""

    # Data fields that must be redacted from the output
    REDACT_KEYS = [
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
        "ZipCode",
    ]

    # Never redact these words
    NEVER_REDACT = [
        true,
        false,
        "true",
        "false",
        "0",
        "0.",
        "0.0",
        0,
        0.,
        "1",
        "1.",
        1,
        1.,
    ]

    # Keys that will be looked up into the observer id dict
    OBS_KEYS = ["SettingId", "StateId"]

    # Key names that will be redacted if they the dict has a OBS_KEY entry
    # and it is in the REDACT_KEYS list.
    VALUES = [
        "Value",
        "ValueAsString",
    ]

    def __init__(self, do_redact: bool, obs_ids: dict[str, str]):
        self.do_redact = do_redact
        self.obs_ids = obs_ids
        self.redacts = {}
        self.redact_info = {}

    def redact(self, obj: T, ctx=None, key=None, secondpass=False) -> T:
        """Redact the object if it is present in the redacted dict.
        A new redaction is created if make_new is not None. ctx is
        for logging output.
        """

        if not self.do_redact:
            return obj

        if isinstance(obj, str):
            # Check if new redaction is needed
            if key and key in self.REDACT_KEYS and obj not in self.NEVER_REDACT:
                red = f"<--Redact #{len(self.redacts) + 1}-->"
                self.redacts[obj] = red
                self.redact_info[red] = {  # For statistics only
                    "text": obj,
                    "from": f"{key} in {ctx}",
                }
                return cast(T, red)

            # Check if the string is already redacted
            if obj in self.redacts:
                return self.redacts[obj]

            # Check if the string contains a redacted string
            for k, v in self.redacts.items():
                if k in obj:
                    obj = obj.replace(k, v)

        elif isinstance(obj, (tuple, list)):
            # Redact each element in the list
            return cast(
                T,
                [self.redact(k, ctx=ctx, key=key, secondpass=secondpass) for k in obj],
            )

        elif isinstance(obj, dict):
            # Redact each value in the dict. Unless secondpass is set, the keys
            # are checked if they are in the REDACT_KEYS list.
            return cast(
                T,
                {
                    k: self.redact(
                        v,
                        ctx=ctx,
                        key=k if not secondpass else key,
                        secondpass=secondpass,
                    )
                    for k, v in obj.items()
                },
            )

        return obj

    def redact_statelist(self, objs, ctx=None):
        """Redact the special state list objects."""
        for obj in objs:
            for key in self.OBS_KEYS:
                if key not in obj:
                    continue
                keyv = self.obs_ids.get(obj[key])
                if keyv is not None:
                    obj[key] = f"{obj[key]} ({keyv})"
                if keyv not in self.REDACT_KEYS:
                    continue
                for value in self.VALUES:
                    if value not in obj:
                        continue
                    obj[value] = self.redact(obj[value], key=obj[key], ctx=ctx)
        return objs


async def async_get_device_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""

    out = {}
    api = out.setdefault("api", {})
    red = None

    try:
        coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
        acc: Account = coordinator.account

        # Helper to redact the output data
        red = Redactor(DO_REDACT, acc._obs_ids)

        async def req(url):
            try:
                result = await acc._request(url)
                if not isinstance(result, (dict, list)):
                    return {
                        "type error": f"Expected dict, got type {type(result).__name__}, value {result}",
                    }
                return result
            except Exception as err:
                return {
                    "exception": type(err).__name__,
                    "err": str(err),
                    "tb": list(traceback.format_exc().splitlines()),
                }

        def add(url, obj, ctx=None):
            api[red.redact(url, ctx=ctx)] = red.redact(obj, ctx=ctx)

        #
        #  API FETCHING
        #
        data = await req(url := "installation")
        installation_ids = [inst["Id"] for inst in data.get("Data", [])]
        add(url, data, ctx="installation")

        circuit_ids = []
        charger_in_circuits_ids = []
        for inst_id in installation_ids:
            data = await req(url := f"installation/{inst_id}/hierarchy")

            for circuit in data.get("Circuits", []):
                circuit_ids.append(circuit["Id"])
                for charger in circuit.get("Chargers", []):
                    charger_in_circuits_ids.append(charger["Id"])

            add(url, data, ctx="hierarchy")

            data = await req(url := f"installation/{inst_id}")
            add(url, data, ctx="installation")

        for circ_id in circuit_ids:
            data = await req(url := f"circuits/{circ_id}")
            add(url, data, ctx="circuit")

        data = await req(url := "chargers")
        charger_ids = [charger["Id"] for charger in data.get("Data", [])]
        add(url, data, ctx="chargers")

        for charger_id in set([*charger_ids, *charger_in_circuits_ids]):
            data = await req(url := f"chargers/{charger_id}")
            add(url, data, ctx="charger")

            data = await req(url := f"chargers/{charger_id}/state")
            red.redact_statelist(data, ctx="state")
            add(url, data, ctx="state")

            data = await req(url := f"chargers/{charger_id}/settings")
            red.redact_statelist(data.values(), ctx="settings")
            add(url, data, ctx="settings")

        #
        #  MAPPINGS
        #
        def addmap(k, v):
            obj = {
                '__key': k,
                '__type': v.__class__.__name__,
            }
            obj.update(v._attrs)
            return obj

        out.setdefault(
            "maps",
            [red.redact(addmap(k, v), ctx="maps") for k, v in acc.map.items()],
        )

    except Exception as err:
        out.setdefault("failures", []).append(
            {
                "exception": type(err).__name__,
                "err": str(err),
                "tb": list(traceback.format_exc().splitlines()),
            }
        )

    try:
        if red:
            # 2nd pass to replace any newer redacted text within the output.
            out = red.redact(out, secondpass=True)

            #
            #  REDACTED DATA
            #
            if INCLUDE_REDACTS:
                out.setdefault("redacts", red.redact_info)

    except Exception as err:
        out.setdefault("failures", []).append(
            {
                "exception": type(err).__name__,
                "err": str(err),
                "tb": list(traceback.format_exc().splitlines()),
            }
        )

    return out


if __name__ == "__main__":
    # Just to execute the script manually. Must be run using
    # python -m custom_components.zaptec.diagnostics
    import asyncio
    import os
    from dataclasses import dataclass
    from pprint import pprint

    import aiohttp

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")
        acc = Account(
            username,
            password,
            client=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)),
        )
        await acc.build()

        try:
            #
            # Mocking to pretend to be a hass instance
            #
            @dataclass
            class FakeHass:
                data: dict

            @dataclass
            class FakeConfig:
                entry_id: str

            @dataclass
            class FakeCoordinator:
                account: Account

            coordinator = FakeCoordinator(account=acc)
            config = FakeConfig(entry_id="")
            hass = FakeHass(data={DOMAIN: {config.entry_id: coordinator}})

            # Get the diagnostics info
            out = await async_get_device_diagnostics(hass, config, None)
            pprint(out)

        finally:
            await acc._client.close()

    asyncio.run(gogo())
