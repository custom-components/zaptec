# Issue #272 Stale Device Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a charger is deselected via the Zaptec integration's reconfigure flow, its stale device (and entities) should be removed from the HA registries instead of lingering forever.

**Architecture:** `async_setup_entry` (`custom_components/zaptec/__init__.py`) already walks every device entry belonging to the config entry and removes ones that are empty or match a deprecated "Circuit" device id. That inline block is extracted into a standalone, directly-testable function `_cleanup_stale_devices(hass, entry, tracked_devices, circuit_ids)`, and a third removal branch is added: a device whose Zaptec identifier is not in `tracked_devices` (the set computed by `ZaptecManager.first_time_setup` from the user's charger selection) is stale and gets removed the same way the Circuit-device case already is.

**Tech Stack:** Python, Home Assistant `device_registry`/`entity_registry` helpers, pytest + `unittest.mock`.

## Background (already confirmed during analysis — do not re-derive)

- Upstream issue: https://github.com/custom-components/zaptec/issues/272 ("Charger present after reconfiguration and deselected a charger"), OPEN, filed by sveinse.
- It references #274 as a blocker; #274 was fixed by PR #275 (merged 2025-08-08), which made `ZaptecManager.create_entities_from_descriptions` (`manager.py:106-107`) skip creating entities for any zaptec object not in `self.tracked_devices`. That fix is why deselected chargers get *no new entities* on reload — but their *old* entity-registry entries from the previous session are never explicitly removed, so they survive and keep the device alive. That's the remaining bug.
- Reconfigure (`config_flow.py:async_step_reconfigure` → `async_update_reload_and_abort`) does a full unload+reload, so `async_setup_entry` reruns with the new `configured_chargers` on every reconfigure.
- `manager.tracked_devices` (`manager.py:41`, populated by `ZaptecManager.first_time_setup`, `manager.py:232-266`) already contains exactly the right set: selected charger ids plus the installation id(s) those chargers belong to. No new data plumbing is needed — `entry.runtime_data = manager` is assigned at `__init__.py:205`, before the cleanup block runs at `__init__.py:217-245`.
- Device identifiers are `{(DOMAIN, zaptec_obj.id)}` (`manager.py:97`), so comparing `zap_dev_id` (already unpacked in the existing loop) against `tracked_devices` is a direct, correct check — same shape as the existing `circuit_ids` comparison.
- This repo has no real HA test harness (`tests/conftest.py`'s `hass` fixture is a bare `MagicMock`, documented as intentional because the real harness can't run natively on Windows here — see `FakeConfigEntry` docstring). So the cleanup logic must be extracted into its own function and unit-tested by patching `dr.async_get`/`er.async_get`/`dr.async_entries_for_config_entry`/`er.async_entries_for_device` directly — do not attempt to exercise the full `async_setup_entry`.

## Global Constraints

- Run tests with: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Lint/format gate: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff` must be clean; `ruff check` pre-existing errors elsewhere in the repo are not this task's concern, but don't introduce new ones in `__init__.py`.
- Work happens on a dedicated branch, not `master`. Branch name: `fix/issue-272-stale-device-cleanup`.
- Never commit automatically — this plan's steps stage and commit locally as part of the task cycle (per this repo's usual TDD flow), but do not push or open a PR without explicit approval.
- Do not touch unrelated files.

---

### Task 0: Create the working branch

**Files:** none (git only)

- [ ] **Step 1: Confirm a clean working tree and branch from master**

```bash
git status
git checkout master
git pull
git checkout -b fix/issue-272-stale-device-cleanup
```

Expected: new branch created, working tree clean.

---

### Task 1: Extract the existing cleanup loop into `_cleanup_stale_devices`, with regression tests for current behavior

This is a pure refactor — no behavior change yet. It exists so Task 2's new branch is added to a function that already has test coverage, instead of bolting untested logic onto an untested 28-line inline block inside a 180-line setup function.

**Files:**
- Modify: `custom_components/zaptec/__init__.py:217-247` (the inline cleanup block inside `async_setup_entry`, plus its imports)
- Test: `tests/test_init.py`

**Interfaces:**
- Produces: `_cleanup_stale_devices(hass: HomeAssistant, entry: ConfigEntry, tracked_devices: set[str], circuit_ids: set[str]) -> None`, module-level function in `custom_components/zaptec/__init__.py`. Called from `async_setup_entry` with `manager.tracked_devices` and the already-computed `circuit_ids`.

- [ ] **Step 1: Write failing regression tests for current behavior**

Add to `tests/test_init.py` (new imports at top of file, alongside the existing ones):

```python
from unittest.mock import MagicMock, patch

from custom_components.zaptec import _cleanup_stale_devices
```

Append these tests to the end of `tests/test_init.py`:

```python
def _mock_registries(device_entries: list, entities_by_device: dict) -> tuple[MagicMock, MagicMock]:
    """Patch dr.async_get/er.async_get and the two registry lookup helpers.

    Returns (device_registry_mock, entity_registry_mock) so callers can
    assert on async_remove_device/async_remove calls.
    """
    device_registry = MagicMock()
    entity_registry = MagicMock()

    def entries_for_device(_entity_registry: MagicMock, device_id: str, include_disabled_entities: bool = True) -> list:
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
    )

    device_registry.async_remove_device.assert_not_called()
    entity_registry.async_remove.assert_not_called()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py -q`
Expected: `ImportError: cannot import name '_cleanup_stale_devices'` (the function doesn't exist yet).

- [ ] **Step 3: Extract `_cleanup_stale_devices` and update the call site**

In `custom_components/zaptec/__init__.py`, replace the block currently at (approximately) lines 217-245:

```python
    # Make a set of the circuit ids from zaptec to check for deprecated Circuit-devices
    circuit_ids = {cid for c in manager.zaptec.chargers if (cid := c.get("CircuitId"))}

    # Clean up unused device entries with no entities
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry_id=entry.entry_id
    )
    for dev in device_entries:
        dev_entities = er.async_entries_for_device(
            entity_registry, dev.id, include_disabled_entities=True
        )
        if not dev_entities:
            device_registry.async_remove_device(dev.id)
            continue
        # identifiers is a set with a (single) tuple ('zaptec', '<zaptec_id>')
        for _, zap_dev_id in dev.identifiers:
            if zap_dev_id in circuit_ids:
                _LOGGER.warning(
                    "Detected deprecated Circuit device %s, "
                    "removing device and associated entities",
                    zap_dev_id,
                )
                for ent in dev_entities:
                    _LOGGER.debug("Deleting entity %s", ent.entity_id)
                    entity_registry.async_remove(ent.entity_id)
                device_registry.async_remove_device(dev.id)

    return True
