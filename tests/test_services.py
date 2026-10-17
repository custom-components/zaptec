"""Tests for services.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol
import yaml

from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.manager import ZaptecManager
import custom_components.zaptec.services as services_module
from custom_components.zaptec.services import (
    CHARGER_ID_SCHEMA,
    LIMIT_CURRENT_SCHEMA,
    SEND_COMMAND_SCHEMA,
    async_setup_services,
    service_handle_authorize_charging,
    service_handle_deauthorize_charging,
    service_handle_limit_current,
    service_handle_restart_charger,
    service_handle_resume_charging,
    service_handle_send_command,
    service_handle_stop_charging,
    service_handle_upgrade_firmware,
)
from custom_components.zaptec.zaptec import Charger
from tests.conftest import setup_integration

SERVICES_YAML_PATH = Path(services_module.__file__).with_name("services.yaml")

INSTALLATION_ID = "inst-mock-1"
CHARGER_ID = "chg-mock-1"


def make_call(hass: HomeAssistant, data: dict[str, Any]) -> ServiceCall:
    """Build a ServiceCall carrying the given data, bypassing schema validation."""
    return ServiceCall(hass, DOMAIN, "test_service", data)


@pytest.fixture
async def manager(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> ZaptecManager:
    """A real ZaptecManager from a full integration setup (registers services too)."""
    return await setup_integration(hass, mock_config_entry, mock_zaptec)


@pytest.fixture
def charger(manager: ZaptecManager) -> MagicMock:
    """The mock charger seeded by mock_zaptec, resolved through the real manager."""
    return manager.zaptec[CHARGER_ID]


@pytest.fixture
def installation(manager: ZaptecManager) -> MagicMock:
    """The mock installation seeded by mock_zaptec, resolved through the real manager."""
    return manager.zaptec[INSTALLATION_ID]


# ---------------------------------------------------------------------------
# async_setup_services
# ---------------------------------------------------------------------------


async def test_setup_registers_all_services(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """A full integration setup registers all eight zaptec services."""
    assert hass.services.async_services().get(DOMAIN, {}).keys() == {
        "stop_charging",
        "resume_charging",
        "authorize_charging",
        "deauthorize_charging",
        "restart_charger",
        "upgrade_firmware",
        "limit_current",
        "send_command",
    }


async def test_setup_is_idempotent(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """Calling async_setup_services again (e.g. a second config entry) re-registers nothing."""
    stop_charging_before = hass.services.async_services()[DOMAIN]["stop_charging"]

    await async_setup_services(hass)

    assert hass.services.async_services()[DOMAIN]["stop_charging"] is stop_charging_before


# ---------------------------------------------------------------------------
# iter_objects resolution / error paths (exercised through stop_charging)
# ---------------------------------------------------------------------------


async def test_resolves_via_legacy_charger_id(hass: HomeAssistant, charger: MagicMock) -> None:
    """A bare charger_id resolves directly to the zaptec object."""
    await service_handle_stop_charging(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("stop_charging_final")


async def test_resolves_via_device_id(hass: HomeAssistant, charger: MagicMock) -> None:
    """A device_id resolves through the device registry's zaptec identifier."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CHARGER_ID)})
    assert device is not None

    await service_handle_stop_charging(make_call(hass, {"device_id": device.id}))

    charger.command.assert_awaited_once_with("stop_charging_final")


async def test_resolves_via_entity_id(hass: HomeAssistant, charger: MagicMock) -> None:
    """An entity_id resolves through the entity registry's device, then the device registry."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CHARGER_ID)})
    assert device is not None
    entity_entry = next(
        e for e in er.async_get(hass).entities.values() if e.device_id == device.id
    )

    await service_handle_stop_charging(make_call(hass, {"entity_id": entity_entry.entity_id}))

    charger.command.assert_awaited_once_with("stop_charging_final")


async def test_entity_id_not_found_raises(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """An unknown entity_id raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find entity"):
        await service_handle_stop_charging(make_call(hass, {"entity_id": "sensor.missing"}))


