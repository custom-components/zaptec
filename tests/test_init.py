"""Tests for custom_components.zaptec.__init__."""

from collections.abc import Collection
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec import _cleanup_devices, _config_entry_error
from custom_components.zaptec.const import CONF_CHARGERS, CONF_MANUAL_SELECT, DOMAIN
from custom_components.zaptec.entity import ZaptecBaseEntity
from custom_components.zaptec.manager import ZaptecEntityDescription, ZaptecManager
from custom_components.zaptec.zaptec.api import Zaptec
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
)
from tests.conftest import make_charger, make_installation, setup_integration


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        # Bad credentials are non-recoverable -> re-auth flow.
        (AuthenticationError("bad"), ConfigEntryAuthFailed),
        # Connection/timeout are recoverable -> HA retries setup.
        (RequestTimeoutError("slow"), ConfigEntryNotReady),
        (RequestConnectionError("down"), ConfigEntryNotReady),
        # Transient server statuses are recoverable -> HA retries setup.
        (RequestError("unavailable", HTTPStatus.SERVICE_UNAVAILABLE), ConfigEntryNotReady),
        (RequestError("too many", HTTPStatus.TOO_MANY_REQUESTS), ConfigEntryNotReady),
        # Other HTTP errors stay permanent.
        (RequestError("forbidden", HTTPStatus.FORBIDDEN), ConfigEntryError),
        (RequestError("not found", HTTPStatus.NOT_FOUND), ConfigEntryError),
    ],
)
def test_config_entry_error_mapping(err: Exception, expected: type[Exception]) -> None:
    """Setup login errors map to the right Home Assistant config-entry error."""
    assert isinstance(_config_entry_error(err), expected)


async def test_setup_entry_creates_manager_and_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    device_registry: dr.DeviceRegistry,
    enable_custom_integrations: None,
) -> None:
    """A full setup wires up the manager and registers at least one entity."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert isinstance(manager, ZaptecManager)
    assert mock_config_entry.runtime_data is manager
    # Matches because mock_zaptec seeds "Mock Charger"/"Mock Home", which HA
    # slugifies into "mock..." entity_ids. Update if that seed naming changes.
    states = [s for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("mock")]
    assert states, "expected at least one zaptec entity to be created"

    assert device_registry.async_get_device(identifiers={(DOMAIN, "chg-mock-1")}) is not None


async def test_setup_entry_charger_with_installation_has_via_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A charger belonging to an installation gets a `via_device` pointing at it.

    Asserted on the DeviceInfo rather than the registry's `via_device_id`: HA resolves
    `via_device` when the device is created, and `mock_zaptec` adds only one
    installation entity, late, so the installation device may not exist yet when the
    charger is registered. Asserting on the registry here is measurably flaky.
    """
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    entities = manager.create_entities_from_zaptec(
        [], [ZaptecEntityDescription(key="operating_mode", cls=ZaptecBaseEntity)]
    )
    charger_entity = next(e for e in entities if e.zaptec_obj.id == "chg-mock-1")
    assert charger_entity.device_info["via_device"] == (DOMAIN, "inst-mock-1")


