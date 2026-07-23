"""Tests for coordinator.py."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.zaptec.const import (
    DOMAIN,
    ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS,
    ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS,
)
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import Charger, Installation, Zaptec, ZaptecApiError


@pytest.fixture
def manager() -> MagicMock:
    """A fake ZaptecManager exposing only what the coordinator touches."""
    mgr = MagicMock()
    mgr.zaptec = MagicMock(spec=Zaptec)
    mgr.device_coordinators = {}
    mgr.tracked_devices = set()
    return mgr


def make_options(**overrides: Any) -> ZaptecUpdateOptions:
    """Build ZaptecUpdateOptions with sane defaults, overridable per test."""
    defaults: dict[str, Any] = {
        "name": "test",
        "update_interval": 600,
        "charging_update_interval": None,
        "tracked_devices": {"dev1"},
        "poll_args": {},
        "zaptec_object": None,
    }
    defaults.update(overrides)
    return ZaptecUpdateOptions(**defaults)


async def test_init_sets_name_and_default_interval(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that coordinator init sets name and update interval correctly."""
    options = make_options(name="MyInstall", update_interval=300)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    assert coordinator.name == f"{DOMAIN}-myinstall"
    assert coordinator.update_interval == timedelta(seconds=300)
    assert coordinator.zaptec is manager.zaptec


