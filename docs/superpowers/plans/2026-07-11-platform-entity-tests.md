# Platform Entity Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `custom_components/zaptec/sensor.py`, `switch.py`, `number.py`, `button.py`, `binary_sensor.py`, and `update.py` from 0% coverage to near-full coverage with real unit tests, runnable locally on native Windows in this dev environment.

**Architecture:** Every entity class in these six files subclasses `ZaptecBaseEntity` (`entity.py`), which is constructed as `ZaptecBaseEntity(coordinator, zaptec_object, description, device_info)` — see `custom_components/zaptec/entity.py:42-65`. This plan reuses that construction pattern and the `hass`/`config_entry` fixtures added to `tests/conftest.py` by PR #394 (`test/coordinator-entity-coverage`), plus the same `coordinator` fixture shape used in that PR's `tests/test_entity.py`. Unlike PR #394 (which used a hand-written `FakeZaptecObj`), these tests build `zaptec_obj` as `MagicMock(spec=Charger)` / `MagicMock(spec=Installation)`, because the entity classes under test call real API-shaped methods (`is_command_valid`, `command`, `set_permanent_cable_lock`, `set_limit_current`, `set_three_to_one_phase_switch_current`, `set_settings`, `set_hmi_brightness`, `asdict`) that `spec=` validates exist on the real class — a typo'd method name fails loudly instead of silently returning a fresh `MagicMock`. `.get()` is wired via `side_effect` to a plain dict's `.get`, so `ZaptecBaseEntity._get_zaptec_value()` still resolves real values through the mock. This was confirmed against the real source: `ZaptecBase` (`custom_components/zaptec/zaptec/api.py:73`) subclasses `collections.abc.Mapping`, so `.get` is a concrete inherited method present in `dir(Charger)`/`dir(Installation)`, and `DataUpdateCoordinator.last_update_success` defaults to `True` (confirmed via `inspect.getsource` against the installed `homeassistant` package), so entities built with this plan's `coordinator` fixture report `available` truthfully without extra setup.

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode = "auto"`, already configured), `unittest.mock`. No new dependencies.

## Global Constraints

- **Branch setup (do once, before Task 1):** this plan stacks on top of PR #394. Create and check out a new branch from it:
  ```bash
  git checkout -b test/platform-entity-coverage test/coordinator-entity-coverage
  ```
  This gives you `tests/conftest.py`'s `hass`/`config_entry` fixtures and PR #394's `tests/test_coordinator.py`/`tests/test_entity.py` for free. Do not modify those two files as part of this plan.
- Do not modify `custom_components/zaptec/zaptec/api.py` or its tests — out of scope for this pass.
- Do not modify the entity-description list constants (`INSTALLATION_ENTITIES`/`CHARGER_ENTITIES`) or `async_setup_entry()` in any of the six platform files — declarative/thin-delegation code, out of scope (same call PR #394 made for `entity.py`'s equivalent thin wrappers).
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Lint gate: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff` and `... -m ruff check custom_components tests` must stay clean for the files this plan touches (new test files must pass `ruff check` outright, not just under `--exit-zero`). **Correction (found during Task 1's review):** the repo root has a `.ruff.toml` (not just `pyproject.toml`) with `select = ["ALL"]` and a large ignore list. `E501` (line-length) is in that ignore list, so don't hand-wrap long docstrings — that part holds. But `SLF001` (private-member-access) and `PLR2004` (magic-value comparisons) are **not** ignored, so private-attribute test assertions (`entity._attr_native_value`, `entity._update_from_zaptec()`, etc.) need `# noqa: SLF001` / `# noqa: PLR2004` comments, matching the precedent already in `tests/test_coordinator.py`/`tests/test_entity.py`. Run `ruff check` after writing each file and add `# noqa` comments to whatever it flags.
- **Do not add `pytest-homeassistant-custom-component` or any other new test dependency.** See PR #394's plan (`docs/superpowers/plans/2026-07-11-coordinator-entity-tests.md`) for why it was evaluated and reverted (native Windows incompatibility).
- Never commit without explicit user approval (per repo CLAUDE.md) — stop before each commit step and wait for approval, or if running unattended per user's chosen execution mode, treat "commit" steps as the point to pause for review.

---

## File Structure

- **Create: `tests/test_sensor.py`** — unit tests for `ZaptecSensor`, `ZaptecSensorTranslate`, `ZaptecChargeSensor`, `ZaptecEnengySensor`.
- **Create: `tests/test_switch.py`** — unit tests for `ZaptecSwitch`, `ZaptecChargeSwitch`, `ZaptecCableLockSwitch`.
- **Create: `tests/test_number.py`** — unit tests for `ZaptecNumber`, `ZaptecAvailableCurrentNumber`, `ZaptecThreeToOnePhaseSwitchCurrent`, `ZaptecSettingNumber`, `ZaptecHmiBrightness`.
- **Create: `tests/test_button.py`** — unit tests for `ZaptecButton`.
- **Create: `tests/test_binary_sensor.py`** — unit tests for `ZaptecBinarySensor`, `ZaptecBinarySensorWithAttrs`.
- **Create: `tests/test_update.py`** — unit tests for `ZaptecUpdate`.

Each test file defines its own small `coordinator` fixture (consuming the `hass`/`config_entry` fixtures from `tests/conftest.py`) and a `make_charger`/`make_installation` helper — this duplication is intentional and matches the precedent set by PR #394's `test_coordinator.py`/`test_entity.py` (each test file is self-contained and readable on its own).

---

### Task 1: `sensor.py` tests

**Files:**
- Create: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`, added by PR #394).
- Produces: nothing consumed by later tasks (each test file is independent).

- [ ] **Step 1: Write the test file**

Create `tests/test_sensor.py`:

```python
"""Tests for sensor.py."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.sensor import (
    ZapSensorEntityDescription,
    ZaptecChargeSensor,
    ZaptecEnengySensor,
    ZaptecSensor,
    ZaptecSensorTranslate,
)
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_sensor_update_from_zaptec_sets_value(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecSensor._update_from_zaptec reads the raw value for its key."""
    charger = make_charger({"total_charge_power": 1500.0})
    description = ZapSensorEntityDescription(key="total_charge_power", cls=ZaptecSensor)
    entity = ZaptecSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == 1500.0
    assert entity._attr_available is True


def test_sensor_translate_post_init_lower_cases_options(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSensorTranslate._post_init lower-cases entity_description.options."""
    charger = make_charger({"device_type": "PRO"})
    description = ZapSensorEntityDescription(
        key="device_type", options=["Pro", "GO"], cls=ZaptecSensorTranslate
    )
    entity = ZaptecSensorTranslate(coordinator, charger, description, DeviceInfo())

    assert entity.entity_description.options == ["pro", "go"]


def test_sensor_translate_update_from_zaptec_lower_cases_value(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSensorTranslate._update_from_zaptec lower-cases the retrieved value."""
    charger = make_charger({"device_type": "PRO"})
    description = ZapSensorEntityDescription(
        key="device_type", options=["pro"], cls=ZaptecSensorTranslate
    )
    entity = ZaptecSensorTranslate(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == "pro"
    assert entity._attr_available is True


def test_charge_sensor_maps_known_mode_to_icon(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecChargeSensor picks the icon matching a known charger_operation_mode."""
    charger = make_charger({"charger_operation_mode": "Connected_Charging"})
    description = ZapSensorEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSensor)
    entity = ZaptecChargeSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == "connected_charging"
    assert entity._attr_icon == "mdi:lightning-bolt"


def test_charge_sensor_falls_back_to_unknown_icon(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecChargeSensor falls back to the 'unknown' icon for an unmapped mode."""
    charger = make_charger({"charger_operation_mode": "Something_Weird"})
    description = ZapSensorEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSensor)
    entity = ZaptecChargeSensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_icon == "mdi:help-rhombus-outline"


def test_energy_sensor_uses_meter_value_when_no_session(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecEnengySensor falls back to the meter reading when no session is present."""
    charger = make_charger({"signed_meter_value": {"RD": [{"RV": 12.5}]}})
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == 12.5


def test_energy_sensor_uses_session_value_when_larger(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecEnengySensor uses the session reading when it exceeds the meter reading."""
    charger = make_charger(
        {
            "signed_meter_value": {"RD": [{"RV": 10.0}]},
            "completed_session": {"SignedSession": {"RD": [{"RV": 20.0}]}},
        }
    )
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == 20.0


def test_energy_sensor_ignores_non_dict_session(
    coordinator: ZaptecUpdateCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    """ZaptecEnengySensor logs and defaults the session reading to 0.0 when it isn't a dict."""
    charger = make_charger(
        {
            "signed_meter_value": {"RD": [{"RV": 10.0}]},
            "completed_session": "not-a-dict",
        }
    )
    description = ZapSensorEntityDescription(key="signed_meter_value_kwh", cls=ZaptecEnengySensor)
    entity = ZaptecEnengySensor(coordinator, charger, description, DeviceInfo())

    with caplog.at_level(logging.DEBUG):
        entity._update_from_zaptec()

    assert entity._attr_native_value == 10.0
    assert "Incorrect typing for completed_session" in caplog.text
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_sensor.py -v
```
Expected: 8 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_sensor.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_sensor.py
```
If `ruff format --diff` shows a diff, apply it with `ruff format tests/test_sensor.py` (no `--diff`) and re-run both checks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sensor.py
git commit -m "test: add sensor.py entity tests"
```

---

### Task 2: `switch.py` tests

**Files:**
- Create: `tests/test_switch.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`).

- [ ] **Step 1: Write the test file**

Create `tests/test_switch.py`:

```python
"""Tests for switch.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.switch import (
    ZapSwitchEntityDescription,
    ZaptecCableLockSwitch,
    ZaptecChargeSwitch,
    ZaptecSwitch,
)
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_switch_update_from_zaptec_sets_is_on(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecSwitch._update_from_zaptec reads the raw boolean value for its key."""
    charger = make_charger({"permanent_cable_lock": True})
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecSwitch)
    entity = ZaptecSwitch(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_is_on is True
    assert entity._attr_available is True


def test_charge_switch_update_from_zaptec_true_only_when_charging(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecChargeSwitch is only "on" when the mode is exactly Connected_Charging."""
    charger = make_charger({"charger_operation_mode": "Connected_Charging"})
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_is_on is True


def test_charge_switch_available_checks_stop_command_when_on(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """When on, ZaptecChargeSwitch.available checks the stop_charging_final command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = True
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity._attr_is_on = True

    assert entity.available is True
    charger.is_command_valid.assert_called_once_with("stop_charging_final")


def test_charge_switch_available_checks_resume_command_when_off(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """When off, ZaptecChargeSwitch.available checks the resume_charging command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = False
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity._attr_is_on = False

    assert entity.available is False
    charger.is_command_valid.assert_called_once_with("resume_charging")


async def test_charge_switch_turn_on_resumes_charging_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on sends resume_charging and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_on()

    charger.command.assert_awaited_once_with("resume_charging")
    entity.trigger_poll.assert_awaited_once()


async def test_charge_switch_turn_on_wraps_command_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    entity.trigger_poll.assert_not_called()


async def test_charge_switch_turn_off_stops_charging_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off sends stop_charging_final and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_off()

    charger.command.assert_awaited_once_with("stop_charging_final")
    entity.trigger_poll.assert_awaited_once()


async def test_charge_switch_turn_off_wraps_command_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="charger_operation_mode", cls=ZaptecChargeSwitch)
    entity = ZaptecChargeSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()

    entity.trigger_poll.assert_not_called()


async def test_cable_lock_switch_turn_on_locks_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on locks the cable and triggers a poll on success."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock()
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecCableLockSwitch)
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_on()

    charger.set_permanent_cable_lock.assert_awaited_once_with(True)
    entity.trigger_poll.assert_awaited_once()


async def test_cable_lock_switch_turn_on_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_on wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecCableLockSwitch)
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    entity.trigger_poll.assert_not_called()


async def test_cable_lock_switch_turn_off_unlocks_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off unlocks the cable and triggers a poll on success."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock()
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecCableLockSwitch)
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_turn_off()

    charger.set_permanent_cable_lock.assert_awaited_once_with(False)
    entity.trigger_poll.assert_awaited_once()


async def test_cable_lock_switch_turn_off_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_turn_off wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_permanent_cable_lock = AsyncMock(side_effect=Exception("boom"))
    description = ZapSwitchEntityDescription(key="permanent_cable_lock", cls=ZaptecCableLockSwitch)
    entity = ZaptecCableLockSwitch(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_off()

    entity.trigger_poll.assert_not_called()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_switch.py -v
```
Expected: 12 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_switch.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_switch.py
```
If `ruff format --diff` shows a diff, apply it and re-run both checks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_switch.py
git commit -m "test: add switch.py entity tests"
```

---

### Task 3: `number.py` tests

**Files:**
- Create: `tests/test_number.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`).

- [ ] **Step 1: Write the test file**

Create `tests/test_number.py`:

```python
"""Tests for number.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.number import (
    ZapNumberEntityDescription,
    ZaptecAvailableCurrentNumber,
    ZaptecHmiBrightness,
    ZaptecNumber,
    ZaptecSettingNumber,
    ZaptecThreeToOnePhaseSwitchCurrent,
)
from custom_components.zaptec.zaptec import Charger, Installation


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def make_installation(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Installation) whose .get() reads from data."""
    installation = MagicMock(spec=Installation)
    installation.id = "install1"
    installation.qual_id = "Installation[install1]"
    installation.get.side_effect = data.get
    return installation


def test_number_update_from_zaptec_sets_value(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecNumber._update_from_zaptec reads the raw value for its key."""
    installation = make_installation({"available_current": 16.0})
    description = ZapNumberEntityDescription(key="available_current", cls=ZaptecNumber)
    entity = ZaptecNumber(coordinator, installation, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == 16.0
    assert entity._attr_available is True


def test_available_current_post_init_uses_reported_max_current(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAvailableCurrentNumber._post_init sets native_max_value from MaxCurrent."""
    installation = make_installation({"MaxCurrent": 20})
    description = ZapNumberEntityDescription(
        key="available_current", native_max_value=0, cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 20


def test_available_current_post_init_defaults_to_32(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAvailableCurrentNumber._post_init defaults to 32A when MaxCurrent is absent."""
    installation = make_installation({})
    description = ZapNumberEntityDescription(
        key="available_current", native_max_value=0, cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 32


async def test_available_current_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value sets the current limit and triggers a poll on success."""
    installation = make_installation({})
    installation.set_limit_current = AsyncMock()
    description = ZapNumberEntityDescription(
        key="available_current", cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(10.0)

    installation.set_limit_current.assert_awaited_once_with(availableCurrent=10.0)
    entity.trigger_poll.assert_awaited_once()


async def test_available_current_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    installation = make_installation({})
    installation.set_limit_current = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="available_current", cls=ZaptecAvailableCurrentNumber
    )
    entity = ZaptecAvailableCurrentNumber(coordinator, installation, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(10.0)

    entity.trigger_poll.assert_not_called()


async def test_three_to_one_phase_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value sets the switch current and triggers a poll on success."""
    installation = make_installation({})
    installation.set_three_to_one_phase_switch_current = AsyncMock()
    description = ZapNumberEntityDescription(
        key="three_to_one_phase_switch_current", cls=ZaptecThreeToOnePhaseSwitchCurrent
    )
    entity = ZaptecThreeToOnePhaseSwitchCurrent(
        coordinator, installation, description, DeviceInfo()
    )
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(8.0)

    installation.set_three_to_one_phase_switch_current.assert_awaited_once_with(8.0)
    entity.trigger_poll.assert_awaited_once()


async def test_three_to_one_phase_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    installation = make_installation({})
    installation.set_three_to_one_phase_switch_current = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="three_to_one_phase_switch_current", cls=ZaptecThreeToOnePhaseSwitchCurrent
    )
    entity = ZaptecThreeToOnePhaseSwitchCurrent(
        coordinator, installation, description, DeviceInfo()
    )
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(8.0)

    entity.trigger_poll.assert_not_called()


def test_setting_number_post_init_uses_reported_max_limit(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecSettingNumber._post_init sets native_max_value from ChargeCurrentInstallationMaxLimit."""
    charger = make_charger({"ChargeCurrentInstallationMaxLimit": 25})
    description = ZapNumberEntityDescription(
        key="charger_max_current",
        native_max_value=0,
        setting="maxChargeCurrent",
        cls=ZaptecSettingNumber,
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())

    assert entity.entity_description.native_max_value == 25


async def test_setting_number_missing_setting_raises_without_calling_api(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value raises HomeAssistantError when no setting is configured."""
    charger = make_charger({})
    charger.set_settings = AsyncMock()
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting=None, cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(16.0)

    charger.set_settings.assert_not_called()
    entity.trigger_poll.assert_not_called()


async def test_setting_number_set_native_value_success(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value writes the configured setting and triggers a poll on success."""
    charger = make_charger({})
    charger.set_settings = AsyncMock()
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting="maxChargeCurrent", cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(16.0)

    charger.set_settings.assert_awaited_once_with({"maxChargeCurrent": 16.0})
    entity.trigger_poll.assert_awaited_once()


async def test_setting_number_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_settings = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(
        key="charger_max_current", setting="maxChargeCurrent", cls=ZaptecSettingNumber
    )
    entity = ZaptecSettingNumber(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(16.0)

    entity.trigger_poll.assert_not_called()


def test_hmi_brightness_update_from_zaptec_scales_up(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecHmiBrightness._update_from_zaptec scales the 0-1 API value to a 0-100 percentage."""
    charger = make_charger({"hmi_brightness": 0.55})
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_native_value == pytest.approx(55.0)


async def test_hmi_brightness_set_native_value_scales_down(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value scales the 0-100 percentage back to 0-1 and triggers a poll."""
    charger = make_charger({})
    charger.set_hmi_brightness = AsyncMock()
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_set_native_value(50.0)

    charger.set_hmi_brightness.assert_awaited_once_with(0.5)
    entity.trigger_poll.assert_awaited_once()


async def test_hmi_brightness_set_native_value_wraps_failure(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_set_native_value wraps a failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.set_hmi_brightness = AsyncMock(side_effect=Exception("boom"))
    description = ZapNumberEntityDescription(key="hmi_brightness", cls=ZaptecHmiBrightness)
    entity = ZaptecHmiBrightness(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(50.0)

    entity.trigger_poll.assert_not_called()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_number.py -v
```
Expected: 14 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_number.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_number.py
```
If `ruff format --diff` shows a diff, apply it and re-run both checks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_number.py
git commit -m "test: add number.py entity tests"
```

---

### Task 4: `button.py` tests

**Files:**
- Create: `tests/test_button.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`).

- [ ] **Step 1: Write the test file**

Create `tests/test_button.py`:

```python
"""Tests for button.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.button import ZapButtonEntityDescription, ZaptecButton
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_button_available_delegates_to_is_command_valid(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecButton.available checks is_command_valid using its own key as the command."""
    charger = make_charger({})
    charger.is_command_valid.return_value = True
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())

    assert entity.available is True
    charger.is_command_valid.assert_called_once_with("restart_charger")


def test_button_unavailable_when_command_invalid(coordinator: ZaptecUpdateCoordinator) -> None:
    """ZaptecButton.available is False when is_command_valid returns False."""
    charger = make_charger({})
    charger.is_command_valid.return_value = False
    description = ZapButtonEntityDescription(key="resume_charging", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())

    assert entity.available is False


async def test_button_press_sends_command_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_press sends the command named by the button's key and triggers a poll."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_press()

    charger.command.assert_awaited_once_with("restart_charger")
    entity.trigger_poll.assert_awaited_once()


async def test_button_press_wraps_command_failure(coordinator: ZaptecUpdateCoordinator) -> None:
    """async_press wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapButtonEntityDescription(key="restart_charger", cls=ZaptecButton)
    entity = ZaptecButton(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_press()

    entity.trigger_poll.assert_not_called()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_button.py -v
```
Expected: 4 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_button.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_button.py
```
If `ruff format --diff` shows a diff, apply it and re-run both checks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_button.py
git commit -m "test: add button.py entity tests"
```

---

### Task 5: `binary_sensor.py` tests

**Files:**
- Create: `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`).

- [ ] **Step 1: Write the test file**

Create `tests/test_binary_sensor.py`:

```python
"""Tests for binary_sensor.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.binary_sensor import (
    ZapBinarySensorEntityDescription,
    ZaptecBinarySensor,
    ZaptecBinarySensorWithAttrs,
)
from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_binary_sensor_update_from_zaptec_sets_is_on(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecBinarySensor._update_from_zaptec reads the raw boolean value for its key."""
    charger = make_charger({"is_online": True})
    description = ZapBinarySensorEntityDescription(key="is_online", cls=ZaptecBinarySensor)
    entity = ZaptecBinarySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_is_on is True
    assert entity._attr_available is True


def test_binary_sensor_with_attrs_post_init_sets_attrs_and_unique_id(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecBinarySensorWithAttrs._post_init copies all raw attrs and overrides unique_id."""
    charger = make_charger({})
    charger.asdict.return_value = {"Id": "charger1", "Active": True}
    description = ZapBinarySensorEntityDescription(key="active", cls=ZaptecBinarySensorWithAttrs)
    entity = ZaptecBinarySensorWithAttrs(coordinator, charger, description, DeviceInfo())

    assert entity._attr_extra_state_attributes == {"Id": "charger1", "Active": True}
    assert entity._attr_unique_id == "charger1"
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_binary_sensor.py -v
```
Expected: 2 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_binary_sensor.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_binary_sensor.py
```
If `ruff format --diff` shows a diff, apply it and re-run both checks.

- [ ] **Step 4: Commit**

```bash
git add tests/test_binary_sensor.py
git commit -m "test: add binary_sensor.py entity tests"
```

---

### Task 6: `update.py` tests, full suite, and coverage check

**Files:**
- Create: `tests/test_update.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (from `tests/conftest.py`).

- [ ] **Step 1: Write the test file**

Create `tests/test_update.py`:

```python
"""Tests for update.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
import pytest

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.update import ZapUpdateEntityDescription, ZaptecUpdate
from custom_components.zaptec.zaptec import Charger


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
    """Create a ZaptecUpdateCoordinator for testing."""
    manager = MagicMock()
    options = ZaptecUpdateOptions(
        name="test",
        update_interval=600,
        charging_update_interval=None,
        tracked_devices=set(),
        poll_args={},
        zaptec_object=None,
    )
    return ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


def make_charger(data: dict[str, Any]) -> MagicMock:
    """Create a MagicMock(spec=Charger) whose .get() reads from data."""
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    charger.qual_id = "Charger[charger1]"
    charger.get.side_effect = data.get
    return charger


def test_update_from_zaptec_sets_installed_and_latest_version(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecUpdate._update_from_zaptec reads both firmware version keys."""
    charger = make_charger(
        {
            "firmware_current_version": "1.0.0",
            "firmware_available_version": "1.1.0",
        }
    )
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()

    assert entity._attr_installed_version == "1.0.0"
    assert entity._attr_latest_version == "1.1.0"
    assert entity._attr_available is True


async def test_async_install_sends_upgrade_firmware_and_polls(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """async_install sends the upgrade_firmware command and triggers a poll on success."""
    charger = make_charger({})
    charger.command = AsyncMock()
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    await entity.async_install(version=None, backup=False)

    charger.command.assert_awaited_once_with("upgrade_firmware")
    entity.trigger_poll.assert_awaited_once()


async def test_async_install_wraps_command_failure(coordinator: ZaptecUpdateCoordinator) -> None:
    """async_install wraps a command failure in HomeAssistantError and skips the poll."""
    charger = make_charger({})
    charger.command = AsyncMock(side_effect=Exception("boom"))
    description = ZapUpdateEntityDescription(key="firmware_update", cls=ZaptecUpdate)
    entity = ZaptecUpdate(coordinator, charger, description, DeviceInfo())
    entity.trigger_poll = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await entity.async_install(version=None, backup=False)

    entity.trigger_poll.assert_not_called()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_update.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Lint**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format tests/test_update.py --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check tests/test_update.py
```
If `ruff format --diff` shows a diff, apply it and re-run both checks.

- [ ] **Step 4: Full suite + coverage check**

```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch --cov-report=term-missing tests -q
```
Expected: `sensor.py`, `switch.py`, `number.py`, `button.py`, `binary_sensor.py`, `update.py` all at or near 100% (any remaining gaps should only be declarative code — `INSTALLATION_ENTITIES`/`CHARGER_ENTITIES` list literals and `async_setup_entry()` — explicitly out of scope for this plan). The pre-existing `test_zconst.py`/`test_redact.py` DNS-fixture errors are expected and unrelated (see `CLAUDE.md`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_update.py
git commit -m "test: add update.py entity tests"
```

---

## Self-Review

**Coverage of stated goal:** Task 1 covers every method with real logic in `sensor.py` (`ZaptecSensor._update_from_zaptec`, `ZaptecSensorTranslate._post_init` and lower-casing, `ZaptecChargeSensor`'s icon map both branches, `ZaptecEnengySensor`'s OCMF max-of-two-readings logic across all three data shapes). Task 2 covers `switch.py` (`ZaptecSwitch._update_from_zaptec`, `ZaptecChargeSwitch.available` both branches plus turn_on/off success+failure, `ZaptecCableLockSwitch` turn_on/off success+failure). Task 3 covers `number.py` (`ZaptecNumber._update_from_zaptec`, `ZaptecAvailableCurrentNumber._post_init` both branches plus set success+failure, `ZaptecThreeToOnePhaseSwitchCurrent` set success+failure, `ZaptecSettingNumber._post_init` plus the missing-setting guard plus set success+failure, `ZaptecHmiBrightness` scaling both directions plus set success+failure). Task 4 covers `button.py` (`ZaptecButton.available` both branches, `async_press` success+failure). Task 5 covers `binary_sensor.py` (`ZaptecBinarySensor._update_from_zaptec`, `ZaptecBinarySensorWithAttrs._post_init`). Task 6 covers `update.py` (`ZaptecUpdate._update_from_zaptec` both keys, `async_install` success+failure) and closes with a full-suite coverage check.

**Placeholder scan:** No TBD/TODO markers; every step has complete, real code and exact commands.

**Type consistency:** The `coordinator` fixture and `make_charger`/`make_installation` helpers are identical in shape across all six files (deliberately duplicated per the design's stated rationale — see File Structure section). `ZaptecUpdateOptions` field names match `coordinator.py` and PR #394's `test_coordinator.py`/`test_entity.py` exactly. Every entity class, description dataclass, and API method name (`is_command_valid`, `command`, `set_permanent_cable_lock`, `set_limit_current`, `set_three_to_one_phase_switch_current`, `set_settings`, `set_hmi_brightness`, `asdict`) was confirmed against the real source in `custom_components/zaptec/zaptec/api.py` and the six platform files before being used in a test.

**Verification note:** `DataUpdateCoordinator.last_update_success` defaulting to `True`, `ZaptecBase` being a `collections.abc.Mapping` subclass (so `.get` is a valid `spec=`'d attribute), and `get_ocmf_max_reader_value`'s own non-dict guard (`custom_components/zaptec/zaptec/utils.py:111-119`) were all confirmed by reading the installed `homeassistant` package source and the repo source directly before writing this plan, to avoid the kind of failed round-trip PR #394's plan explicitly called out avoiding.