async def test_setup_entry_charger_without_installation_has_no_via_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A charger with no installation gets a DeviceInfo without `via_device`."""
    mock_zaptec.chargers[0].installation = None

    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    entities = manager.create_entities_from_zaptec(
        [], [ZaptecEntityDescription(key="operating_mode", cls=ZaptecBaseEntity)]
    )
    charger_entity = next(e for e in entities if e.zaptec_obj.id == "chg-mock-1")
    assert "via_device" not in charger_entity.device_info


def _zaptec_double(chargers: list[MagicMock], objects: dict[str, MagicMock]) -> MagicMock:
    """A minimal Zaptec double for `first_time_setup`, which only needs iteration."""
    zaptec = MagicMock(spec=Zaptec)
    zaptec.__iter__.side_effect = lambda: iter(objects)
    zaptec.build = AsyncMock(return_value=None)
    zaptec.chargers = chargers
    return zaptec


async def test_first_time_setup_keeps_installation_of_selected_charger() -> None:
    """A selected charger's installation is tracked, even when the charger has none.

    The `is not None` check matters here rather than truthiness: `Installation` is a
    `Mapping`, so an installation carrying no attributes is falsy. Since
    `_cleanup_devices` deletes untracked devices, dropping one on falsiness
    would reap a live installation's device.
    """
    empty_install = make_installation({"id": "inst-empty"})
    empty_install.__len__.return_value = 0  # falsy, as a real attribute-less one is
    with_install = make_charger({"id": "chg-a"}, installation=empty_install)
    without_install = make_charger({"id": "chg-b"}, installation=None)

    tracked, all_present = await ZaptecManager.first_time_setup(
        _zaptec_double(
            [with_install, without_install],
            {"inst-empty": empty_install, "chg-a": with_install, "chg-b": without_install},
        ),
        {"chg-a", "chg-b"},
    )

    assert not empty_install, "fixture must be falsy for this test to mean anything"
    assert tracked == {"chg-a", "chg-b", "inst-empty"}
    assert all_present is True


@pytest.mark.parametrize(
    ("configured", "expected_tracked", "expected_all_present"),
    [
        # Track-all mode keeps every object in the account.
        (None, {"inst-1", "chg-a", "chg-b"}, True),
        # A charger present in the account but not selected is simply not tracked.
        ({"chg-a"}, {"chg-a", "inst-1"}, True),
        # A selected charger absent from the API response -> flag goes False, and the
        # chargers that *were* returned are still tracked.
        ({"chg-a", "chg-absent"}, {"chg-a", "inst-1"}, False),
        # Nothing selected that exists -> nothing tracked at all.
        ({"chg-absent"}, set(), False),
        # A selected id that exists but isn't a charger can never reach `keep`. It has
        # to count as absent: otherwise this returns an empty tracked set while
        # reporting the account complete, which authorizes deleting every device.
        ({"inst-1"}, set(), False),
        # A list, not a set: this is what entry.data actually holds.
        (["chg-a"], {"chg-a", "inst-1"}, True),
    ],
)
async def test_first_time_setup_tracked_devices(
    configured: Collection[str] | None,
    expected_tracked: set[str],
    expected_all_present: bool,
) -> None:
    """`first_time_setup` reports both what to track and whether the account was complete."""
    installation = make_installation({"id": "inst-1"})
    charger_a = make_charger({"id": "chg-a"}, installation=installation)
    charger_b = make_charger({"id": "chg-b"}, installation=installation)
    objects = {"inst-1": installation, "chg-a": charger_a, "chg-b": charger_b}

    tracked, all_present = await ZaptecManager.first_time_setup(
        _zaptec_double([charger_a, charger_b], objects), configured
    )

    assert tracked == expected_tracked
    assert all_present is expected_all_present


def _add_device(
    device_registry: dr.DeviceRegistry, entry: MockConfigEntry, zaptec_id: str
) -> dr.DeviceEntry:
    """Register a device identified the way manager.py's device_info does."""
    return device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, zaptec_id)},
        name=zaptec_id,
    )


def _add_entity(
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
    device: dr.DeviceEntry,
    unique_id: str,
) -> str:
    """Register one sensor entity against `device` and return its entity_id."""
    return entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=unique_id,
        device_id=device.id,
        config_entry=entry,
    ).entity_id


async def test_cleanup_removes_device_with_no_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A device with zero registered entities is removed outright."""
    mock_config_entry.add_to_hass(hass)
    device = _add_device(device_registry, mock_config_entry, "charger-empty")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices={"charger-empty"},  # tracked, but still has no entities
        circuit_ids=set(),
        check_untracked=True,
    )

    assert device_registry.async_get(device.id) is None


async def test_cleanup_removes_deprecated_circuit_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A device matching a known Circuit id is removed along with its entities.

    Tracked on purpose, so the Circuit match is the only thing that can remove it -
    otherwise the untracked branch would carry this test and deleting the Circuit
    check would go unnoticed.
    """
    mock_config_entry.add_to_hass(hass)
    device = _add_device(device_registry, mock_config_entry, "circuit-123")
    entity_id = _add_entity(entity_registry, mock_config_entry, device, "circuit-123_power")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices={"circuit-123"},
        circuit_ids={"circuit-123"},
        check_untracked=True,
    )

    assert entity_registry.async_get(entity_id) is None
    assert device_registry.async_get(device.id) is None


@pytest.mark.parametrize("zaptec_id", ["charger-kept", "installation-1"])
async def test_cleanup_keeps_tracked_device_with_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    zaptec_id: str,
) -> None:
    """A tracked device with entities is left alone, charger or installation alike.

    `tracked_devices` holds both charger ids and their installation ids
    (`first_time_setup` keeps an installation whenever any of its chargers is
    selected), so installations go through the same tracked-device check.
    """
    mock_config_entry.add_to_hass(hass)
    device = _add_device(device_registry, mock_config_entry, zaptec_id)
    entity_id = _add_entity(entity_registry, mock_config_entry, device, f"{zaptec_id}_power")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices={zaptec_id},
        circuit_ids=set(),
        check_untracked=True,
    )

    assert entity_registry.async_get(entity_id) is not None
    assert device_registry.async_get(device.id) is not None