async def test_entity_without_device_raises(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """An entity with no device_id raises a HomeAssistantError."""
    entry = er.async_get(hass).async_get_or_create("sensor", "other", "no_device_unique_id")

    with pytest.raises(HomeAssistantError, match="doesn't have a device"):
        await service_handle_stop_charging(make_call(hass, {"entity_id": entry.entity_id}))


async def test_device_id_not_found_raises(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """An unknown device_id raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find device"):
        await service_handle_stop_charging(make_call(hass, {"device_id": "device_missing"}))


async def test_device_without_identifiers_raises(
    hass: HomeAssistant, manager: ZaptecManager, mock_config_entry: MockConfigEntry
) -> None:
    """A device with no zaptec identifiers raises a HomeAssistantError."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id, connections={("mac", "00:00:00:00:00:00")}
    )

    with pytest.raises(HomeAssistantError, match="Unable to find identifiers"):
        await service_handle_stop_charging(make_call(hass, {"device_id": device.id}))


async def test_device_with_non_zaptec_identifier_raises(
    hass: HomeAssistant, manager: ZaptecManager, mock_config_entry: MockConfigEntry
) -> None:
    """A device tied to a non-zaptec identifier domain raises a HomeAssistantError."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id, identifiers={("other_domain", "foo")}
    )

    with pytest.raises(HomeAssistantError, match="Non-zaptec device specified"):
        await service_handle_stop_charging(make_call(hass, {"device_id": device.id}))


async def test_no_ids_specified_raises_with_missing_field(
    hass: HomeAssistant, manager: ZaptecManager
) -> None:
    """Calling a handler with none of charger_id/device_id/entity_id names the missing field."""
    with pytest.raises(HomeAssistantError, match="Missing field 'charger_id'"):
        await service_handle_stop_charging(make_call(hass, {}))


async def test_unknown_zaptec_object_raises(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """A uid with no matching zaptec object raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Unable to find zaptec object"):
        await service_handle_stop_charging(make_call(hass, {"charger_id": "charger_missing"}))


async def test_wrong_object_type_raises(hass: HomeAssistant, manager: ZaptecManager) -> None:
    """A uid resolving to the wrong zaptec object type raises a HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="is not a Charger"):
        await service_handle_stop_charging(make_call(hass, {"charger_id": INSTALLATION_ID}))


async def test_object_without_coordinator_raises(
    hass: HomeAssistant, manager: ZaptecManager
) -> None:
    """A resolved zaptec object with no matching coordinator raises a HomeAssistantError."""
    del manager.device_coordinators[CHARGER_ID]

    with pytest.raises(HomeAssistantError, match="is not available"):
        await service_handle_stop_charging(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_resolves_across_multiple_config_entries(
    hass: HomeAssistant, manager: ZaptecManager
) -> None:
    """A service call resolves a charger under a second config entry's manager."""
    other_charger_id = "chg-mock-2"
    other_charger = MagicMock(spec=Charger)
    other_charger.id = other_charger_id
    other_charger.command = AsyncMock()
    other_coordinator = MagicMock()
    other_coordinator.trigger_poll = AsyncMock()

    other_manager = MagicMock()
    other_manager.zaptec = {other_charger_id: other_charger}
    other_manager.device_coordinators = {other_charger_id: other_coordinator}

    other_entry = MockConfigEntry(domain=DOMAIN, title="Second account")
    other_entry.add_to_hass(hass)
    other_entry.runtime_data = other_manager

    await service_handle_stop_charging(make_call(hass, {"charger_id": other_charger_id}))

    other_charger.command.assert_awaited_once_with("stop_charging_final")


async def test_unloaded_config_entry_without_runtime_data_is_skipped(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """An entry with no runtime_data (never set up) is skipped, not crashed on."""
    other_entry = MockConfigEntry(domain=DOMAIN, title="Not set up")
    other_entry.add_to_hass(hass)

    await service_handle_stop_charging(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("stop_charging_final")


# ---------------------------------------------------------------------------
# Individual service handlers
# ---------------------------------------------------------------------------


async def test_stop_charging_wraps_command_failure(
    hass: HomeAssistant, charger: MagicMock, manager: ZaptecManager
) -> None:
    """A command failure is wrapped in HomeAssistantError and no poll is triggered."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="stop_charging_final"):
        await service_handle_stop_charging(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_resume_charging_sends_command(hass: HomeAssistant, charger: MagicMock) -> None:
    """resume_charging sends the resume_charging command."""
    await service_handle_resume_charging(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("resume_charging")


async def test_resume_charging_wraps_command_failure(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """A command failure is wrapped in HomeAssistantError."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="resume_charging"):
        await service_handle_resume_charging(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_authorize_charging_calls_authorize_charge(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """authorize_charging calls authorize_charge."""
    await service_handle_authorize_charging(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.authorize_charge.assert_awaited_once()


async def test_authorize_charging_wraps_failure(hass: HomeAssistant, charger: MagicMock) -> None:
    """An authorize_charge failure is wrapped in HomeAssistantError."""
    charger.authorize_charge.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="authorize_charge"):
        await service_handle_authorize_charging(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_deauthorize_charging_sends_command(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """deauthorize_charging sends the deauthorize_and_stop command."""
    await service_handle_deauthorize_charging(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("deauthorize_and_stop")


async def test_deauthorize_charging_wraps_command_failure(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """A command failure is wrapped in HomeAssistantError."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="deauthorize_and_stop"):
        await service_handle_deauthorize_charging(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_restart_charger_sends_command(hass: HomeAssistant, charger: MagicMock) -> None:
    """restart_charger sends the restart_charger command."""
    await service_handle_restart_charger(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("restart_charger")


async def test_restart_charger_wraps_command_failure(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """A command failure is wrapped in HomeAssistantError."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="restart_charger"):
        await service_handle_restart_charger(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_upgrade_firmware_sends_command(hass: HomeAssistant, charger: MagicMock) -> None:
    """upgrade_firmware sends the upgrade_firmware command."""
    await service_handle_upgrade_firmware(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_awaited_once_with("upgrade_firmware")


async def test_upgrade_firmware_wraps_command_failure(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """A command failure is wrapped in HomeAssistantError."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="upgrade_firmware"):
        await service_handle_upgrade_firmware(make_call(hass, {"charger_id": CHARGER_ID}))


async def test_limit_current_with_available_current_only(
    hass: HomeAssistant, installation: MagicMock
) -> None:
    """Only availableCurrent is passed through when available_current is set."""
    await service_handle_limit_current(
        make_call(hass, {"installation_id": INSTALLATION_ID, "available_current": 16})
    )

    installation.set_limit_current.assert_awaited_once_with(availableCurrent=16)


async def test_limit_current_with_all_phases(
    hass: HomeAssistant, installation: MagicMock
) -> None:
    """All three phase kwargs are passed through when the phase fields are set."""
    await service_handle_limit_current(
        make_call(
            hass,
            {
                "installation_id": INSTALLATION_ID,
                "available_current_phase1": 10,
                "available_current_phase2": 11,
                "available_current_phase3": 12,
            },
        )
    )

    installation.set_limit_current.assert_awaited_once_with(
        availableCurrentPhase1=10, availableCurrentPhase2=11, availableCurrentPhase3=12
    )


async def test_limit_current_wraps_failure(hass: HomeAssistant, installation: MagicMock) -> None:
    """A set_limit_current failure is wrapped in HomeAssistantError."""
    installation.set_limit_current.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="Limit current failed"):
        await service_handle_limit_current(
            make_call(hass, {"installation_id": INSTALLATION_ID, "available_current": 16})
        )


async def test_send_command_with_string_command(hass: HomeAssistant, charger: MagicMock) -> None:
    """send_command forwards a string command."""
    await service_handle_send_command(
        make_call(hass, {"charger_id": CHARGER_ID, "command": "StopChargingFinal"})
    )

    charger.command.assert_awaited_once_with("StopChargingFinal")


async def test_send_command_with_integer_command(hass: HomeAssistant, charger: MagicMock) -> None:
    """send_command forwards an integer command."""
    await service_handle_send_command(make_call(hass, {"charger_id": CHARGER_ID, "command": 507}))

    charger.command.assert_awaited_once_with(507)


async def test_send_command_missing_command_raises(
    hass: HomeAssistant, charger: MagicMock
) -> None:
    """send_command without a command value raises before calling the charger."""
    with pytest.raises(HomeAssistantError, match="No Command received"):
        await service_handle_send_command(make_call(hass, {"charger_id": CHARGER_ID}))

    charger.command.assert_not_awaited()


async def test_send_command_wraps_failure(hass: HomeAssistant, charger: MagicMock) -> None:
    """A command failure is wrapped in HomeAssistantError."""
    charger.command.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="'StopChargingFinal' failed"):
        await service_handle_send_command(
            make_call(hass, {"charger_id": CHARGER_ID, "command": "StopChargingFinal"})
        )


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


async def test_services_yaml_keys_match_registered_service_names(
    hass: HomeAssistant, manager: ZaptecManager
) -> None:
    """services.yaml documents exactly the services async_setup_services registers."""
    registered = set(hass.services.async_services()[DOMAIN])
    documented = set(yaml.safe_load(SERVICES_YAML_PATH.read_text()))

    assert documented == registered
