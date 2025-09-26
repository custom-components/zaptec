"""Diagnostics support for Zaptec."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .manager import ZaptecConfigEntry, ZaptecManager
from .zaptec import Redactor, Zaptec, ZaptecBase

_LOGGER = logging.getLogger(__name__)

# If this is true, the output data will be redacted.
DO_REDACT = True

# If this is set to True, the redacted data will be included in the output.
# USE WITH CAUTION! This will include sensitive data in the output.
INCLUDE_REDACTS = False


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ZaptecConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    try:
        return await _get_diagnostics(hass, config_entry)
    except Exception:
        _LOGGER.exception("Error getting diagnostics")


async def async_get_device_diagnostics(
    hass: HomeAssistant, config_entry: ZaptecConfigEntry, device: dr.DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    try:
        return await _get_diagnostics(hass, config_entry)
    except Exception:
        _LOGGER.exception("Error getting diagnostics for device %s", device.id)


async def _get_diagnostics(
    hass: HomeAssistant,
    config_entry: ZaptecConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a device."""

    out = {}
    manager: ZaptecManager = config_entry.runtime_data
    zaptec: Zaptec = manager.zaptec

    # Helper to redact the output data
    redact = Redactor(DO_REDACT)

    def add_failure(err: Exception) -> None:
        """Add a failure to the output."""
        out.setdefault("failures", []).append(
            {
                "exception": type(err).__name__,
                "err": str(err),
                "tb": list(traceback.format_exc().splitlines()),
            }
        )

    #
    #  PRE SEED OBJECT IDS FOR REDACTION
    #
    try:
        for objid, obj in zaptec.items():
            redact.add(objid, replace_by=f"<--{obj.qual_id}-->", ctx="preseed")
    except Exception as err:
        add_failure(err)

    #
    #  API FETCHING
    #
    try:
        api = out.setdefault("api", {})

        async def request(url: str) -> Any:
            """Make an API request and return the result."""
            try:
                result = await zaptec.request(url)
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

        def add(url, obj, ctx=None) -> None:
            api[redact(url, ctx=ctx)] = redact(obj, ctx=ctx)

        data = await request(url := "installation")
        installation_ids = [inst["Id"] for inst in data.get("Data", [])]
        add(url, data, ctx="installation")

        charger_in_circuits_ids = []
        for inst_id in installation_ids:
            data = await request(url := f"installation/{inst_id}/hierarchy")

            for circuit in data.get("Circuits", []):
                add(f"circuits/{circuit['Id']}", circuit, ctx="circuit")
                for charger in circuit.get("Chargers", []):
                    charger_in_circuits_ids.append(charger["Id"])

            add(url, data, ctx="hierarchy")

            data = await request(url := f"installation/{inst_id}")
            add(url, data, ctx="installation")

        data = await request(url := "chargers")
        charger_ids = [charger["Id"] for charger in data.get("Data", [])]
        add(url, data, ctx="chargers")

        for charger_id in {*charger_ids, *charger_in_circuits_ids}:
            data = await request(url := f"chargers/{charger_id}")
            add(url, data, ctx="charger")

            data = await request(url := f"chargers/{charger_id}/state")
            redact.redact_statelist(data, ctx="state")
            add(url, data, ctx="state")

    except Exception as err:
        add_failure(err)

    #
    #  ZAPTEC OBJECTS
    #
    try:

        def addmap(k: str, v: ZaptecBase) -> dict:
            obj = {
                "__key": k,
                "qual_id": v.qual_id,
            }
            obj.update(v.asdict())
            return obj

        out.setdefault(
            "zaptec",
            [redact(addmap(k, v), ctx="zaptec") for k, v in zaptec.items()],
        )
    except Exception as err:
        add_failure(err)

    #
    #  ENTITIES
    #
    try:
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)

        device_map = out.setdefault("entities", {})
        for dev in dr.async_entries_for_config_entry(
            device_registry, config_entry_id=config_entry.entry_id
        ):
            for _, zap_dev_id in dev.identifiers:
                entity_list = device_map.setdefault(redact(zap_dev_id, ctx="entities"), [])

                dev_entities = er.async_entries_for_device(
                    entity_registry, dev.id, include_disabled_entities=True
                )
                for ent in dev_entities:
                    entity_list.append(
                        {
                            "entity_id": ent.entity_id,
                            "unique_id": ent.unique_id,
                        }
                    )
    except Exception as err:
        add_failure(err)

    #
    #  2ND PASS
    #
    try:
        # 2nd pass to replace any newer redacted text within the output.
        out = redact(out, second_pass=True)
    except Exception as err:
        add_failure(err)

    #
    #  REDACTED DATA
    #
    try:
        if INCLUDE_REDACTS:
            out.setdefault("redacts", redact.redact_info)
    except Exception as err:
        add_failure(err)

    return out
