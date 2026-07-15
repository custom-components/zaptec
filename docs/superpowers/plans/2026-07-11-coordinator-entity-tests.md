# Coordinator/Entity Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `custom_components/zaptec/coordinator.py` and `custom_components/zaptec/entity.py` from ~27-29% coverage up to near-full coverage with real unit tests, runnable locally on native Windows in this dev environment.

**Architecture:** `ZaptecUpdateCoordinator` subclasses HA's `DataUpdateCoordinator` and `ZaptecBaseEntity` subclasses HA's `CoordinatorEntity` — both need a `hass`-shaped object and a `ConfigEntry`-shaped object, but a close reading of `homeassistant/helpers/update_coordinator.py` shows the only things either base class actually touches are `hass.loop` (for `loop.call_at()`/`loop.time()` scheduling) and a handful of `ConfigEntry` attributes/methods (`pref_disable_polling`, `async_on_unload`, `async_create_background_task`). Two tiny hand-written test doubles — a `MagicMock` with a real running event loop attached as `.loop`, and a small `FakeConfigEntry` class — satisfy exactly that surface without needing a real `HomeAssistant` instance. This was verified directly against the real coordinator/entity code before writing this plan (constructing `ZaptecUpdateCoordinator`, calling `set_update_interval()`, exercising `trigger_poll()`'s real background-task scheduling, and constructing `ZaptecBaseEntity` all worked under real `pytest` with these fakes). `Charger`/`Installation`/`Zaptec` objects are still faked with `unittest.mock.MagicMock(spec=...)` or a tiny `FakeZaptecObj`, keeping these tests decoupled from `api.py` (explicitly out of scope).

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode = "auto"`, already configured), `unittest.mock`. No new dependencies.

## Global Constraints

- Do not modify `custom_components/zaptec/zaptec/api.py` or its tests — out of scope for this pass.
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests -q`
- Lint gate: `"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format custom_components tests --diff` and `... -m ruff check custom_components tests` must stay clean for the files this plan touches (new test files must pass `ruff check` outright, not just under `--exit-zero`).
- **Do not add `pytest-homeassistant-custom-component` or any other new test dependency.** It was evaluated and reverted: its `hass` fixture cannot run on native Windows in this dev environment (confirmed two layers deep — `homeassistant.runner` needs POSIX-only `fcntl`/`resource` at import time, and even past that, asyncio's Windows `ProactorEventLoop` needs a real OS socket that the package's built-in `pytest-socket` safety net blocks outright). It also collides with the sibling `luxtronik` repo's shared `py314` conda env, which already lists the same package unpinned. Fine for CI (`ubuntu-latest`) or the real Dev Container, but out of scope here — this plan uses hand-rolled fakes instead so every task is verifiable locally in this session.
- Never commit without explicit user approval (per repo CLAUDE.md) — stop before each commit step and wait for approval, or if running unattended per user's chosen execution mode, treat "commit" steps as the point to pause for review.

---

## File Structure

- **Modify: `tests/conftest.py`** — add the shared `FakeConfigEntry`/`config_entry` and `hass` fixtures that Tasks 2-8 all build on.
- **Create: `tests/test_coordinator.py`** — unit tests for `ZaptecUpdateCoordinator` (init/validation, `set_update_interval`, `_async_update_data`, `trigger_poll`/`_trigger_poll`).
- **Create: `tests/test_entity.py`** — unit tests for `ZaptecBaseEntity` (init, `key`, `_get_zaptec_value`, `_handle_coordinator_update`, logging helpers, `trigger_poll`).

## Known finding (documented, not fixed, by this plan)

`entity.py`'s `_handle_coordinator_update` sets `self._attr_available = False` on a `KeyUnavailableError`, but `ZaptecBaseEntity` never overrides the `available` property, so it resolves to HA's `CoordinatorEntity.available` (`self.coordinator.last_update_success`) — `_attr_available` is never actually read for availability reporting. Confirmed empirically while validating this plan's approach (`entity._attr_available` reads `False` but `entity.available` still reads `True` afterward). Per user decision, Task 7 writes a test that documents today's real behavior (the flag is set but has no effect) rather than fixing it. File a separate follow-up issue for the fix if desired — out of scope here.

---

### Task 1: Shared `hass`/`config_entry` fixtures

**Files:**
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `hass` fixture (an async fixture yielding a `MagicMock` with `.loop` set to the real running event loop) and `config_entry` fixture (yields a `FakeConfigEntry` instance), used by every test in Tasks 2-8.

- [ ] **Step 1: Add the fixtures**

In `tests/conftest.py`, insert after the existing imports (after `from custom_components.zaptec.zaptec.api import Zaptec`) and before the first existing `@pytest.fixture`:

```python
import asyncio
from typing import Any
from unittest.mock import MagicMock
```

(add these to the existing `import` block at the top of the file, alongside the existing `import asyncio`, `import os`, `import pytest` — note `asyncio` is already imported, so only add `from typing import Any` and `from unittest.mock import MagicMock`)

Then, after the imports and before the first existing fixture, add:

```python
class FakeConfigEntry:
    """Minimal stand-in for HA's ConfigEntry, exposing only what
    coordinator.py and entity.py actually touch (`pref_disable_polling`,
    `async_on_unload`, `async_create_background_task`). A real ConfigEntry
    pulls in HA's full test-harness machinery, which cannot run on native
    Windows in this dev environment - see CLAUDE.md's environment notes.
    """

    pref_disable_polling = False
    title = "Mock Title"

    def async_on_unload(self, func: Any) -> None:
        """Record an unload callback. Never invoked by these tests."""

    def async_create_background_task(
        self, hass: Any, target: Any, name: str, eager_start: bool = True
    ) -> asyncio.Task:
        """Schedule target as a real asyncio Task, so trigger_poll()'s
        cancel-and-replace logic is genuinely exercised by tests.
        """
        return asyncio.ensure_future(target)


@pytest.fixture
def config_entry() -> FakeConfigEntry:
    """A fake config entry for coordinator/entity tests."""
    return FakeConfigEntry()


@pytest.fixture
async def hass() -> MagicMock:
    """A minimal fake HomeAssistant object exposing a real running event loop.

    DataUpdateCoordinator only reads `hass.loop` (to schedule refreshes via
    `loop.call_at()`/`loop.time()`); coordinator.py and entity.py never touch
    any other HomeAssistant functionality (config, states, services, etc.).
    """
    fake_hass = MagicMock()
    fake_hass.loop = asyncio.get_running_loop()
    return fake_hass
```

- [ ] **Step 2: Verify with a throwaway smoke check**

Run:
```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests -q
```
Expected: same pass/skip/error counts as before this change (10 passed, 2 skipped, 22 errors — the pre-existing DNS-fixture gap in `test_zconst.py`/`test_redact.py`, see `CLAUDE.md`). No new collection errors — this confirms `conftest.py` still loads cleanly and the new fixtures don't break anything (they're unused until Task 2).

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/conftest.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/conftest.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add fake hass/config_entry fixtures for coordinator/entity tests"
```

---

### Task 2: `coordinator.py` — init and validation tests

**Files:**
- Create: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (Task 1).
- Produces: `manager` fixture and `make_options()` helper, reused by Tasks 3-5.

- [ ] **Step 1: Write the test file with fixtures and init tests**

Create `tests/test_coordinator.py`:

```python
"""Tests for coordinator.py."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

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
    options = make_options(name="MyInstall", update_interval=300)
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    assert coordinator.name == f"{DOMAIN}-myinstall"
    assert coordinator.update_interval == timedelta(seconds=300)
    assert coordinator.zaptec is manager.zaptec


async def test_init_raises_if_charging_interval_without_charger(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    options = make_options(
        charging_update_interval=60,
        zaptec_object=MagicMock(spec=Installation),
    )

    with pytest.raises(ValueError, match="Charging update interval requires a Charger object"):
        ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)


async def test_init_accepts_charging_interval_with_charger(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    options = make_options(charging_update_interval=60, zaptec_object=charger)

    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    assert coordinator._charging_update_interval == timedelta(seconds=60)
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_coordinator.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_coordinator.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_coordinator.py
```
Expected: no output / no errors. Fix and re-run if `ruff format` shows a diff (apply with the non-`--diff` command).

- [ ] **Step 4: Commit**

```bash
git add tests/test_coordinator.py
git commit -m "test: add ZaptecUpdateCoordinator init/validation tests"
```

---

### Task 3: `coordinator.py` — `set_update_interval` tests

**Files:**
- Modify: `tests/test_coordinator.py` (append)

**Interfaces:**
- Consumes: `manager` fixture and `make_options()` (Task 2); `hass`, `config_entry` fixtures (Task 1).

- [ ] **Step 1: Add the `patch` import and append the tests**

Change the top-level import line in `tests/test_coordinator.py`:

```python
from unittest.mock import MagicMock
```

to:

```python
from unittest.mock import MagicMock, patch
```

Then append to `tests/test_coordinator.py`:

```python
async def test_set_update_interval_switches_between_charging_and_default(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    charger.qual_id = "Charger[abc123]"
    options = make_options(
        update_interval=600,
        charging_update_interval=60,
        zaptec_object=charger,
    )
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)
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
    charger = MagicMock(spec=Charger)
    charger.is_charging.return_value = False
    charger.qual_id = "Charger[abc123]"
    options = make_options(
        update_interval=600,
        charging_update_interval=60,
        zaptec_object=charger,
    )
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    with patch.object(coordinator, "_schedule_refresh") as mock_schedule:
        coordinator.set_update_interval()
        mock_schedule.assert_not_called()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_coordinator.py -v
```
Expected: 5 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_coordinator.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_coordinator.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_coordinator.py
git commit -m "test: add ZaptecUpdateCoordinator.set_update_interval tests"
```

---

### Task 4: `coordinator.py` — `_async_update_data` tests

**Files:**
- Modify: `tests/test_coordinator.py` (append)

**Interfaces:**
- Consumes: `manager` fixture and `make_options()` (Task 2); `hass`, `config_entry` fixtures (Task 1).

- [ ] **Step 1: Add the needed imports and append the tests**

Change the top-level import line in `tests/test_coordinator.py`:

```python
from unittest.mock import MagicMock, patch
```

to:

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

Add a new import line, directly after the `import pytest` line:

```python
from homeassistant.helpers.update_coordinator import UpdateFailed
```

Change this import line:

```python
from custom_components.zaptec.zaptec import Charger, Installation, Zaptec
```

to:

```python
from custom_components.zaptec.zaptec import Charger, Installation, Zaptec, ZaptecApiError
```

Then append to `tests/test_coordinator.py`:

```python
async def test_async_update_data_polls_zaptec_with_options(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    manager.zaptec.poll = AsyncMock()
    options = make_options(
        tracked_devices={"dev1", "dev2"},
        poll_args={"poll_state": True},
    )
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    await coordinator._async_update_data()

    manager.zaptec.poll.assert_awaited_once_with({"dev1", "dev2"}, poll_state=True)


async def test_async_update_data_raises_update_failed_on_api_error(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    manager.zaptec.poll = AsyncMock(side_effect=ZaptecApiError("boom"))
    options = make_options()
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_coordinator.py -v
```
Expected: 7 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_coordinator.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_coordinator.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_coordinator.py
git commit -m "test: add ZaptecUpdateCoordinator._async_update_data tests"
```

---

### Task 5: `coordinator.py` — `trigger_poll` / `_trigger_poll` tests

**Files:**
- Modify: `tests/test_coordinator.py` (append)

**Interfaces:**
- Consumes: `manager` fixture and `make_options()` (Task 2); `hass`, `config_entry` fixtures (Task 1); `AsyncMock` import (Task 4).

- [ ] **Step 1: Add the needed imports and append the tests**

Add a new import line at the very top of the import block in `tests/test_coordinator.py` (before `from datetime import timedelta`):

```python
import asyncio
```

Change this import line:

```python
from custom_components.zaptec.const import DOMAIN
```

to:

```python
from custom_components.zaptec.const import (
    DOMAIN,
    ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS,
    ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS,
)
```

Then append to `tests/test_coordinator.py`:

```python
async def test_trigger_poll_charger_uses_charger_delays(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    charger = MagicMock(spec=Charger)
    charger.qual_id = "Charger[abc123]"
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(coordinator, "async_refresh", AsyncMock()) as mock_refresh,
    ):
        await coordinator._trigger_poll(charger)

    assert mock_sleep.await_count == len(ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS)
    assert mock_refresh.await_count == len(ZAPTEC_POLL_CHARGER_TRIGGER_DELAYS)


async def test_trigger_poll_installation_also_triggers_tracked_children(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
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
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()) as mock_sleep,
        patch.object(coordinator, "async_refresh", AsyncMock()) as mock_refresh,
    ):
        await coordinator._trigger_poll(installation)

    assert mock_sleep.await_count == len(ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS)
    assert mock_refresh.await_count == len(ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS)
    child_coordinator.trigger_poll.assert_awaited_once()


async def test_trigger_poll_installation_skips_untracked_children(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    charger = MagicMock(spec=Charger)
    charger.id = "charger1"
    installation = MagicMock(spec=Installation)
    installation.qual_id = "Installation[abc123]"
    installation.chargers = [charger]
    manager.tracked_devices = set()  # charger1 is not tracked

    options = make_options(zaptec_object=installation)
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    with (
        patch("custom_components.zaptec.coordinator.asyncio.sleep", AsyncMock()),
        patch.object(coordinator, "async_refresh", AsyncMock()),
    ):
        # Would raise KeyError from manager.device_coordinators[charger.id] if
        # the untracked charger were not filtered out first.
        await coordinator._trigger_poll(installation)


async def test_trigger_poll_noop_without_zaptec_object(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    options = make_options(zaptec_object=None)
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

    await coordinator.trigger_poll()

    assert coordinator._trigger_task is None


async def test_trigger_poll_cancels_inflight_task_before_starting_new_one(
    hass: MagicMock, config_entry: Any, manager: MagicMock
) -> None:
    charger = MagicMock(spec=Charger)
    charger.qual_id = "Charger[abc123]"
    options = make_options(zaptec_object=charger)
    coordinator = ZaptecUpdateCoordinator(hass, entry=config_entry, manager=manager, options=options)

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
        first_task = coordinator._trigger_task
        assert first_task is not None
        assert not first_task.done()

        await coordinator.trigger_poll()

        assert first_task.cancelled()
        await asyncio.sleep(0)  # let the second task's done-callback run
        assert coordinator._trigger_task is None
        assert call_count == 2
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_coordinator.py -v
```
Expected: 12 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_coordinator.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_coordinator.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_coordinator.py
git commit -m "test: add ZaptecUpdateCoordinator.trigger_poll tests"
```

---

### Task 6: `entity.py` — init, `key`, `_get_zaptec_value` tests

**Files:**
- Create: `tests/test_entity.py`

**Interfaces:**
- Consumes: `hass`, `config_entry` fixtures (Task 1); `ZaptecUpdateCoordinator`, `ZaptecUpdateOptions` (existing).
- Produces: `coordinator`, `zaptec_obj`, `entity` fixtures and `FakeZaptecObj`, reused by Tasks 7-8.

- [ ] **Step 1: Write the test file with fixtures and value-retrieval tests**

Create `tests/test_entity.py`:

```python
"""Tests for entity.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.entity import DeviceInfo, EntityDescription

