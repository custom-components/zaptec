"""Tests for number.py."""

import pytest

from custom_components.zaptec.manager import ZaptecEntityDescription
from custom_components.zaptec.number import (
    CHARGER_ENTITIES,
    INSTALLATION_ENTITIES,
    MIN_CHARGE_CURRENT,
    ZapNumberEntityDescription,
)


def _find(descriptions: list[ZaptecEntityDescription], key: str) -> ZapNumberEntityDescription:
    """Return the entity description with the given key."""
    return next(d for d in descriptions if d.key == key)


@pytest.mark.parametrize(
    ("descriptions", "key", "expected_min"),
    [
        pytest.param(
            CHARGER_ENTITIES, "charger_min_current", MIN_CHARGE_CURRENT, id="charger_min_current"
        ),
        # 0 holds the charger off rather than requesting a charging current.
        pytest.param(INSTALLATION_ENTITIES, "available_current", 0, id="available_current"),
        # Zaptec documents 0 as the way to force 3-phase charging.
        pytest.param(
            INSTALLATION_ENTITIES,
            "three_to_one_phase_switch_current",
            0,
            id="phase_switch_current",
        ),
        # Constrained relationally against charger_min_current, not by a static floor.
        pytest.param(CHARGER_ENTITIES, "charger_max_current", 0, id="charger_max_current"),
    ],
)
def test_native_min_value(
    descriptions: list[ZaptecEntityDescription], key: str, expected_min: float
) -> None:
    """Only charger_min_current declares the 6A floor."""
    assert _find(descriptions, key).native_min_value == expected_min