async def test_cleanup_removes_deselected_charger_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A charger deselected via reconfigure loses its device and entities.

    `tracked_devices` already excluded it, so no new entities were created - but its
    entity-registry entries from the prior session were never removed, leaving
    `dev_entities` non-empty and the device untouched.
    """
    mock_config_entry.add_to_hass(hass)
    stale = _add_device(device_registry, mock_config_entry, "charger-stale")
    stale_entity = _add_entity(entity_registry, mock_config_entry, stale, "charger-stale_power")
    kept = _add_device(device_registry, mock_config_entry, "charger-kept")
    kept_entity = _add_entity(entity_registry, mock_config_entry, kept, "charger-kept_power")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices={"charger-kept"},  # charger-stale was deselected
        circuit_ids=set(),
        check_untracked=True,
    )

    assert entity_registry.async_get(stale_entity) is None
    assert device_registry.async_get(stale.id) is None
    assert entity_registry.async_get(kept_entity) is not None
    assert device_registry.async_get(kept.id) is not None


async def test_cleanup_skips_untracked_removal_when_selection_incomplete(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An untracked device survives when `check_untracked` is False.

    If the Zaptec API returns a partial account this session, a charger the user
    still has selected drops out of `tracked_devices`. Without this guard that blip
    would be mistaken for a deselection and permanently delete the device.
    """
    mock_config_entry.add_to_hass(hass)
    device = _add_device(device_registry, mock_config_entry, "charger-maybe-stale")
    entity_id = _add_entity(
        entity_registry, mock_config_entry, device, "charger-maybe-stale_power"
    )

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices=set(),  # would look untracked...
        circuit_ids=set(),
        check_untracked=False,  # ...but the API response was incomplete this session
    )

    assert entity_registry.async_get(entity_id) is not None
    assert device_registry.async_get(device.id) is not None


async def test_cleanup_removes_multi_identifier_device_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A device is removed once even when several of its ids match.

    Zaptec devices carry a single identifier today, but nothing enforces that, and
    `async_remove_device` pops without a default - a second removal would raise
    KeyError and abort setup.
    """
    mock_config_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "charger-stale-a"), (DOMAIN, "charger-stale-b")},
        name="two-identifier device",
    )
    entity_id = _add_entity(entity_registry, mock_config_entry, device, "two_id_power")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices=set(),  # both identifiers are untracked
        circuit_ids=set(),
        check_untracked=True,
    )

    assert entity_registry.async_get(entity_id) is None
    assert device_registry.async_get(device.id) is None


async def test_cleanup_ignores_identifiers_from_other_domains(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An identifier from another integration never triggers removal.

    A foreign id is by definition absent from `tracked_devices`, so without the
    domain guard the untracked check would delete a device we still track.
    """
    mock_config_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "chg-mock-1"), ("other_domain", "foreign-id")},
        name="shared device",
    )
    entity_id = _add_entity(entity_registry, mock_config_entry, device, "shared_power")

    _cleanup_devices(
        hass,
        mock_config_entry,
        tracked_devices={"chg-mock-1"},
        circuit_ids=set(),
        check_untracked=True,
    )

    assert entity_registry.async_get(entity_id) is not None
    assert device_registry.async_get(device.id) is not None


@pytest.mark.parametrize(
    ("manual_select", "configured", "expect_removed"),
    [
        # Manual-select mode with every selection present -> the leftover goes.
        (True, ["chg-mock-1"], True),
        # A selected charger missing from the API response this session -> keep everything.
        (True, ["chg-mock-1", "chg-gone-from-api"], False),
        # Track-all mode has no deselection to detect, so nothing is ever reaped.
        (False, None, False),
    ],
)
async def test_setup_entry_cleans_up_deselected_charger(
    hass: HomeAssistant,
    mock_zaptec: MagicMock,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    enable_custom_integrations: None,
    manual_select: bool,
    configured: list[str] | None,
    expect_removed: bool,
) -> None:
    """End-to-end: a full setup reaps a previously-selected charger's leftover device."""
    data: dict[str, object] = {CONF_USERNAME: "user", CONF_PASSWORD: "pass"}
    if manual_select:
        data |= {CONF_MANUAL_SELECT: True, CONF_CHARGERS: configured}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock Zaptec",
        data=data,
        entry_id="mock_entry_1",
    )
    entry.add_to_hass(hass)
    # Left behind by a previous session, for a charger since deselected.
    stale = _add_device(device_registry, entry, "chg-deselected-1")
    stale_entity = _add_entity(entity_registry, entry, stale, "chg-deselected-1_power")

    with patch("custom_components.zaptec.Zaptec", return_value=mock_zaptec):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (device_registry.async_get(stale.id) is None) is expect_removed
    assert (entity_registry.async_get(stale_entity) is None) is expect_removed
    # The still-selected charger keeps its device either way.
    assert device_registry.async_get_device(identifiers={(DOMAIN, "chg-mock-1")}) is not None
    # So does its installation: `first_time_setup` keeps the installation of any
    # selected charger, so it must never be seen as untracked and reaped.
    assert device_registry.async_get_device(identifiers={(DOMAIN, "inst-mock-1")}) is not None