from custom_components.zaptec.coordinator import ZaptecUpdateCoordinator, ZaptecUpdateOptions
from custom_components.zaptec.entity import KeyUnavailableError, ZaptecBaseEntity
from custom_components.zaptec.zaptec import MISSING


class FakeZaptecObj:
    """Minimal stand-in for a ZaptecBase object, exposing only what ZaptecBaseEntity uses."""

    def __init__(self, obj_id: str, data: dict[str, Any]) -> None:
        self.id = obj_id
        self._data = data

    @property
    def qual_id(self) -> str:
        return f"Fake[{self.id}]"

    def get(self, key: str, default: Any = MISSING) -> Any:
        return self._data.get(key, default)


@pytest.fixture
def coordinator(hass: MagicMock, config_entry: Any) -> ZaptecUpdateCoordinator:
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


@pytest.fixture
def zaptec_obj() -> FakeZaptecObj:
    return FakeZaptecObj(
        "dev1",
        {"operating_mode": "Connected", "nested": {"inner": "value"}},
    )


@pytest.fixture
def entity(coordinator: ZaptecUpdateCoordinator, zaptec_obj: FakeZaptecObj) -> ZaptecBaseEntity:
    description = EntityDescription(key="operating_mode")
    return ZaptecBaseEntity(coordinator, zaptec_obj, description, DeviceInfo())


