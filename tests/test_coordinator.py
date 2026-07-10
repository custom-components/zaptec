"""Tests for coordinator.py."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import Charger, Installation, Zaptec


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
