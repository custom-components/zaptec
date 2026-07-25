# HA Test-Harness Migration (coordinator + entity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PR #394's hand-rolled coordinator/entity unit tests with behavior-first tests running on the real `pytest-homeassistant-custom-component` (pytest-hacc) harness, plus the reusable infrastructure the later #395 replacement will build on.

**Architecture:** Adopt pytest-hacc with a repo-root, `win32`-guarded compatibility shim so the harness runs on native-Windows `py314` and on Linux CI. Tests set the integration up through the real `hass` + `MockConfigEntry`, patching only the `Zaptec` client at its construction boundary (`patch("custom_components.zaptec.Zaptec", ...)`) so the manager, coordinators, entities, and platforms all run as real code against canned data. Assertions target public state (`hass.states.get(...)`, registries) instead of private methods.

**Tech Stack:** Python 3.13/3.14 (CI matrix), Home Assistant 2026.4.3 (3.14) / 2026.2.3 (3.13 revert), `pytest-homeassistant-custom-component` (unpinned, follows HA), pytest 9, `syrupy` (available, used later by #395), `MagicMock(spec=...)` test doubles.

## Global Constraints

- Do NOT add `homeassistant` to `requirements_test.txt` (it is pinned in `requirements.txt`, with a 3.13 sed-revert in validate.yaml). Leave `pytest-homeassistant-custom-component` UNPINNED so it transitively resolves to the release matching whichever HA the active Python leg installs. Pinning an exact pytest-hacc version breaks CI's 3.13 leg (newest releases require Python >=3.14).
- The Windows shim MUST be guarded by `if sys.platform == "win32":` — it must be a complete no-op on Linux CI.
- No production code changes in `custom_components/**`. This PR is test-only. Bug #410 is documented via `xfail`, never fixed here.
- Local run command in this env: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest <args>`. Use forward slashes for the python.exe path.
- Ruff (format + check) must be clean on all new/changed files, pinned ruff `0.15.22`.
- Never `git commit` without explicit user approval (project CLAUDE.md). Each task's "Commit" step means: stage, show the diff, and request approval before committing.
- `[tool.pytest.ini_options]` in `pyproject.toml` already sets `asyncio_mode = "auto"` and `filterwarnings = ["ignore::DeprecationWarning"]`. Reuse these; do not duplicate.

---

### Task 1: pytest-hacc harness infrastructure + Windows shim

**Files:**
- Modify: `requirements_test.txt`
- Create: `conftest.py` (repo root)
- Modify: `pyproject.toml` (add `addopts` under `[tool.pytest.ini_options]`)
- Test: `tests/test_harness_smoke.py` (temporary smoke test, removed in Task 5)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a working real `hass` fixture available to every test; the repo-root `conftest.py` re-loads the pytest-hacc plugin after applying the Windows shim.

- [ ] **Step 1: Add pinned test dependencies**

Replace the contents of `requirements_test.txt` with:

```
pytest
pytest-asyncio
pytest-mock
pytest-cov
pytest-homeassistant-custom-component
```

- [ ] **Step 2: Create the repo-root conftest with the Windows shim**

Create `conftest.py` at the repo root (NOT in `tests/` — `pytest_plugins` is only honored in the rootdir conftest):

```python
"""Repo-root conftest: load pytest-homeassistant-custom-component explicitly.

The plugin autoloads via a pytest11 entry point, but importing it on Windows
fails immediately (`homeassistant.runner` imports `fcntl`, Unix-only) before any
test collects. `-p no:homeassistant` in pyproject.toml blocks that autoload;
this file loads the plugin back explicitly, with Windows compatibility shims
applied first. pytest only honors `pytest_plugins` in the rootdir conftest, so
this cannot live in tests/conftest.py. The shim is a no-op on Linux (CI), where
fcntl/resource exist and the plugin imports natively.
"""

import sys
import types

if sys.platform == "win32":
    if "fcntl" not in sys.modules:
        fake_fcntl = types.ModuleType("fcntl")
        fake_fcntl.LOCK_SH = 1
        fake_fcntl.LOCK_EX = 2
        fake_fcntl.LOCK_NB = 4
        fake_fcntl.LOCK_UN = 8
        fake_fcntl.flock = lambda *args, **kwargs: None
        fake_fcntl.lockf = lambda *args, **kwargs: None
        fake_fcntl.fcntl = lambda *args, **kwargs: 0
        fake_fcntl.ioctl = lambda *args, **kwargs: 0
        sys.modules["fcntl"] = fake_fcntl

    if "resource" not in sys.modules:
        fake_resource = types.ModuleType("resource")
        fake_resource.RLIMIT_NOFILE = 7
        fake_resource.RLIM_INFINITY = -1
        fake_resource.getrlimit = lambda *args, **kwargs: (8192, 8192)
        fake_resource.setrlimit = lambda *args, **kwargs: None
        sys.modules["resource"] = fake_resource

    import socket as _socket_mod

    _orig_socketpair = _socket_mod.socketpair

    def _shimmed_socketpair(*args, **kwargs):
        blocked = getattr(_socket_mod.socket, "__module__", "") == "pytest_socket"
        if not blocked:
            return _orig_socketpair(*args, **kwargs)
        import pytest_socket

        pytest_socket.enable_socket()
        try:
            return _orig_socketpair(*args, **kwargs)
        finally:
            pytest_socket.socket_allow_hosts(["127.0.0.1"])
            pytest_socket.disable_socket(allow_unix_socket=True)

    _socket_mod.socketpair = _shimmed_socketpair

pytest_plugins = "pytest_homeassistant_custom_component.plugins"
```

- [ ] **Step 3: Block the broken plugin autoload in pyproject.toml**

Add an `addopts` line inside the existing `[tool.pytest.ini_options]` table in `pyproject.toml` (leave `asyncio_mode`, `filterwarnings`, `pythonpath`, `testpaths` as-is):

```toml
addopts = "-p no:homeassistant"
```

- [ ] **Step 4: Write the smoke test**

Create `tests/test_harness_smoke.py`:

```python
"""Smoke test: the real HA `hass` fixture spins up under the shim. Removed in Task 5."""

from homeassistant.core import HomeAssistant


async def test_hass_fixture_starts(hass: HomeAssistant) -> None:
    """The harness's real hass fixture is a live HomeAssistant with a working state machine."""
    assert isinstance(hass, HomeAssistant)
    hass.states.async_set("probe.entity", "on")
    await hass.async_block_till_done()
    assert hass.states.get("probe.entity").state == "on"
```

- [ ] **Step 5: Run the smoke test**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_harness_smoke.py -v`
Expected: PASS (1 passed). If it errors with `No module named 'fcntl'`, the shim/rootdir wiring is wrong — verify `conftest.py` is at repo root and `addopts` was added.

- [ ] **Step 6: Verify the rest of the suite still collects**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: existing tests still pass/skip as before (the known `test_zconst.py`/`test_redact.py` DNS errors may appear — that is pre-existing and unrelated). No new collection errors.

- [ ] **Step 7: Ruff + commit**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format . --diff` and `-m ruff check`. Fix any issues.
Then stage `requirements_test.txt`, `conftest.py`, `pyproject.toml`, `tests/test_harness_smoke.py`, show the diff, and request approval before:

```bash
git add requirements_test.txt conftest.py pyproject.toml tests/test_harness_smoke.py
git commit -m "test: adopt pytest-homeassistant-custom-component harness with Windows shim"
```

---

### Task 2: Shared fixtures — mock Zaptec client, MockConfigEntry, setup helper

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_init.py` (add an integration-setup test alongside the existing `test_config_entry_error_mapping`)

**Interfaces:**
- Consumes: the real `hass` fixture (Task 1).
- Produces, in `tests/conftest.py`:
  - `make_charger(data: dict, *, installation=None, charging: bool = False) -> MagicMock` — a `MagicMock(spec=Charger)` whose `.get(key, default=MISSING)` is backed by `data`, with `.id`, `.name`, `.model`, `.qual_id`, `.is_charging()`, `.installation` wired.
  - `make_installation(data: dict, *, chargers=()) -> MagicMock` — a `MagicMock(spec=Installation)` similarly backed, with `.chargers`, async `.stream_main`/`.stream_close`.
  - `mock_zaptec` fixture → `MagicMock(spec=Zaptec)` exposing Mapping access (`__getitem__`/`__iter__`/`__contains__`/`__len__`), `.objects()`, `.installations`, `.chargers`, async `.login`/`.build`/`.poll`, and `.redact`, seeded with one installation + one charger.
  - `mock_config_entry` fixture → `MockConfigEntry` for domain `zaptec`.
  - `setup_integration(hass, mock_config_entry, mock_zaptec) -> ZaptecManager` async helper that patches the client and runs full `async_setup`.

- [ ] **Step 1: Write the failing integration-setup test**

Add to `tests/test_init.py`:

```python
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.zaptec.manager import ZaptecManager


async def test_setup_entry_creates_manager_and_entities(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A full setup wires up the manager and registers at least one entity."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert isinstance(manager, ZaptecManager)
    assert mock_config_entry.runtime_data is manager
    # At least one entity from the seeded charger reached the state machine.
    states = [s for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("mock")]
    assert states, "expected at least one zaptec entity to be created"
```

(Note: `setup_integration`, `mock_config_entry`, `mock_zaptec` come from `tests/conftest.py`, added next.)

- [ ] **Step 2: Run it to verify it fails**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py::test_setup_entry_creates_manager_and_entities -v`
Expected: FAIL — `fixture 'mock_config_entry' not found` (or `NameError: setup_integration`).

- [ ] **Step 3: Add the fixtures and helper to tests/conftest.py**

Append to `tests/conftest.py` (keep the existing api-login fixtures):

```python
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.zaptec.manager import ZaptecManager
from custom_components.zaptec.zaptec import MISSING, Charger, Installation


def _backed_get(data: dict):
    """Return a `.get(key, default=MISSING)` implementation backed by `data`."""

    def _get(key, default=MISSING):
        return data.get(key, default)

    return _get


def make_charger(data: dict, *, installation=None, charging: bool = False) -> MagicMock:
    """Build a spec'd Charger double backed by `data`."""
    charger = MagicMock(spec=Charger)
    charger.id = data["id"]
    charger.name = data.get("name", "Mock Charger")
    charger.model = "Zaptec Charger"
    charger.qual_id = f"Charger[{data['id'][-6:]}]"
    charger.get.side_effect = _backed_get(data)
    charger.is_charging.return_value = charging
    charger.installation = installation
    return charger


def make_installation(data: dict, *, chargers=()) -> MagicMock:
    """Build a spec'd Installation double backed by `data`."""
    install = MagicMock(spec=Installation)
    install.id = data["id"]
    install.name = data.get("name", "Mock Installation")
    install.model = "Zaptec Installation"
    install.qual_id = f"Installation[{data['id'][-6:]}]"
    install.get.side_effect = _backed_get(data)
    install.chargers = list(chargers)
    install.stream_main = AsyncMock(return_value=None)
    install.stream_close = AsyncMock(return_value=None)
    return install


@pytest.fixture
def mock_zaptec() -> MagicMock:
    """A spec'd Zaptec client seeded with one installation and one charger."""
    installation = make_installation({"id": "inst-mock-1", "name": "Mock Home"})
    charger = make_charger(
        {
            "id": "chg-mock-1",
            "name": "Mock Charger",
            # Keys read by entities under test; extend as needed for coverage.
            "operating_mode": "Connected",
            "charger_operation_mode": "Connected",
        },
        installation=installation,
        charging=False,
    )
    installation.chargers = [charger]

    objects = {installation.id: installation, charger.id: charger}

    zaptec = MagicMock(spec=Zaptec)
    zaptec.__getitem__.side_effect = objects.__getitem__
    zaptec.__iter__.side_effect = lambda: iter(objects)
    zaptec.__contains__.side_effect = objects.__contains__
    zaptec.__len__.side_effect = lambda: len(objects)
    zaptec.objects.return_value = list(objects.values())
    zaptec.installations = [installation]
    zaptec.chargers = [charger]
    zaptec.login = AsyncMock(return_value=None)
    zaptec.build = AsyncMock(return_value=None)
    zaptec.poll = AsyncMock(return_value=None)
    zaptec.show_all_updates = False
    zaptec.redact = MagicMock()
    zaptec.redact.dumps.return_value = ""
    return zaptec


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A MockConfigEntry for the zaptec domain."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Mock Zaptec",
        data={CONF_USERNAME: "user", CONF_PASSWORD: "pass"},
        entry_id="mock_entry_1",
    )


