"""Diagnostics support for Zaptec."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# to Support running this as a script.
if __name__ != "__main__":
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

from . import ZaptecUpdateCoordinator
from .api import Account
from .const import DOMAIN

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

    def redact(self, text: str, make_new=None, ctx=None):
        """Redact the text if it is present in the redacted dict.
        A new redaction is created if make_new is not None. ctx is only
        for logging output.
        """
        if not self.do_redact:
            return text
        elif text in self.redacts:
            return self.redacts[text]
        elif make_new is not None:
            red = f"<--Redact #{len(self.redacts) + 1}-->"
            self.redacts[text] = red
            self.redact_info[red] = {  # For statistics only
                "text": text,
                "from": f"{make_new} in {ctx}",
            }
            return red
        if isinstance(text, str):
            for k, v in self.redacts.items():
                if str(k) in text:
                    text = text.replace(k, v)
        return text

    def redact_obj_inplace(self, obj, ctx=None, secondpass=False):
        """Iterate over obj and redact the fields. NOTE! This function
        modifies the argument object in-place.
        """
        if isinstance(obj, list):
            for k in obj:
                self.redact_obj_inplace(k, ctx=ctx, secondpass=secondpass)
            return obj
        elif not isinstance(obj, dict):
            return self.redact(obj, ctx=ctx)
        for k, v in obj.items():
            if isinstance(v, (list, dict)):
                self.redact_obj_inplace(v, ctx=ctx, secondpass=secondpass)
                continue
            obj[k] = self.redact(
                v,
                make_new=k if not secondpass and k in self.REDACT_KEYS else None,
                ctx=ctx,
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
                    obj[value] = self.redact(obj[value], make_new=obj[key], ctx=ctx)
        return objs


async def async_get_device_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""

    coordinator: ZaptecUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    acc: Account = coordinator.account

    out = {}
    api = out.setdefault("api", {})

    # Helper to redact the output data
    red = Redactor(DO_REDACT, acc._obs_ids)  # FIXME: Access to private member

    async def req(url):
        try:
            return await acc._request(url)  # FIXME: Access to private member
        except Exception as err:
            return {"failed": str(err)}

    def add(url, obj, ctx=None):
        red.redact_obj_inplace(obj, ctx=ctx)
        api[red.redact(url)] = obj

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
    out.setdefault(
        "maps",
        [
            red.redact_obj_inplace(deepcopy(obj._attrs), ctx="maps")
            for obj in acc.map.values()
        ],
    )

    # 2nd pass to replace any newer redacted text within the output.
    red.redact_obj_inplace(out, secondpass=True)

    #
    #  REDACTED DATA
    #
    if INCLUDE_REDACTS:
        out.setdefault("redacts", red.redact_info)

    return out


if __name__ == "__main__":
    # Just to execute the script manually. Must be run using
    # python -m custom_components.zaptec.diagnostics
    import asyncio
    import aiohttp
    import os
    from pprint import pprint
    from dataclasses import dataclass

    async def gogo():
        username = os.environ.get("zaptec_username")
        password = os.environ.get("zaptec_password")
        acc = Account(
            username,
            password,
            client=aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)),
        )

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
