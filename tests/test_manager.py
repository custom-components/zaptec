"""Tests for custom_components.zaptec.manager."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.zaptec.manager import ZaptecManager

CHARGER_ID = "e6f5a1b2-0000-4000-8000-000000000001"


class FakeCoordinator:
    """Records what the stream callback asks of a device coordinator."""

    def __init__(self) -> None:
        """Initialize the counters."""
        self.listener_updates = 0
        self.polls = 0

    def async_update_listeners(self) -> None:
        """Count a listener update."""
        self.listener_updates += 1

    async def trigger_poll(self) -> None:
        """Count a triggered poll."""
        self.polls += 1


def stream_event(state_id: int, value: str) -> dict[str, Any]:
    """Build a stream event as Zaptec sends it for a charger observation."""
    return {
        "ChargerId": CHARGER_ID,
        "StateId": state_id,
        "Timestamp": "2026-09-09T21:35:34.625569Z",
        "ValueAsString": value,
    }


@pytest.fixture
def coordinator() -> FakeCoordinator:
    """Return the coordinator standing in for the tracked charger."""
    return FakeCoordinator()


@pytest.fixture
def manager(coordinator: FakeCoordinator) -> ZaptecManager:
    """Return a manager with one charger coordinator registered."""
    manager = ZaptecManager(
        None,  # type: ignore[arg-type]
        entry=None,  # type: ignore[arg-type]
        zaptec=None,  # type: ignore[arg-type]
        tracked_devices={CHARGER_ID},
    )
    manager.device_coordinators[CHARGER_ID] = coordinator  # type: ignore[assignment]
    return manager


@pytest.mark.asyncio
async def test_session_identifier_event_triggers_poll(
    manager: ZaptecManager, coordinator: FakeCoordinator
) -> None:
    """A session start polls for the observations Zaptec never streams."""
    await manager.stream_callback(stream_event(721, "ad55f97c-9c53-4de5-8003-f323ebd02080"))

    assert coordinator.polls == 1


@pytest.mark.asyncio
async def test_charger_operation_mode_event_triggers_poll(
    manager: ZaptecManager, coordinator: FakeCoordinator
) -> None:
    """An operation mode change polls for the CompletedSession written with it."""
    await manager.stream_callback(stream_event(710, "1"))

    assert coordinator.polls == 1


@pytest.mark.asyncio
async def test_unrelated_observation_does_not_trigger_poll(
    manager: ZaptecManager, coordinator: FakeCoordinator
) -> None:
    """Observations that carry their own value need no poll to be up to date."""
    await manager.stream_callback(stream_event(201, "29.088"))

    assert coordinator.polls == 0


@pytest.mark.asyncio
async def test_event_without_state_id_does_not_trigger_poll(
    manager: ZaptecManager, coordinator: FakeCoordinator
) -> None:
    """An event that carries no observation id cannot mark a session boundary."""
    await manager.stream_callback({"ChargerId": CHARGER_ID})

    assert coordinator.polls == 0


@pytest.mark.parametrize("state_id", [201, 721])
@pytest.mark.asyncio
async def test_stream_event_updates_listeners(
    manager: ZaptecManager, coordinator: FakeCoordinator, state_id: int
) -> None:
    """Every event refreshes the entities, whether or not it also polls."""
    await manager.stream_callback(stream_event(state_id, "1"))

    assert coordinator.listener_updates == 1


@pytest.mark.asyncio
async def test_event_for_untracked_charger_is_ignored(
    manager: ZaptecManager, coordinator: FakeCoordinator
) -> None:
    """A charger without a coordinator has nothing to update or poll."""
    event = stream_event(721, "ad55f97c-9c53-4de5-8003-f323ebd02080")
    event["ChargerId"] = "e6f5a1b2-0000-4000-8000-000000000002"

    await manager.stream_callback(event)

    assert coordinator.listener_updates == 0
    assert coordinator.polls == 0