async def setup_integration(hass, mock_config_entry, mock_zaptec) -> ZaptecManager:
    """Set the integration up through the real async_setup, with a mocked client."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.zaptec.Zaptec", return_value=mock_zaptec):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry.runtime_data
```

Also add these imports at the top of `tests/test_init.py` so the test can call the helper:

```python
from tests.conftest import setup_integration
```

- [ ] **Step 4: Run and iterate to green**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_init.py -v`
Expected: PASS. Likely iteration points (fix against the real code if they surface):
- `CONF_USERNAME`/`CONF_PASSWORD`/`DOMAIN` import path — confirm they live in `custom_components/zaptec/const.py`; adjust if re-exported elsewhere.
- If setup calls a `Zaptec` member not wired above (e.g. an attribute read during `async_setup_entry`), add it to `mock_zaptec` as a `MagicMock`/`AsyncMock`. Check the traceback for the exact missing member.
- `enable_custom_integrations` — pytest-hacc's autouse fixture should load `custom_components.zaptec`; if the domain isn't found, add the `enable_custom_integrations` fixture arg to the test.

- [ ] **Step 5: Ruff + commit**

Run ruff format/check. Then stage `tests/conftest.py`, `tests/test_init.py`, show diff, request approval:

```bash
git add tests/conftest.py tests/test_init.py
git commit -m "test: add real-harness setup fixtures (mock Zaptec client + MockConfigEntry)"
```

---

### Task 3: Coordinator behavior tests

**Files:**
- Test: `tests/test_coordinator.py` (create)

**Interfaces:**
- Consumes: `mock_zaptec`, `mock_config_entry`, `setup_integration` (Task 2); `hass` (Task 1).
- Produces: behavior coverage of `coordinator.py` via public coordinator API.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coordinator.py`:

```python
"""Behavior tests for ZaptecUpdateCoordinator, driven through the real harness."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.zaptec.zaptec import ZaptecApiError
from tests.conftest import setup_integration


