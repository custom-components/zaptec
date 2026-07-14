"""Tests for custom_components.zaptec.__init__."""

from http import HTTPStatus
from unittest.mock import MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
import pytest

from custom_components.zaptec import (
    _cleanup_stale_devices,
    _config_entry_error,
    _should_check_untracked,
)
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
)


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        # Bad credentials are non-recoverable -> re-auth flow.
        (AuthenticationError("bad"), ConfigEntryAuthFailed),
        # Connection/timeout are recoverable -> HA retries setup.
        (RequestTimeoutError("slow"), ConfigEntryNotReady),
        (RequestConnectionError("down"), ConfigEntryNotReady),
        # Transient server statuses are recoverable -> HA retries setup (issue #392).
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


def _mock_registries(
    device_entries: list, entities_by_device: dict
) -> tuple[MagicMock, MagicMock]:
    """Patch dr.async_get/er.async_get and the two registry lookup helpers.

    Returns (device_registry_mock, entity_registry_mock) so callers can
    assert on async_remove_device/async_remove calls.
    """
    device_registry = MagicMock()
    entity_registry = MagicMock()

    def entries_for_device(
        _entity_registry: MagicMock, device_id: str, include_disabled_entities: bool = True
    ) -> list:
        return entities_by_device.get(device_id, [])

    patch("custom_components.zaptec.dr.async_get", return_value=device_registry).start()
    patch("custom_components.zaptec.er.async_get", return_value=entity_registry).start()
    patch(
        "custom_components.zaptec.dr.async_entries_for_config_entry",
        return_value=device_entries,
    ).start()
    patch(
        "custom_components.zaptec.er.async_entries_for_device",
        side_effect=entries_for_device,
    ).start()

    return device_registry, entity_registry


@pytest.fixture(autouse=True)
def _stop_patches():
    """Undo any patch.start() calls made via _mock_registries after each test."""
    yield
    patch.stopall()


def _device(device_id: str, zaptec_id: str) -> MagicMock:
    dev = MagicMock()
    dev.id = device_id
    dev.identifiers = {("zaptec", zaptec_id)}
    return dev


def _entity(entity_id: str) -> MagicMock:
    ent = MagicMock()
    ent.entity_id = entity_id
    return ent


def test_cleanup_removes_device_with_no_entities() -> None:
    """A device with zero registered entities is removed outright."""
    empty_device = _device("dev-empty", "charger-empty")
    device_registry, entity_registry = _mock_registries(
        device_entries=[empty_device],
        entities_by_device={"dev-empty": []},
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices={"charger-empty"},  # tracked, but still has no entities
        circuit_ids=set(),
        check_untracked=True,
    )

    device_registry.async_remove_device.assert_called_once_with("dev-empty")
    entity_registry.async_remove.assert_not_called()


def test_cleanup_removes_deprecated_circuit_device() -> None:
    """A device matching a known Circuit id is removed along with its entities."""
    circuit_device = _device("dev-circuit", "circuit-123")
    circuit_entity = _entity("sensor.circuit_123_power")
    device_registry, entity_registry = _mock_registries(
        device_entries=[circuit_device],
        entities_by_device={"dev-circuit": [circuit_entity]},
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices=set(),
        circuit_ids={"circuit-123"},
        check_untracked=True,
    )

    entity_registry.async_remove.assert_called_once_with("sensor.circuit_123_power")
    device_registry.async_remove_device.assert_called_once_with("dev-circuit")


def test_cleanup_keeps_tracked_device_with_entities() -> None:
    """A device that is tracked and has entities is left alone."""
    kept_device = _device("dev-kept", "charger-kept")
    kept_entity = _entity("sensor.kept_charger_power")
    device_registry, entity_registry = _mock_registries(
        device_entries=[kept_device],
        entities_by_device={"dev-kept": [kept_entity]},
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices={"charger-kept"},
        circuit_ids=set(),
        check_untracked=True,
    )

    device_registry.async_remove_device.assert_not_called()
    entity_registry.async_remove.assert_not_called()


