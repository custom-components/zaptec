"""Tests for services.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
import pytest
import voluptuous as vol
import yaml

from custom_components.zaptec.const import DOMAIN
import custom_components.zaptec.services as services_module
from custom_components.zaptec.services import (
    CHARGER_ID_SCHEMA,
    LIMIT_CURRENT_SCHEMA,
    SEND_COMMAND_SCHEMA,
    async_setup_services,
)
from custom_components.zaptec.zaptec import Charger, Installation

SERVICES_YAML_PATH = Path(services_module.__file__).with_name("services.yaml")


def make_call(hass: MagicMock, data: dict[str, Any]) -> ServiceCall:
    """Build a ServiceCall carrying the given data, bypassing schema validation."""
    return ServiceCall(hass, DOMAIN, "test_service", data)


def make_charger(uid: str = "charger1") -> MagicMock:
    """Create a MagicMock(spec=Charger) with async command methods."""
    charger = MagicMock(spec=Charger)
    charger.id = uid
    charger.command = AsyncMock()
    charger.authorize_charge = AsyncMock()
    return charger


def make_installation(uid: str = "install1") -> MagicMock:
    """Create a MagicMock(spec=Installation) with an async set_limit_current."""
    installation = MagicMock(spec=Installation)
    installation.id = uid
    installation.set_limit_current = AsyncMock()
    return installation


@pytest.fixture
def manager() -> MagicMock:
    """A manager stub exposing plain dicts for `.zaptec` and `.device_coordinators`."""
    mgr = MagicMock()
    mgr.zaptec = {}
    mgr.device_coordinators = {}
    return mgr


@pytest.fixture
def fake_registries() -> SimpleNamespace:
    """Patch er.async_get/dr.async_get with dict-backed fakes and expose the dicts."""
    entities: dict[str, Any] = {}
    devices: dict[str, Any] = {}

    ent_reg = MagicMock()
    ent_reg.async_get.side_effect = entities.get

    dev_reg = MagicMock()
    dev_reg.async_get.side_effect = devices.get

    with (
        patch("custom_components.zaptec.services.er.async_get", return_value=ent_reg),
        patch("custom_components.zaptec.services.dr.async_get", return_value=dev_reg),
    ):
        yield SimpleNamespace(entities=entities, devices=devices)


@pytest.fixture
def add_charger(manager: MagicMock) -> Any:
    """Register a charger + coordinator pair under a given uid in the manager stubs."""

    def _add(uid: str = "charger1") -> tuple[MagicMock, MagicMock]:
        charger = make_charger(uid)
        coordinator = MagicMock()
        coordinator.trigger_poll = AsyncMock()
        manager.zaptec[uid] = charger
        manager.device_coordinators[uid] = coordinator
        return charger, coordinator

    return _add


@pytest.fixture
def add_installation(manager: MagicMock) -> Any:
    """Register an installation + coordinator pair under a given uid in the manager stubs."""

    def _add(uid: str = "install1") -> tuple[MagicMock, MagicMock]:
        installation = make_installation(uid)
        coordinator = MagicMock()
        coordinator.trigger_poll = AsyncMock()
        manager.zaptec[uid] = installation
        manager.device_coordinators[uid] = coordinator
        return installation, coordinator

    return _add


@pytest.fixture
async def handlers(hass: MagicMock, manager: MagicMock) -> dict[str, Any]:
    """Register zaptec services and return {name: handler} for direct invocation."""
    hass.config_entries.async_entries.return_value = [SimpleNamespace(runtime_data=manager)]
    hass.services.has_service = MagicMock(return_value=False)
    await async_setup_services(hass)
    return {call.args[1]: call.args[2] for call in hass.services.async_register.call_args_list}


# ---------------------------------------------------------------------------
# async_setup_services
# ---------------------------------------------------------------------------


async def test_async_setup_services_registers_all_services(hass: MagicMock) -> None:
    """All eight zaptec services get registered under the zaptec domain."""
    hass.services.has_service = MagicMock(return_value=False)

    await async_setup_services(hass)

    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert registered == {
        "stop_charging",
        "resume_charging",
        "authorize_charging",
        "deauthorize_charging",
        "restart_charger",
        "upgrade_firmware",
        "limit_current",
        "send_command",
    }
    assert all(call.args[0] == DOMAIN for call in hass.services.async_register.call_args_list)


async def test_async_setup_services_skips_already_registered(hass: MagicMock) -> None:
    """A service that has_service reports as already present is not re-registered."""
    hass.services.has_service = MagicMock(
        side_effect=lambda _domain, name: name == "stop_charging"
    )

    await async_setup_services(hass)

    registered = {call.args[1] for call in hass.services.async_register.call_args_list}
    assert "stop_charging" not in registered
    assert "resume_charging" in registered


# ---------------------------------------------------------------------------
# iter_objects resolution / error paths (exercised through stop_charging)
# ---------------------------------------------------------------------------


async def test_resolves_via_legacy_charger_id(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A bare charger_id resolves directly to the zaptec object."""
    charger, coordinator = add_charger("charger1")

    await handlers["stop_charging"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_awaited_once_with("stop_charging_final")
    coordinator.trigger_poll.assert_awaited_once()


async def test_resolves_via_device_id(
    hass: MagicMock,
    manager: MagicMock,
    add_charger: Any,
    handlers: dict[str, Any],
    fake_registries: SimpleNamespace,
) -> None:
    """A device_id resolves through the device registry's zaptec identifier."""
    charger, coordinator = add_charger("charger1")
    fake_registries.devices["device1"] = SimpleNamespace(
        identifiers={(DOMAIN, "charger1")}, name="Device 1"
    )

    await handlers["stop_charging"](make_call(hass, {"device_id": "device1"}))

    charger.command.assert_awaited_once_with("stop_charging_final")
    coordinator.trigger_poll.assert_awaited_once()


async def test_resolves_via_entity_id(
    hass: MagicMock,
    manager: MagicMock,
    add_charger: Any,
    handlers: dict[str, Any],
    fake_registries: SimpleNamespace,
) -> None:
    """An entity_id resolves through the entity registry's device, then the device registry."""
    charger, coordinator = add_charger("charger1")
    fake_registries.entities["sensor.foo"] = SimpleNamespace(device_id="device1")
    fake_registries.devices["device1"] = SimpleNamespace(
        identifiers={(DOMAIN, "charger1")}, name="Device 1"
    )

    await handlers["stop_charging"](make_call(hass, {"entity_id": "sensor.foo"}))

    charger.command.assert_awaited_once_with("stop_charging_final")
    coordinator.trigger_poll.assert_awaited_once()


async def test_entity_id_not_found_raises(
    hass: MagicMock, handlers: dict[str, Any], fake_registries: SimpleNamespace
) -> None:
    """An unknown entity_id raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find entity"):
        await handlers["stop_charging"](make_call(hass, {"entity_id": "sensor.missing"}))


async def test_entity_without_device_raises(
    hass: MagicMock, handlers: dict[str, Any], fake_registries: SimpleNamespace
) -> None:
    """An entity with no device_id raises a HomeAssistantError."""
    fake_registries.entities["sensor.foo"] = SimpleNamespace(device_id=None)

    with pytest.raises(HomeAssistantError, match="doesn't have a device"):
        await handlers["stop_charging"](make_call(hass, {"entity_id": "sensor.foo"}))


async def test_device_id_not_found_raises(
    hass: MagicMock, handlers: dict[str, Any], fake_registries: SimpleNamespace
) -> None:
    """An unknown device_id raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find device"):
        await handlers["stop_charging"](make_call(hass, {"device_id": "device_missing"}))


async def test_device_without_identifiers_raises(
    hass: MagicMock, handlers: dict[str, Any], fake_registries: SimpleNamespace
) -> None:
    """A device with no identifiers raises a HomeAssistantError."""
    fake_registries.devices["device1"] = SimpleNamespace(identifiers=set(), name="Device 1")

    with pytest.raises(HomeAssistantError, match="Unable to find identifiers"):
        await handlers["stop_charging"](make_call(hass, {"device_id": "device1"}))


async def test_device_with_non_zaptec_identifier_raises(
    hass: MagicMock, handlers: dict[str, Any], fake_registries: SimpleNamespace
) -> None:
    """A device tied to a non-zaptec identifier domain raises a HomeAssistantError."""
    fake_registries.devices["device1"] = SimpleNamespace(
        identifiers={("other_domain", "foo")}, name="Device 1"
    )

    with pytest.raises(HomeAssistantError, match="Non-zaptec device specified"):
        await handlers["stop_charging"](make_call(hass, {"device_id": "device1"}))


async def test_no_ids_specified_raises_with_missing_field(
    hass: MagicMock, handlers: dict[str, Any]
) -> None:
    """Calling a handler with none of charger_id/device_id/entity_id set names the missing field."""
    with pytest.raises(HomeAssistantError, match="Missing field 'charger_id'"):
        await handlers["stop_charging"](make_call(hass, {}))


async def test_unknown_zaptec_object_raises(hass: MagicMock, handlers: dict[str, Any]) -> None:
    """A uid with no matching zaptec object raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find zaptec object"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "charger_missing"}))


async def test_wrong_object_type_raises(
    hass: MagicMock, manager: MagicMock, add_installation: Any, handlers: dict[str, Any]
) -> None:
    """A uid resolving to the wrong zaptec object type raises a HomeAssistantError."""
    add_installation("install1")

    with pytest.raises(HomeAssistantError, match="is not a Charger"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "install1"}))


async def test_object_without_coordinator_raises(
    hass: MagicMock, manager: MagicMock, handlers: dict[str, Any]
) -> None:
    """A resolved zaptec object with no matching coordinator raises a HomeAssistantError."""
    manager.zaptec["charger1"] = make_charger("charger1")

    with pytest.raises(HomeAssistantError, match="is not available"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "charger1"}))


async def test_multiple_chargers_in_one_call_are_all_processed(
    hass: MagicMock,
    manager: MagicMock,
    add_charger: Any,
    handlers: dict[str, Any],
    fake_registries: SimpleNamespace,
) -> None:
    """A single call mixing a legacy charger_id and a device_id targets both chargers."""
    charger1, coordinator1 = add_charger("charger1")
    charger2, coordinator2 = add_charger("charger2")
    fake_registries.devices["device2"] = SimpleNamespace(
        identifiers={(DOMAIN, "charger2")}, name="Device 2"
    )

    await handlers["stop_charging"](
        make_call(hass, {"charger_id": "charger1", "device_id": ["device2"]})
    )

    charger1.command.assert_awaited_once_with("stop_charging_final")
    coordinator1.trigger_poll.assert_awaited_once()
    charger2.command.assert_awaited_once_with("stop_charging_final")
    coordinator2.trigger_poll.assert_awaited_once()


async def test_resolves_across_multiple_config_entries(
    hass: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A service call resolves a charger that lives under a second config entry's manager.

    Regression test for the bug this refactor fixes: services now register
    once at HA startup instead of once per config entry, so iter_objects must
    search every loaded entry's manager, not just the one that happened to
    exist when async_setup_services first ran.
    """
    charger1, _coordinator1 = add_charger("charger1")

    other_manager = MagicMock()
    other_charger = make_charger("charger2")
    other_coordinator = MagicMock()
    other_coordinator.trigger_poll = AsyncMock()
    other_manager.zaptec = {"charger2": other_charger}
    other_manager.device_coordinators = {"charger2": other_coordinator}

    hass.config_entries.async_entries.return_value.append(
        SimpleNamespace(runtime_data=other_manager)
    )

    await handlers["stop_charging"](make_call(hass, {"charger_id": "charger2"}))

    other_charger.command.assert_awaited_once_with("stop_charging_final")
    other_coordinator.trigger_poll.assert_awaited_once()
    charger1.command.assert_not_awaited()


async def test_unloaded_config_entry_without_runtime_data_is_skipped(
    hass: MagicMock, handlers: dict[str, Any]
) -> None:
    """An entry with no runtime_data (not currently loaded) is skipped, not crashed on."""
    hass.config_entries.async_entries.return_value.append(SimpleNamespace())

    with pytest.raises(HomeAssistantError, match="Unable to find zaptec object"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "charger_missing"}))