async def test_successful_poll_marks_last_update_success(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A successful poll leaves every coordinator reporting success."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    for coordinator in manager.all_coordinators:
        assert coordinator.last_update_success is True
    mock_zaptec.poll.assert_awaited()


async def test_poll_failure_sets_update_failed(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A ZaptecApiError during poll flips last_update_success to False."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    head = manager.head_coordinator

    mock_zaptec.poll.side_effect = ZaptecApiError("boom")
    await head.async_refresh()

    assert head.last_update_success is False


async def test_device_coordinator_switches_interval_when_charging(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A charger's coordinator uses the shorter interval once it reports charging."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    charger_coord = manager.device_coordinators["chg-mock-1"]
    idle_interval = charger_coord.update_interval

    # Flip the seeded charger to 'charging' and re-run the update-listener path.
    mock_zaptec.chargers[0].is_charging.return_value = True
    charger_coord.set_update_interval()

    assert charger_coord.update_interval < idle_interval
```

- [ ] **Step 2: Run to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_coordinator.py -v`
Expected: FAIL only if something is wired wrong — these use already-built fixtures, so a failure here signals a fixture gap (e.g. `set_update_interval` needs `options.zaptec_object` to be the same charger mock; confirm `mock_zaptec.chargers[0]` is the object stored as `zaptec["chg-mock-1"]`). Fix in `tests/conftest.py` if needed.

- [ ] **Step 3: Make them pass**

Iterate on fixtures/assertions until green. The charging-interval test depends on `ZAPTEC_POLL_INTERVAL_CHARGING < ZAPTEC_POLL_INTERVAL_IDLE` (true in `const.py`) and on the device coordinator's `options.zaptec_object` being a `Charger` — verify `make_charger` returns a `spec=Charger` instance so `isinstance(..., Charger)` in the coordinator passes. If `isinstance` fails against `MagicMock(spec=Charger)`, switch that check by constructing a real `Charger` (see Task 2 iteration note) or confirm `spec=Charger` satisfies `isinstance` (it does for `MagicMock(spec=Cls)`).

- [ ] **Step 4: Run to verify pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_coordinator.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Ruff + commit**

```bash
git add tests/test_coordinator.py
git commit -m "test: behavior coverage for ZaptecUpdateCoordinator via real harness"
```

---

### Task 4: Entity behavior tests (incl. #410 xfail)

**Files:**
- Test: `tests/test_entity.py` (create)

**Interfaces:**
- Consumes: `mock_zaptec`, `mock_config_entry`, `setup_integration`, `hass`.
- Produces: behavior coverage of `entity.py`; documents #410 via `xfail`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entity.py`. The value/availability tests read a real entity's public state; the two logging-dedup assertions are the deliberately-allowed small white-box residue.

```python
"""Behavior tests for ZaptecBaseEntity, driven through the real harness."""

import logging

from homeassistant.core import HomeAssistant
import pytest

from tests.conftest import setup_integration


async def _get_zaptec_entity(hass: HomeAssistant):
    """Return one live zaptec entity_id whose value is backed by seeded data."""
    for state in hass.states.async_all():
        if state.entity_id.startswith(("sensor.", "binary_sensor.", "switch.", "number.")):
            return state.entity_id
    raise AssertionError("no zaptec entity found")


async def test_entity_reports_value_from_zaptec(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A backed key surfaces as the entity's state (not 'unavailable'/'unknown')."""
    await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)
    state = hass.states.get(entity_id)
    assert state.state not in ("unavailable", "unknown")


@pytest.mark.xfail(
    reason="#410: _attr_available is set on KeyUnavailableError but never affects "
    "reported availability (available is not overridden). Documenting current behavior.",
    strict=True,
)
async def test_entity_becomes_unavailable_when_key_missing(
    hass: HomeAssistant, mock_config_entry, mock_zaptec
) -> None:
    """A key that disappears SHOULD mark the entity unavailable (currently it does not — #410)."""
    await setup_integration(hass, mock_config_entry, mock_zaptec)
    entity_id = await _get_zaptec_entity(hass)

    # Make every key lookup miss, then re-run a refresh so entities re-read.
    from custom_components.zaptec.zaptec import MISSING

    mock_zaptec.chargers[0].get.side_effect = lambda key, default=MISSING: default
    manager = mock_config_entry.runtime_data
    for coordinator in manager.all_coordinators:
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    # This assertion is what SHOULD hold; strict xfail => the test failing here is expected
    # and will turn XPASS (alerting us) once #410 is fixed.
    assert hass.states.get(entity_id).state == "unavailable"
```

- [ ] **Step 2: Run to verify status**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_entity.py -v`
Expected: `test_entity_reports_value_from_zaptec` PASS; `test_entity_becomes_unavailable_when_key_missing` XFAIL (not FAIL, not XPASS). If it XPASSes, #410 is somehow already satisfied — stop and re-examine before proceeding.

- [ ] **Step 3: Add the logging-dedup residue tests**

These cover `_log_value`'s change-detection, which has no observable state effect, so a small white-box test is justified per the spec. Append to `tests/test_entity.py`:

```python
async def test_log_value_logs_on_change_then_skips_when_unchanged(
    hass: HomeAssistant, mock_config_entry, mock_zaptec, caplog
) -> None:
    """_log_value logs when the tracked value changes and stays quiet when it doesn't."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)
    # Grab a real entity instance from the platform via the coordinator's listeners.
    coordinator = manager.device_coordinators["chg-mock-1"]
    entity = next(iter(coordinator._listeners.values()))[0].__self__  # noqa: SLF001
    entity.some_attr = "value1"

    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert "value1" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        entity._log_value("some_attr")  # noqa: SLF001
    assert caplog.text == ""
```

Note: retrieving the entity instance from `coordinator._listeners` is fragile; if it doesn't resolve cleanly, instead import a concrete entity class (e.g. from `sensor.py`) and instantiate it directly with the `mock_zaptec` charger + the real coordinator — a minimal, contained white-box construction. Confirm the exact listener structure during implementation.

- [ ] **Step 4: Run to verify pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_entity.py -v`
Expected: 2 passed, 1 xfailed.

- [ ] **Step 5: Ruff + commit**

```bash
git add tests/test_entity.py
git commit -m "test: behavior coverage for ZaptecBaseEntity; xfail documents #410"
```

---

### Task 5: Coverage verification, smoke-test cleanup, final gate

**Files:**
- Delete: `tests/test_harness_smoke.py`
- Verify only: coverage on `coordinator.py` / `entity.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: the final, CI-ready state.

- [ ] **Step 1: Remove the temporary smoke test**

The `hass` fixture is now exercised by real tests; delete `tests/test_harness_smoke.py`.

- [ ] **Step 2: Coverage check on the two target modules**

Run:
```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest \
  tests/test_coordinator.py tests/test_entity.py tests/test_init.py \
  --cov=custom_components/zaptec/coordinator --cov=custom_components/zaptec/entity \
  --cov-branch --cov-report=term-missing
```
Expected: `coordinator.py` and `entity.py` at or above the pre-migration numbers (100% / 98%). If below, add targeted behavior tests for the uncovered lines (name them in the gap and add a test in the appropriate file); do not pad with white-box tests where a behavior test is possible.

- [ ] **Step 3: Full suite + lint gate**

Run all three, expect clean (bar the pre-existing `test_zconst.py`/`test_redact.py` DNS errors):
```bash
SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format . --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check
```

- [ ] **Step 4: hassfest/HACS sanity (manual)**

Confirm no shipped-component files changed: `git diff --stat master -- custom_components/` must be empty. `requirements_test.txt`, root `conftest.py`, and `pyproject.toml` are dev-only and not shipped. (Use the `hassfest-hacs-check` skill for the checklist.)

- [ ] **Step 5: Commit + push**

```bash
git add -A
git commit -m "test: remove temporary harness smoke test after migration"
git push -u origin test/ha-test-harness-migration
```
Then (with user approval) open the replacement PR against `custom-components/zaptec:master`, noting in the body that it replaces #394, is stacked-independent of the upstream PR queue (see upstream-pr-stack), and that #410 is deferred/xfail pending maintainer input.

---

## Self-Review

**Spec coverage:**
- Infra (spec §1): Task 1 — requirements pin, root conftest shim, `-p no:homeassistant`. ✓
- `mock_zaptec`/`setup_integration` (spec §3, Layer-2 patch): Task 2. ✓
- Coordinator + entity behavior (spec §2): Tasks 3–4, asserting public state. ✓
- Small white-box residue allowed (spec §2): Task 4 Step 3, explicitly bounded. ✓
- #410 test-only via xfail (spec §4): Task 4 Step 1, `strict=True`. ✓
- Success criteria (spec): coverage + native-Windows + CI + ruff + hassfest — Task 5. ✓
- Non-goals (platforms/snapshots/#410 fix): correctly excluded; snapshots + full platform coverage left to the #395 replacement. ✓

**Placeholder scan:** No "TBD"/"handle edge cases" — each code step has concrete code. The two acknowledged fragile spots (entity retrieval from `_listeners`; `isinstance` vs `spec=`) carry explicit fallbacks, not vague hand-waves.

**Type consistency:** `setup_integration(hass, mock_config_entry, mock_zaptec) -> ZaptecManager`, `make_charger`/`make_installation`, and the charger id `"chg-mock-1"` are used identically across Tasks 2–4. `mock_zaptec.chargers[0]` is the same object as `zaptec["chg-mock-1"]` (seeded from one `objects` dict), which Task 3's interval test relies on.

## Known risks carried into execution

1. `MagicMock(spec=Charger)` must satisfy `isinstance(obj, Charger)` in `coordinator.py:84` — true for `spec=`, but if a real `Charger` is needed, Task 2's iteration note covers constructing one with canned `_attrs`.
2. Full `async_setup` pulls in services + all six platforms + streams; the mock must satisfy whatever they touch. Task 2 Step 4 is the iteration point; add missing mock members from tracebacks.
3. Entity-instance retrieval for the logging-residue test is implementation-coupled; Task 4 Step 3 gives a direct-construction fallback.