```

with:

```python
    # Make a set of the circuit ids from zaptec to check for deprecated Circuit-devices
    circuit_ids = {cid for c in manager.zaptec.chargers if (cid := c.get("CircuitId"))}

    _cleanup_stale_devices(hass, entry, manager.tracked_devices, circuit_ids)

    return True
```

Then add the extracted function below `async_setup_entry` (immediately before `remove_deprecated_entities`, since both are cleanup helpers):

```python
def _cleanup_stale_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    tracked_devices: set[str],
    circuit_ids: set[str],
) -> None:
    """Remove device entries that no longer belong to this config entry.

    Three cases are handled: devices left with no entities at all, deprecated
    Circuit-devices (pre-dating a hierarchy change), and devices whose zaptec
    id is no longer tracked, e.g. a charger deselected via reconfigure.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_entries = dr.async_entries_for_config_entry(
        device_registry, config_entry_id=entry.entry_id
    )
    for dev in device_entries:
        dev_entities = er.async_entries_for_device(
            entity_registry, dev.id, include_disabled_entities=True
        )
        if not dev_entities:
            device_registry.async_remove_device(dev.id)
            continue
        # identifiers is a set with a (single) tuple ('zaptec', '<zaptec_id>')
        for _, zap_dev_id in dev.identifiers:
            if zap_dev_id in circuit_ids:
                _LOGGER.warning(
                    "Detected deprecated Circuit device %s, "
                    "removing device and associated entities",
                    zap_dev_id,
                )
            elif zap_dev_id not in tracked_devices:
                _LOGGER.warning(
                    "Detected stale device %s no longer selected, "
                    "removing device and associated entities",
                    zap_dev_id,
                )
            else:
                continue

            for ent in dev_entities:
                _LOGGER.debug("Deleting entity %s", ent.entity_id)
                entity_registry.async_remove(ent.entity_id)
            device_registry.async_remove_device(dev.id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py -q`
Expected: all tests PASS, including the 3 new ones and the pre-existing `test_config_entry_error_mapping` cases.

- [ ] **Step 5: Lint/format check**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`
Expected: no diff for the touched files (if there is one, run without `--diff` to apply it).

- [ ] **Step 6: Commit**

```bash
git add custom_components/zaptec/__init__.py tests/test_init.py
git commit -m "refactor: extract device-registry cleanup into _cleanup_stale_devices

No behavior change. Makes the cleanup logic (previously inline in
async_setup_entry) independently unit-testable, ahead of adding the
issue #272 fix."
```

---

### Task 2: Add the untracked-device removal branch (the actual #272 fix) — this is already implemented as part of Task 1's `_cleanup_stale_devices` body above

Task 1 already writes the `elif zap_dev_id not in tracked_devices:` branch together with the extraction, because the refactor and the fix are one small contiguous edit to the same function and splitting them would mean writing the function body twice. What Task 1 does **not** yet cover is a test that proves the new branch actually fires for the reported scenario (a previously-tracked charger device with real entities, now deselected). Task 2 adds exactly that test.

**Files:**
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `_cleanup_stale_devices` from Task 1 (already wired up and passing regression tests).

- [ ] **Step 1: Write the failing test for the reported bug**

Append to `tests/test_init.py`:

```python
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
    )

    entity_registry.async_remove.assert_called_once_with("sensor.stale_charger_power")
    device_registry.async_remove_device.assert_called_once_with("dev-stale")
```

- [ ] **Step 2: Run the test**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py::test_cleanup_removes_deselected_charger_device -v`
Expected: PASS (the `elif` branch from Task 1 already makes this pass — if it fails, the branch in `_cleanup_stale_devices` was not added correctly; go back and check the `elif zap_dev_id not in tracked_devices` clause).

- [ ] **Step 3: Run the full test suite**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: all tests PASS (network-dependent zconst/redact tests aside, per this repo's known `SKIP_ZAPTEC_API_TEST` limitation).

- [ ] **Step 4: Commit**

```bash
git add tests/test_init.py
git commit -m "test: cover issue #272 deselected-charger device cleanup"
```

---

### Task 3: Manual verification and wrap-up

**Files:** none (verification only)

- [ ] **Step 1: Run full lint + test gate**

```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch tests -q
```

Expected: format diff clean, all tests pass (aside from the pre-documented network-dependent zconst/redact tests).

- [ ] **Step 2: Sanity-check no unrelated diff crept in**

```bash
git diff master --stat
```

Expected: only `custom_components/zaptec/__init__.py` and `tests/test_init.py` changed.

- [ ] **Step 3: Hand off**

Report back to the user that the branch `fix/issue-272-stale-device-cleanup` is ready, summarize the diff, and use `superpowers:finishing-a-development-branch` to decide on PR vs. further review — do not push or open a PR without explicit approval (per this repo's CLAUDE.md).