async def test_init_raises_if_charging_interval_without_charger(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that charging interval requires a Charger object."""
    options = make_options(
        charging_update_interval=60,
        zaptec_object=MagicMock(spec=Installation),
    )

    with pytest.raises(ValueError, match="Charging update interval requires a Charger object"):
        ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


async def test_init_accepts_charging_interval_with_charger(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that charging interval is accepted when a Charger object is provided."""
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    options = make_options(charging_update_interval=60, zaptec_object=charger)

    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    assert coordinator._charging_update_interval == timedelta(seconds=60)  # noqa: SLF001


async def test_set_update_interval_switches_between_charging_and_default(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that set_update_interval switches between charging and default intervals."""
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    charger.qual_id = "Charger[abc123]"
    options = make_options(
        update_interval=600,
        charging_update_interval=60,
        zaptec_object=charger,
    )
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )
    assert coordinator.update_interval == timedelta(seconds=600)

    charger.is_charging.return_value = True
    coordinator.set_update_interval()
    assert coordinator.update_interval == timedelta(seconds=60)

    charger.is_charging.return_value = False
    coordinator.set_update_interval()
    assert coordinator.update_interval == timedelta(seconds=600)


async def test_set_update_interval_is_noop_when_unchanged(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that set_update_interval doesn't reschedule when interval is unchanged."""
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    options = make_options(
        update_interval=600,
        charging_update_interval=60,
        zaptec_object=charger,
    )
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch.object(coordinator, "_schedule_refresh") as mock_schedule:
        coordinator.set_update_interval()
        mock_schedule.assert_not_called()


async def test_async_update_data_polls_zaptec_with_options(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that _async_update_data calls zaptec.poll with correct parameters."""
    manager.zaptec.poll = AsyncMock()
    options = make_options(
        tracked_devices={"dev1", "dev2"},
        poll_args={"poll_state": True},
    )
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    await coordinator._async_update_data()  # noqa: SLF001

    manager.zaptec.poll.assert_awaited_once_with({"dev1", "dev2"}, poll_state=True)


async def test_async_update_data_raises_update_failed_on_api_error(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that _async_update_data raises UpdateFailed on ZaptecApiError."""
    api_error = ZaptecApiError("boom")
    manager.zaptec.poll = AsyncMock(side_effect=api_error)
    options = make_options()
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coordinator._async_update_data()  # noqa: SLF001
    assert exc_info.value.__cause__ is api_error


async def test_trigger_poll_charger_uses_charger_delays(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that _trigger_poll sleeps/refreshes once per charger delay."""
    charger = MagicMock(spec=Charger)
    charger.qual_id = "Charger[abc123]"
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(coordinator, "async_refresh", AsyncMock()) as mock_refresh,
    ):
        await coordinator._trigger_poll(charger)  # noqa: SLF001

    assert mock_sleep.await_count == len(ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS)
    assert mock_refresh.await_count == len(ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS)


async def test_trigger_poll_installation_also_triggers_tracked_children(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that _trigger_poll on an Installation also polls tracked child chargers."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    installation = MagicMock(spec=Installation)
    installation.qual_id = "Installation[abc123]"
    installation.chargers = [charger]
    manager.tracked_devices = {"charger1"}

    child_coordinator = MagicMock()
    child_coordinator.trigger_poll = AsyncMock()
    manager.device_coordinators = {"charger1": child_coordinator}

    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(coordinator, "async_refresh", AsyncMock()) as mock_refresh,
    ):
        await coordinator._trigger_poll(installation)  # noqa: SLF001

    assert mock_sleep.await_count == len(ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS)
    assert mock_refresh.await_count == len(ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS)
    child_coordinator.trigger_poll.assert_awaited_once()


async def test_trigger_poll_installation_skips_untracked_children(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that _trigger_poll skips children that aren't in tracked_devices."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    installation = MagicMock(spec=Installation)
    installation.qual_id = "Installation[abc123]"
    installation.chargers = [charger]
    manager.tracked_devices = set()  # charger1 is not tracked

    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()),
        patch.object(coordinator, "async_refresh", AsyncMock()),
    ):
        # Would raise KeyError from manager.device_coordinators[charger.id] if
        # the untracked charger were not filtered out first.
        await coordinator._trigger_poll(installation)  # noqa: SLF001


async def test_trigger_poll_noop_without_zaptec_object(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that trigger_poll is a no-op when there is no zaptec_object."""
    options = make_options(zaptec_object=None)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    await coordinator.trigger_poll()

    assert coordinator._trigger_task is None  # noqa: SLF001


async def test_trigger_poll_cancels_inflight_task_before_starting_new_one(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Test that a second trigger_poll cancels the in-flight task and starts a new one."""
    charger = MagicMock(spec=Charger)
    charger.qual_id = "Charger[abc123]"
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    call_count = 0
    first_started = asyncio.Event()

    async def fake_trigger_poll(_zaptec_obj: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await asyncio.Event().wait()  # blocks forever, until cancelled

    with patch.object(coordinator, "_trigger_poll", fake_trigger_poll):
        await coordinator.trigger_poll()
        await first_started.wait()
        first_task = coordinator._trigger_task  # noqa: SLF001
        assert first_task is not None
        assert not first_task.done()

        await coordinator.trigger_poll()

        assert first_task.cancelled()
        # Two loop iterations are required here: the first lets the second
        # task run to completion; the second lets its done-callback (which
        # clears coordinator._trigger_task) actually fire, since
        # Task.add_done_callback schedules callbacks via call_soon rather
        # than invoking them synchronously on completion.
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # let the second task's done-callback run
        assert coordinator._trigger_task is None  # noqa: SLF001
        assert call_count == 2  # noqa: PLR2004


# ---------------------------------------------------------------------------
#   Insufficient-role Repair issue (#311)
# ---------------------------------------------------------------------------


async def test_async_update_data_creates_repair_issue_for_insufficient_role(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """A User-only installation gets a Repair issue created after a poll."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.qual_id = "Installation[inst1]"
    installation.get.side_effect = lambda key, default=None: {
        "current_user_roles": "User",
        "name": "Home",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_create_issue.assert_called_once_with(
        hass,
        DOMAIN,
        "insufficient_role_inst1",
        is_fixable=False,
        severity=mock_ir.IssueSeverity.WARNING,
        translation_key="insufficient_role",
        translation_placeholders={"installation_name": "Home", "role": "User"},
        learn_more_url="https://portal.zaptec.com/",
    )
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_never_deletes_issue_while_role_stays_insufficient(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Repeated polls with an unchanged User-only role never call async_delete_issue.

    This is a regression guard for the "don't nag aware users" requirement:
    HA's issue registry preserves a user's "Ignore" dismissal across repeat
    async_create_issue() calls for the same issue_id, but a delete+recreate
    cycle would reset it. As long as the role doesn't change, this code must
    never delete the issue between polls.
    """
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.side_effect = lambda key, default=None: {
        "current_user_roles": "User",
        "name": "Home",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001
        await coordinator._async_update_data()  # noqa: SLF001
        await coordinator._async_update_data()  # noqa: SLF001

    assert mock_ir.async_create_issue.call_count == 3  # noqa: PLR2004
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_clears_repair_issue_for_owner_role(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """An Owner-role installation deletes any existing Repair issue."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.side_effect = lambda key, default=None: {
        "current_user_roles": "Owner",
    }.get(key, default)
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_delete_issue.assert_called_once_with(hass, DOMAIN, "insufficient_role_inst1")
    mock_ir.async_create_issue.assert_not_called()


async def test_async_update_data_skips_role_check_when_role_unknown(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """No CurrentUserRoles observed yet -> neither create nor delete an issue."""
    manager.zaptec.poll = AsyncMock()
    installation = MagicMock(spec=Installation)
    installation.id = "inst1"
    installation.get.return_value = None
    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch("custom_components.zaptec.coordinator.ir") as mock_ir:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_ir.async_create_issue.assert_not_called()
    mock_ir.async_delete_issue.assert_not_called()


async def test_async_update_data_skips_role_check_for_non_installation(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    """Charger/account-wide coordinators never run the installation role check."""
    manager.zaptec.poll = AsyncMock()
    charger = MagicMock(spec=Charger)
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(
        hass, entry=config_entry, manager=manager, options=options
    )

    with patch.object(coordinator, "_check_installation_role") as mock_check:
        await coordinator._async_update_data()  # noqa: SLF001

    mock_check.assert_not_called()