def test_cleanup_removes_deselected_charger_device() -> None:
    """Issue #272: a charger deselected via reconfigure loses its device and entities.

    Before this fix, `tracked_devices` correctly excludes the deselected
    charger (so no new entities are created for it, per the #274/#275 fix),
    but its old entity-registry entries from the prior session were never
    explicitly removed, so `dev_entities` stayed non-empty and the device
    was never cleaned up.
    """
    stale_device = _device("dev-stale", "charger-stale")
    kept_device = _device("dev-kept", "charger-kept")
    stale_entity = _entity("sensor.stale_charger_power")
    kept_entity = _entity("sensor.kept_charger_power")

    device_registry, entity_registry = _mock_registries(
        device_entries=[stale_device, kept_device],
        entities_by_device={
            "dev-stale": [stale_entity],
            "dev-kept": [kept_entity],
        },
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices={"charger-kept"},  # charger-stale was deselected
        circuit_ids=set(),
        check_untracked=True,
    )

    entity_registry.async_remove.assert_called_once_with("sensor.stale_charger_power")
    device_registry.async_remove_device.assert_called_once_with("dev-stale")


def test_cleanup_skips_untracked_removal_when_selection_incomplete() -> None:
    """A device not in tracked_devices is left alone when check_untracked is False.

    This guards against a real risk found in review: if the Zaptec API returns
    a partial account this session (outage, transient blip), a charger the
    user genuinely still has selected can silently drop out of
    tracked_devices. Without this guard, that transient blip would be
    mistaken for the user deselecting the charger and its device+entities
    would be permanently deleted.
    """
    maybe_stale_device = _device("dev-maybe-stale", "charger-maybe-stale")
    maybe_stale_entity = _entity("sensor.maybe_stale_charger_power")

    device_registry, entity_registry = _mock_registries(
        device_entries=[maybe_stale_device],
        entities_by_device={"dev-maybe-stale": [maybe_stale_entity]},
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices=set(),  # would look untracked...
        circuit_ids=set(),
        check_untracked=False,  # ...but the API response was incomplete this session
    )

    device_registry.async_remove_device.assert_not_called()
    entity_registry.async_remove.assert_not_called()


def test_cleanup_keeps_tracked_installation_device() -> None:
    """An installation device (not a charger) with a tracked id is left alone.

    tracked_devices holds both charger ids and their installation ids
    (ZaptecManager.first_time_setup keeps an installation whenever any of its
    chargers is selected) - this documents that installations go through the
    same tracked-device check as chargers.
    """
    installation_device = _device("dev-installation", "installation-1")
    installation_entity = _entity("sensor.installation_1_power")

    device_registry, entity_registry = _mock_registries(
        device_entries=[installation_device],
        entities_by_device={"dev-installation": [installation_entity]},
    )

    _cleanup_stale_devices(
        MagicMock(),
        MagicMock(entry_id="entry1"),
        tracked_devices={"installation-1"},
        circuit_ids=set(),
        check_untracked=True,
    )

    device_registry.async_remove_device.assert_not_called()
    entity_registry.async_remove.assert_not_called()


@pytest.mark.parametrize(
    ("configured_chargers", "all_selected_present", "expected"),
    [
        # Track-all mode: never check untracked, regardless of all_selected_present
        # (which is always True in this mode anyway - included for defensiveness).
        (None, True, False),
        (None, False, False),
        # Manual-select mode, every selected charger confirmed present -> safe to check.
        ({"charger-a"}, True, True),
        # Manual-select mode, API response was partial this session -> must not check.
        ({"charger-a"}, False, False),
    ],
)
def test_should_check_untracked(
    configured_chargers: set[str] | None,
    all_selected_present: bool,
    expected: bool,
) -> None:
    """Untracked-device removal is only ever safe in confirmed manual-select mode."""
    assert _should_check_untracked(configured_chargers, all_selected_present) is expected