def test_init_sets_unique_id_device_info_and_log_key(
    entity: ZaptecBaseEntity, zaptec_obj: FakeZaptecObj
) -> None:
    assert entity._attr_unique_id == "dev1_operating_mode"
    assert entity._attr_device_info == DeviceInfo()
    assert entity._log_zaptec_key == "operating_mode"


def test_key_property_returns_description_key(entity: ZaptecBaseEntity) -> None:
    assert entity.key == "operating_mode"


def test_get_zaptec_value_returns_value(entity: ZaptecBaseEntity) -> None:
    assert entity._get_zaptec_value() == "Connected"


def test_get_zaptec_value_lower_cases_string(entity: ZaptecBaseEntity) -> None:
    assert entity._get_zaptec_value(lower_case_str=True) == "connected"


def test_get_zaptec_value_follows_dotted_key(entity: ZaptecBaseEntity) -> None:
    assert entity._get_zaptec_value(key="nested.inner") == "value"


def test_get_zaptec_value_returns_default_without_raising(entity: ZaptecBaseEntity) -> None:
    assert entity._get_zaptec_value(key="missing_key", default="fallback") == "fallback"


def test_get_zaptec_value_raises_when_key_missing(entity: ZaptecBaseEntity) -> None:
    with pytest.raises(KeyUnavailableError) as exc_info:
        entity._get_zaptec_value(key="missing_key")
    assert exc_info.value.key == "missing_key"