# ---------------------------------------------------------------------------
# Individual service handlers
# ---------------------------------------------------------------------------


async def test_stop_charging_wraps_command_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="stop_charging_final"):
        await handlers["stop_charging"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_resume_charging_sends_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """resume_charging sends the resume_charging command and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["resume_charging"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_awaited_once_with("resume_charging")
    coordinator.trigger_poll.assert_awaited_once()


async def test_resume_charging_wraps_command_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="resume_charging"):
        await handlers["resume_charging"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_authorize_charging_calls_authorize_charge(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """authorize_charging calls authorize_charge and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["authorize_charging"](make_call(hass, {"charger_id": "charger1"}))

    charger.authorize_charge.assert_awaited_once()
    coordinator.trigger_poll.assert_awaited_once()


async def test_authorize_charging_wraps_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A authorize_charge failure is wrapped in HomeAssistantError."""
    charger, coordinator = add_charger("charger1")
    charger.authorize_charge.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="authorize_charge"):
        await handlers["authorize_charging"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_deauthorize_charging_sends_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """deauthorize_charging sends the deauthorize_and_stop command and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["deauthorize_charging"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_awaited_once_with("deauthorize_and_stop")
    coordinator.trigger_poll.assert_awaited_once()


async def test_deauthorize_charging_wraps_command_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="deauthorize_and_stop"):
        await handlers["deauthorize_charging"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_restart_charger_sends_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """restart_charger sends the restart_charger command and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["restart_charger"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_awaited_once_with("restart_charger")
    coordinator.trigger_poll.assert_awaited_once()


async def test_restart_charger_wraps_command_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="restart_charger"):
        await handlers["restart_charger"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_upgrade_firmware_sends_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """upgrade_firmware sends the upgrade_firmware command and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["upgrade_firmware"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_awaited_once_with("upgrade_firmware")
    coordinator.trigger_poll.assert_awaited_once()


async def test_upgrade_firmware_wraps_command_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="upgrade_firmware"):
        await handlers["upgrade_firmware"](make_call(hass, {"charger_id": "charger1"}))

    coordinator.trigger_poll.assert_not_awaited()


async def test_limit_current_with_available_current_only(
    hass: MagicMock, manager: MagicMock, add_installation: Any, handlers: dict[str, Any]
) -> None:
    """Only availableCurrent is passed through when available_current is set."""
    installation, coordinator = add_installation("install1")

    await handlers["limit_current"](
        make_call(hass, {"installation_id": "install1", "available_current": 16})
    )

    installation.set_limit_current.assert_awaited_once_with(availableCurrent=16)
    coordinator.trigger_poll.assert_awaited_once()


async def test_limit_current_with_all_phases(
    hass: MagicMock, manager: MagicMock, add_installation: Any, handlers: dict[str, Any]
) -> None:
    """All three phase kwargs are passed through when the phase fields are set."""
    installation, coordinator = add_installation("install1")

    await handlers["limit_current"](
        make_call(
            hass,
            {
                "installation_id": "install1",
                "available_current_phase1": 10,
                "available_current_phase2": 11,
                "available_current_phase3": 12,
            },
        )
    )

    installation.set_limit_current.assert_awaited_once_with(
        availableCurrentPhase1=10, availableCurrentPhase2=11, availableCurrentPhase3=12
    )
    coordinator.trigger_poll.assert_awaited_once()


async def test_limit_current_wraps_failure(
    hass: MagicMock, manager: MagicMock, add_installation: Any, handlers: dict[str, Any]
) -> None:
    """A set_limit_current failure is wrapped in HomeAssistantError and skips the poll."""
    installation, coordinator = add_installation("install1")
    installation.set_limit_current.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="Limit current failed"):
        await handlers["limit_current"](
            make_call(hass, {"installation_id": "install1", "available_current": 16})
        )

    coordinator.trigger_poll.assert_not_awaited()


async def test_send_command_with_string_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """send_command forwards a string command and polls."""
    charger, coordinator = add_charger("charger1")

    await handlers["send_command"](
        make_call(hass, {"charger_id": "charger1", "command": "StopChargingFinal"})
    )

    charger.command.assert_awaited_once_with("StopChargingFinal")
    coordinator.trigger_poll.assert_awaited_once()


async def test_send_command_with_integer_command(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """send_command forwards an integer command."""
    charger, _coordinator = add_charger("charger1")

    await handlers["send_command"](make_call(hass, {"charger_id": "charger1", "command": 507}))

    charger.command.assert_awaited_once_with(507)


async def test_send_command_missing_command_raises(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """send_command without a command value raises before calling the charger."""
    charger, _coordinator = add_charger("charger1")

    with pytest.raises(HomeAssistantError, match="No Command received"):
        await handlers["send_command"](make_call(hass, {"charger_id": "charger1"}))

    charger.command.assert_not_awaited()


async def test_send_command_wraps_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A command failure is wrapped in HomeAssistantError and skips the poll."""
    charger, coordinator = add_charger("charger1")
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="'StopChargingFinal' failed"):
        await handlers["send_command"](
            make_call(hass, {"charger_id": "charger1", "command": "StopChargingFinal"})
        )

    coordinator.trigger_poll.assert_not_awaited()


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_charger_id_schema_requires_one_of_the_id_fields() -> None:
    """CHARGER_ID_SCHEMA rejects data with none of charger_id/device_id/entity_id."""
    with pytest.raises(vol.Invalid, match="At leas one of"):
        CHARGER_ID_SCHEMA({})


def test_charger_id_schema_accepts_entity_id() -> None:
    """CHARGER_ID_SCHEMA accepts a bare entity_id and normalizes it to a list."""
    result = CHARGER_ID_SCHEMA({"entity_id": "sensor.foo"})
    assert result["entity_id"] == ["sensor.foo"]


def test_limit_current_schema_requires_current_value() -> None:
    """LIMIT_CURRENT_SCHEMA rejects data with neither available_current nor all three phases."""
    with pytest.raises(vol.Invalid, match="Either 'available_current'"):
        LIMIT_CURRENT_SCHEMA({"installation_id": "x"})


def test_limit_current_schema_accepts_available_current() -> None:
    """LIMIT_CURRENT_SCHEMA accepts a bare available_current."""
    result = LIMIT_CURRENT_SCHEMA({"installation_id": "x", "available_current": 16})
    assert result["available_current"] == 16  # noqa: PLR2004


def test_limit_current_schema_accepts_all_three_phases() -> None:
    """LIMIT_CURRENT_SCHEMA accepts all three phase fields together."""
    result = LIMIT_CURRENT_SCHEMA(
        {
            "installation_id": "x",
            "available_current_phase1": 1,
            "available_current_phase2": 2,
            "available_current_phase3": 3,
        }
    )
    assert result["available_current_phase3"] == 3  # noqa: PLR2004


def test_limit_current_schema_rejects_partial_phases() -> None:
    """LIMIT_CURRENT_SCHEMA rejects only two of the three phase fields."""
    with pytest.raises(vol.Invalid, match="Either 'available_current'"):
        LIMIT_CURRENT_SCHEMA(
            {
                "installation_id": "x",
                "available_current_phase1": 1,
                "available_current_phase2": 2,
            }
        )


def test_limit_current_schema_rejects_current_and_phases_together() -> None:
    """LIMIT_CURRENT_SCHEMA rejects mixing available_current with the phase fields."""
    with pytest.raises(vol.Invalid, match="Either 'available_current'"):
        LIMIT_CURRENT_SCHEMA(
            {
                "installation_id": "x",
                "available_current": 16,
                "available_current_phase1": 1,
                "available_current_phase2": 2,
                "available_current_phase3": 3,
            }
        )


def test_send_command_schema_accepts_string_and_int_commands() -> None:
    """SEND_COMMAND_SCHEMA accepts both string and integer commands."""
    assert (
        SEND_COMMAND_SCHEMA({"charger_id": "x", "command": "StopChargingFinal"})["command"]
        == "StopChargingFinal"
    )
    assert SEND_COMMAND_SCHEMA({"charger_id": "x", "command": 5})["command"] == 5  # noqa: PLR2004


def test_send_command_schema_requires_command() -> None:
    """SEND_COMMAND_SCHEMA rejects data missing the command field."""
    with pytest.raises(vol.Invalid, match="required key not provided"):
        SEND_COMMAND_SCHEMA({"charger_id": "x"})


# ---------------------------------------------------------------------------
# services.yaml consistency
# ---------------------------------------------------------------------------


async def test_services_yaml_keys_match_registered_service_names(hass: MagicMock) -> None:
    """services.yaml documents exactly the services async_setup_services registers.

    A key mismatch here (e.g. a typo) means HA's UI silently falls back to an
    undocumented, field-less form for the real service, while the yaml entry
    documents a service that doesn't exist.
    """
    hass.services.has_service = MagicMock(return_value=False)
    await async_setup_services(hass)
    registered = {call.args[1] for call in hass.services.async_register.call_args_list}

    documented = set(yaml.safe_load(SERVICES_YAML_PATH.read_text()))

    assert documented == registered
