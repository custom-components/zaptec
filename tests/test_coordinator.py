"""Behavior tests for ZaptecUpdateCoordinator, driven through the real harness."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import (
    DOMAIN,
    ZAPTEC_POLL_INTERVAL_CHARGING,
    ZAPTEC_POLL_INTERVAL_IDLE,
)
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import ZaptecApiError
from tests.conftest import CHARGER_DATA, INSTALLATION_DATA, reseed, setup_integration


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
    # Also assert the relation directly, catching e.g. const.py setting
    # CHARGING >= IDLE, which the equality asserts alone would miss.
    assert charger_coord.update_interval < idle_interval


async def test_charging_update_interval_requires_charger_object(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Constructing a coordinator with a charging interval on a non-Charger object errors.

    Skips `setup_integration` to hit the constructor-time guard directly: a
    bare `manager=MagicMock()` auto-vivifies `self.zaptec` with no error before
    the `isinstance(zaptec_object, Charger)` check runs.
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

    `head_coordinator` is the one coordinator built with `zaptec_object=None`
    (device coordinators always get a real Charger/Installation), satisfying
    trigger_poll()'s no-op guard.
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

    # Collapse the real delays to zero, keeping real asyncio.sleep(0) checkpoints so
    # the eagerly-started background task actually suspends and can be cancelled mid-flight.
    monkeypatch.setattr(
        "custom_components.zaptec.coordinator.ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS", [0, 0, 0]
    )

    # HA's eager task factory runs the task immediately; it suspends at the
    # first sleep(0) checkpoint and is left pending.
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

    Patches `asyncio.sleep` globally, rather than the delays-list constant (as the
    cancel/reschedule test does), just to reach the loop's first iteration fast —
    installations use their own delay constant this test doesn't care about.
    `charger_coord.trigger_poll` is mocked to isolate "parent calls child" from
    the child's own trigger_poll logic (covered elsewhere).
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


# ---------------------------------------------------------------------------
#   Insufficient-role Repair issue (#311)
# ---------------------------------------------------------------------------

ISSUE_ID = f"insufficient_role_{INSTALLATION_DATA['id']}"


async def test_insufficient_role_creates_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A User-only installation gets a Repair issue after the first poll."""
    reseed(mock_zaptec.installations[0], INSTALLATION_DATA | {"current_user_roles": "User"})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "insufficient_role"
    assert issue.translation_placeholders == {"installation_name": "Mock Home", "role": "User"}
    # A repair flow this integration never registers would break the Repairs dialog.
    assert issue.is_fixable is False
    assert issue.learn_more_url == "https://portal.zaptec.com/"


async def test_repeated_polls_preserve_an_ignored_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """Polling again with an unchanged role keeps the user's "Ignore" dismissal.

    Regression guard for the "don't nag aware users" requirement: a
    delete-then-recreate cycle would reset dismissed_version.
    """
    installation = mock_zaptec.installations[0]
    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    coordinator = manager.device_coordinators[INSTALLATION_DATA["id"]]

    ir.async_ignore_issue(hass, DOMAIN, ISSUE_ID, ignore=True)
    dismissed_version = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID).dismissed_version
    assert dismissed_version is not None

    await coordinator.async_refresh()
    # Vary the role within "still insufficient": the updated placeholder proves the
    # check ran again, so the preserved dismissal can't pass by the issue being untouched.
    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "User, Guest"})
    await coordinator.async_refresh()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders["role"] == "User, Guest"
    assert issue.dismissed_version == dismissed_version


async def test_sufficient_role_clears_the_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """Regaining the Owner role removes an existing Repair issue on the next poll."""
    installation = mock_zaptec.installations[0]
    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "Owner"})
    await manager.device_coordinators[INSTALLATION_DATA["id"]].async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_unknown_role_creates_no_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """No CurrentUserRoles observed yet -> no issue either way."""
    reseed(mock_zaptec.installations[0], INSTALLATION_DATA)

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_unknown_role_leaves_an_existing_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A poll that no longer observes CurrentUserRoles must not clear the issue."""
    installation = mock_zaptec.installations[0]
    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    reseed(installation, INSTALLATION_DATA)
    await manager.device_coordinators[INSTALLATION_DATA["id"]].async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_charger_coordinator_skips_the_role_check(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The role check is Installation-scoped: a charger poll never raises an issue."""
    reseed(mock_zaptec.chargers[0], CHARGER_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    await manager.device_coordinators[CHARGER_DATA["id"]].async_refresh()

    charger_issue_id = f"insufficient_role_{CHARGER_DATA['id']}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, charger_issue_id) is None


# ---------------------------------------------------------------------------
#   Restricted-charger Repair issue
# ---------------------------------------------------------------------------

CHARGER_ISSUE_ID = f"insufficient_charger_role_{INSTALLATION_DATA['id']}"


async def test_restricted_charger_creates_a_charger_repair_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """An Owner installation holding a User-only charger warns about that charger."""
    reseed(mock_zaptec.installations[0], INSTALLATION_DATA | {"current_user_roles": "Owner"})
    reseed(mock_zaptec.chargers[0], CHARGER_DATA | {"current_user_roles": "User"})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "insufficient_charger_role"
    assert issue.translation_placeholders == {
        "installation_name": "Mock Home",
        "chargers": "Mock Charger",
    }
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_insufficient_installation_role_supersedes_the_charger_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The two issues are mutually exclusive; the installation one wins."""
    installation = mock_zaptec.installations[0]
    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "Owner"})
    reseed(mock_zaptec.chargers[0], CHARGER_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    assert ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID) is not None

    reseed(installation, INSTALLATION_DATA | {"current_user_roles": "User"})
    await manager.device_coordinators[INSTALLATION_DATA["id"]].async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID) is None


async def test_charger_issue_clears_when_the_charger_role_is_granted(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """Granting Service on the charger removes the charger issue on the next poll."""
    charger = mock_zaptec.chargers[0]
    reseed(mock_zaptec.installations[0], INSTALLATION_DATA | {"current_user_roles": "Owner"})
    reseed(charger, CHARGER_DATA | {"current_user_roles": "User"})
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    assert ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID) is not None

    reseed(charger, CHARGER_DATA | {"current_user_roles": "Maintainer"})
    await manager.device_coordinators[INSTALLATION_DATA["id"]].async_refresh()

    assert ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID) is None


async def test_charger_with_unobserved_role_is_not_reported(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A charger that hasn't been polled yet is left out, not assumed restricted."""
    reseed(mock_zaptec.installations[0], INSTALLATION_DATA | {"current_user_roles": "Owner"})
    reseed(mock_zaptec.chargers[0], CHARGER_DATA)

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert ir.async_get(hass).async_get_issue(DOMAIN, CHARGER_ISSUE_ID) is None