def test_get_zaptec_value_raises_when_object_is_not_a_mapping(entity: ZaptecBaseEntity) -> None:
    class NotMapping:
        pass

    entity.zaptec_obj = NotMapping()  # type: ignore[assignment]

    with pytest.raises(KeyUnavailableError):
        entity._get_zaptec_value(key="operating_mode")
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_entity.py -v
```
Expected: 8 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_entity.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_entity.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_entity.py
git commit -m "test: add ZaptecBaseEntity init and _get_zaptec_value tests"
```

---

### Task 7: `entity.py` — `_handle_coordinator_update` and logging helper tests

**Files:**
- Modify: `tests/test_entity.py` (append)

**Interfaces:**
- Consumes: `entity` fixture (Task 6).

- [ ] **Step 1: Add the `logging` import and append the tests**

Add a new import line at the very top of the import block in `tests/test_entity.py` (before `from typing import Any`):

```python
import logging
```

Then append to `tests/test_entity.py`:

```python
def test_handle_coordinator_update_success_updates_value_and_writes_state(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.async_write_ha_state = MagicMock()
    entity._log_attribute = "some_attr"
    entity.some_attr = "new_value"
    entity._update_from_zaptec = lambda: None

    with caplog.at_level(logging.DEBUG):
        entity._handle_coordinator_update()

    entity.async_write_ha_state.assert_called_once()
    assert "new_value" in caplog.text


def test_handle_coordinator_update_key_unavailable_sets_attr_available_false(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.async_write_ha_state = MagicMock()

    def raise_unavailable() -> None:
        raise KeyUnavailableError("operating_mode", "boom")

    entity._update_from_zaptec = raise_unavailable

    with caplog.at_level(logging.INFO):
        entity._handle_coordinator_update()

    # NOTE: this sets _attr_available, but ZaptecBaseEntity does not override
    # the `available` property inherited from HA's CoordinatorEntity (which
    # returns coordinator.last_update_success instead), so this flag currently
    # has no effect on the entity's actual reported availability. This test
    # documents today's real behavior, not the intended one - see the "Known
    # finding" note at the top of this plan.
    assert entity._attr_available is False
    assert "sensor.test is unavailable" in caplog.text
    entity.async_write_ha_state.assert_called_once()


def test_log_zaptec_attribute_formats_string_key(entity: ZaptecBaseEntity) -> None:
    entity._log_zaptec_key = "operating_mode"
    assert entity._log_zaptec_attribute == ".operating_mode"


def test_log_zaptec_attribute_formats_none_key(entity: ZaptecBaseEntity) -> None:
    entity._log_zaptec_key = None
    assert entity._log_zaptec_attribute == ""


def test_log_zaptec_attribute_formats_iterable_key(entity: ZaptecBaseEntity) -> None:
    entity._log_zaptec_key = ["mode", "state"]
    assert entity._log_zaptec_attribute == ".mode and .state"


def test_log_value_logs_on_change(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")

    assert "value1" in caplog.text
    assert entity._prev_value == "value1"


def test_log_value_skips_logging_when_unchanged(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"
    entity._prev_value = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")

    assert caplog.text == ""


def test_log_value_force_logs_even_when_unchanged(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.some_attr = "value1"
    entity._prev_value = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr", force=True)

    assert "value1" in caplog.text


def test_log_value_noop_for_none_attribute(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        entity._log_value(None)

    assert caplog.text == ""


def test_log_unavailable_logs_on_transition_to_unavailable(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity._attr_available = False

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable()

    assert "Entity sensor.test is unavailable" in caplog.text


def test_log_unavailable_logs_error_for_unexpected_exception(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity._attr_available = False

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=ValueError("boom"))

    assert "Getting value failed" in caplog.text


def test_log_unavailable_skips_error_for_key_unavailable_error(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity._attr_available = False

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=KeyUnavailableError("some_key", "boom"))

    assert "Getting value failed" not in caplog.text


def test_log_unavailable_skips_error_for_keys_in_skip_set(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity.entity_description = EntityDescription(key="three_to_one_phase_switch_current")
    entity._attr_available = False

    with caplog.at_level(logging.DEBUG):
        entity._log_unavailable(exception=ValueError("boom"))

    assert "Getting value failed" not in caplog.text


def test_log_unavailable_logs_on_recovery(
    entity: ZaptecBaseEntity, caplog: pytest.LogCaptureFixture
) -> None:
    entity.entity_id = "sensor.test"
    entity._prev_available = False
    entity._attr_available = True

    with caplog.at_level(logging.INFO):
        entity._log_unavailable()

    assert "Entity sensor.test is available" in caplog.text
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_entity.py -v
```
Expected: 21 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_entity.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_entity.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_entity.py
git commit -m "test: add ZaptecBaseEntity update-handling and logging tests"
```

---

### Task 8: `entity.py` — `trigger_poll` delegation test

**Files:**
- Modify: `tests/test_entity.py` (append)

**Interfaces:**
- Consumes: `entity`, `coordinator` fixtures (Task 6).

- [ ] **Step 1: Add the `AsyncMock` import and append the test**

Change the import line:

```python
from unittest.mock import MagicMock
```

to:

```python
from unittest.mock import AsyncMock, MagicMock
```

Append to `tests/test_entity.py`:

```python
async def test_trigger_poll_delegates_to_coordinator(
    entity: ZaptecBaseEntity, coordinator: ZaptecUpdateCoordinator
) -> None:
    coordinator.trigger_poll = AsyncMock()

    await entity.trigger_poll()

    coordinator.trigger_poll.assert_awaited_once()
