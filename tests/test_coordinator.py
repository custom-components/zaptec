"""Behavior tests for ZaptecUpdateCoordinator, driven through the real harness."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import (
    ZAPTEC_POLL_INTERVAL_CHARGING,
    ZAPTEC_POLL_INTERVAL_IDLE,
)
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import ZaptecApiError
from tests.conftest import setup_integration


async def test_successful_poll_marks_last_update_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A successful poll leaves every coordinator reporting success."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    for coordinator in manager.all_coordinators:
        assert coordinator.last_update_success is True
    mock_zaptec.poll.assert_awaited()


async def test_poll_failure_sets_update_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A ZaptecApiError during poll flips last_update_success to False."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    head = manager.head_coordinator

    mock_zaptec.poll.side_effect = ZaptecApiError("boom")
    await head.async_refresh()

    assert head.last_update_success is False


async def test_device_coordinator_switches_interval_when_charging(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A charger's coordinator uses the shorter interval once it reports charging."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    charger_coord = manager.device_coordinators["chg-mock-1"]
    idle_interval = charger_coord.update_interval
    assert idle_interval == timedelta(seconds=ZAPTEC_POLL_INTERVAL_IDLE)

    # Flip the seeded charger to 'charging' and re-run the update-listener path.
    mock_zaptec.chargers[0].is_charging.return_value = True
    charger_coord.set_update_interval()

    assert charger_coord.update_interval == timedelta(seconds=ZAPTEC_POLL_INTERVAL_CHARGING)
    # Also assert the relation directly: charging must poll faster than idle. This
    # catches a regression that equality checks alone would miss, e.g. const.py
    # setting ZAPTEC_POLL_INTERVAL_CHARGING >= ZAPTEC_POLL_INTERVAL_IDLE, which would
    # still pass both equality asserts above despite breaking the whole feature.
    assert charger_coord.update_interval < idle_interval


async def test_charging_update_interval_requires_charger_object(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Constructing a coordinator with a charging interval on a non-Charger object errors.

    Constructs `ZaptecUpdateCoordinator` directly (skipping `setup_integration`)
    to hit this constructor-time guard in isolation. A bare, unconfigured
    `manager=MagicMock()` is enough: `__init__` does `self.zaptec = manager.zaptec`
    (auto-vivifies on a MagicMock, no error) before the `isinstance(zaptec_object,
    Charger)` check runs, so nothing about the manager's real behavior is
    exercised before the ValueError fires.
    """
    mock_config_entry.add_to_hass(hass)

    with pytest.raises(ValueError, match="Charging update interval requires a Charger object"):
        ZaptecUpdateCoordinator(
            hass,
            entry=mock_config_entry,
            manager=MagicMock(),
            options=ZaptecUpdateOptions(
                name="bad",
                update_interval=60,
                charging_update_interval=30,
                tracked_devices=set(),
                poll_args={},
                zaptec_object=object(),  # not a Charger instance
            ),
        )


async def test_trigger_poll_is_noop_without_zaptec_object(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """trigger_poll() on a coordinator with no bound zaptec object does nothing.

    `manager.head_coordinator` specifically: it's the one coordinator built with
    `zaptec_object=None` (the account-wide coordinator; __init__.py), unlike every
    device coordinator, which always gets a real Charger/Installation. That's what
    satisfies trigger_poll()'s `if zaptec_obj is None: return` no-op guard.
    """
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    await manager.head_coordinator.trigger_poll()

    assert manager.head_coordinator._trigger_task is None  # noqa: SLF001


async def test_trigger_poll_cancels_in_flight_task_and_reschedules(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second trigger_poll() call cancels the running poll sequence and starts a new one."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    charger_coord = manager.device_coordinators["chg-mock-1"]

    # Collapse the real multi-second delays to zero so the poll sequence runs fast, while
    # still going through real asyncio.sleep(0) checkpoints (needed so the eagerly-started
    # background task actually suspends and can be observed/cancelled mid-flight).
    monkeypatch.setattr(
        "custom_components.zaptec.coordinator.ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS", [0, 0, 0]
    )

    # HA's eager task factory starts the background task running immediately; it
    # suspends at the first real `asyncio.sleep(0)` checkpoint and is left pending.
    await charger_coord.trigger_poll()
    first_task = charger_coord._trigger_task  # noqa: SLF001
    assert first_task is not None

    # Second call sees the still-pending first task and cancels it before rescheduling.
    await charger_coord.trigger_poll()
    assert first_task.cancelled()

    second_task = charger_coord._trigger_task  # noqa: SLF001
    assert second_task is not None
    assert second_task is not first_task
    await second_task
    await hass.async_block_till_done()

    assert charger_coord._trigger_task is None  # noqa: SLF001
    assert charger_coord.last_update_success is True


async def test_trigger_poll_triggers_child_charger_coordinators(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling an installation also triggers the poll sequence of its tracked chargers.

    Patches `asyncio.sleep` globally rather than a delays-list constant (as the
    cancel/reschedule test does) because installations use a different constant
    (ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS) whose values this test doesn't care
    about — it only needs to reach the loop's first iteration fast, since
    children are triggered at `i == 1` (coordinator.py). `charger_coord.trigger_poll`
    is replaced with a mock rather than left real, to isolate "did the parent call
    the child" from "does the child's own trigger_poll work" (covered elsewhere).
    """
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    install_coord = manager.device_coordinators["inst-mock-1"]
    charger_coord = manager.device_coordinators["chg-mock-1"]

    monkeypatch.setattr(
        "custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock(return_value=None)
    )
    charger_coord.trigger_poll = AsyncMock()

    await install_coord.trigger_poll()
    task = install_coord._trigger_task  # noqa: SLF001
    assert task is not None
    await task
    await hass.async_block_till_done()

    charger_coord.trigger_poll.assert_awaited_once()
