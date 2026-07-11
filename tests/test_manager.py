"""Tests for manager.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from custom_components.zaptec.manager import ZaptecManager
from custom_components.zaptec.zaptec import Zaptec


def test_manager_init_creates_empty_statistics_coordinators(
    hass: MagicMock, config_entry: Any
) -> None:
    """ZaptecManager starts with an empty statistics_coordinators dict."""
    manager = ZaptecManager(hass, entry=config_entry, zaptec=MagicMock(spec=Zaptec))
    assert manager.statistics_coordinators == {}