```

- [ ] **Step 2: Run the tests**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_entity.py -v
```
Expected: 22 passed.

- [ ] **Step 3: Lint**

```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format tests/test_entity.py --diff
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check tests/test_entity.py
```

- [ ] **Step 4: Full suite + coverage check**

```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch --cov-report=term-missing tests -q
```
Expected: `coordinator.py` and `entity.py` both at or near 100% (any remaining gaps should only be `if TYPE_CHECKING:` blocks or defensive lines, both excluded by `[tool.coverage.report] exclude_lines` in `pyproject.toml`). The pre-existing `test_zconst.py`/`test_redact.py` DNS-fixture errors are expected and unrelated (see `CLAUDE.md`).

- [ ] **Step 5: Commit**

```bash
git add tests/test_entity.py
git commit -m "test: add ZaptecBaseEntity.trigger_poll delegation test"
```

---

## Self-Review

**Coverage of stated goal:** Tasks 2-5 cover every method in `coordinator.py` (`__init__` incl. the `ValueError` guard, `set_update_interval`, `_async_update_data` incl. the `UpdateFailed` path, `_trigger_poll` for both `Charger` and `Installation` incl. the tracked/untracked-children branches, `trigger_poll` incl. the no-op and cancel-and-replace paths). Tasks 6-8 cover every method in `entity.py` (`__init__`, `key`, `_get_zaptec_value` incl. all four exit paths, `_handle_coordinator_update` incl. both branches, `_log_zaptec_attribute` all three branches, `_log_value` all three branches, `_log_unavailable` all four branches, `trigger_poll`).

**Placeholder scan:** No TBD/TODO markers; every step has complete, real code.

**Type consistency:** `ZaptecUpdateOptions` field names (`name`, `update_interval`, `charging_update_interval`, `tracked_devices`, `poll_args`, `zaptec_object`) match `coordinator.py` exactly across all tasks. `make_options()` (Task 2) is reused unchanged through Task 5. `FakeConfigEntry`/`config_entry`/`hass` fixtures (Task 1) are reused unchanged through all later tasks. `FakeZaptecObj`, `coordinator`, `zaptec_obj`, `entity` fixtures (Task 6) are reused unchanged through Tasks 7-8.

**Verification note:** The `hass`/`config_entry`/`FakeZaptecObj` fake-based approach in this plan was hand-verified against the real `coordinator.py`/`entity.py` code (construction, `set_update_interval`, `trigger_poll`'s real background-task scheduling and cancellation, `_get_zaptec_value`, `_handle_coordinator_update`) via a standalone script and a real `pytest` run before this plan was written, specifically to avoid a second failed round-trip after the `pytest-homeassistant-custom-component` approach turned out to be non-viable on native Windows in this environment.
