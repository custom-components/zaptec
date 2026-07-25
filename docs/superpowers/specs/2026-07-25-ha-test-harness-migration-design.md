# Design: Migrate coordinator/entity tests to the HA test harness

**Date:** 2026-07-25
**Status:** Approved (brainstorming complete)
**Scope of this spec:** the replacement for PR #394 (coordinator + entity tests). Establishes the reusable infrastructure that a later, separate PR (replacing #395, the platform-entity tests) will build on.

## Background & motivation

Maintainer review on PR #394 (CHANGES_REQUESTED, 2026-07-25) asked how gold/platinum HA integrations test coordinators and entities, aiming at a high-quality standard.

The current test suite is hand-rolled: it instantiates `ZaptecUpdateCoordinator` and `ZaptecBaseEntity` directly and asserts against private methods (`# noqa: SLF001` throughout `tests/test_entity.py`), with a `MagicMock`-based fake `hass` and a `FakeConfigEntry` in `tests/conftest.py`. This is white-box, implementation-coupled testing.

Gold/platinum HA integrations instead use `pytest-homeassistant-custom-component` (pytest-hacc): a real `hass`, `MockConfigEntry`, and tests that set the integration up through the normal `async_setup` path with the cloud API mocked, then assert on **public state** (`hass.states.get(...)`, entity/device registries), often via `syrupy` snapshot tests.

The original reason for the hand-rolled mocks was that pytest-hacc did not run on the maintainer's native-Windows dev environment (`homeassistant` imports `fcntl`, which is Unix-only). That is a local-dev constraint, not a project one: CI runs on Linux where pytest-hacc works, and the Windows issue is solvable with a small, OS-guarded compatibility shim (the sibling `luxtronik` integration already does exactly this).

PRs #394 and #395 have been converted to **draft** and will be replaced by PRs built on this approach.

## Goals

- Bring the coordinator + entity tests to gold/platinum shape: behavior-first, through the real HA harness.
- Establish reusable test infrastructure (`mock_zaptec` + `setup_integration` + Windows shim) that the #395 replacement reuses without re-solving anything.
- Match or beat current coverage on `coordinator.py` / `entity.py` (100% / 98%) — but via observable behavior, not private-method assertions.
- Tests must run green in **native-Windows py314** (via the shim) *and* Linux CI.

## Non-goals (out of scope for this spec)

- The six platform files (`sensor/switch/number/button/binary_sensor/update`) — that is the #395 replacement, a separate PR.
- Config-flow / `__init__` coverage.
- Snapshot tests (deferred to the #395 replacement, where full-state snapshots pay off).
- Fixing bug #410 (see "Bug #410" below — this PR stays test-only).

## Approach (selected)

**Real harness, behavior-first.** Adopt pytest-hacc, patch the integration at the `Zaptec` client boundary, and assert on public state. Chosen over (B) a like-for-like fixture swap and (C) staying hand-rolled, because it is the only option that reaches the target standard and it turns the #410 gap into a real, self-catching test.

## Design

### 1. Test infrastructure (the foundation)

- **`requirements_test.txt`** — add `pytest-homeassistant-custom-component` **unpinned**. Do NOT add `homeassistant` here: it is already pinned in `requirements.txt`, and validate.yaml sed-reverts it to the last 3.13-compatible release (`2026.2.3`) on the 3.13 matrix leg. pytest-hacc pins an exact `homeassistant==` itself, so leaving it unpinned makes pip resolve the release matching whichever HA the active Python installs — stable (transitively pinned via HA) yet 3.13/3.14-portable. Pinning an exact pytest-hacc version breaks 3.13 (newest releases require Python >=3.14).
- **`conftest.py` (repo root, new)** — port luxtronik's OS-guarded shim: under `sys.platform == "win32"`, stub `fcntl` / `resource` and wrap `socket.socketpair`, then `pytest_plugins = "pytest_homeassistant_custom_component.plugins"`. Completely no-op on Linux, so CI is unaffected. `pytest_plugins` is only honored in the rootdir conftest, so this cannot live in `tests/conftest.py`.
- **pytest config** (`pyproject.toml` or `pytest.ini`) — add `-p no:homeassistant` to block the broken plugin autoload; the root conftest re-loads it explicitly *after* shimming. Confirm during planning that the repo has no conflicting existing pytest config.
- **`tests/conftest.py`** — replace the hand-rolled `hass` / `FakeConfigEntry` with:
  - the harness's real `hass` fixture,
  - a **`mock_zaptec`** fixture: `MagicMock(spec=Zaptec)` pre-populated with a representative installation + charger object graph (and, because `Zaptec` is a `Mapping[str, ZaptecBase]`, implementing `__getitem__` / `__iter__` / `values()` to yield the fake `Charger` / `Installation` objects the platforms enumerate),
  - a **`mock_config_entry`** (`MockConfigEntry`) and a **`setup_integration(hass, mock_zaptec)`** helper that patches the client into the setup path and awaits `async_setup`.

**Why this shim is justified now (and #403 was not):** a standalone shim PR (#403) was closed because pytest-hacc was not a real dependency, so CI didn't install it and the unconditional plugin import broke Linux CI. Here pytest-hacc becomes a genuine `requirements_test.txt` dependency (CI installs it, Linux import works natively) and the shim is `win32`-guarded (never runs on Linux). Both failure modes are avoided.

**Key risk & mitigation:** the whole approach hinges on the shim making pytest-hacc run in native-Windows py314. **Plan step 1 is a throwaway feasibility probe** (a trivial `async def test_hass(hass)` under the shim) before any real test is written. Fallback if it fails: run tests in a devcontainer on the user's Raspberry Pi (HA-in-Docker, separate port). Rated low-risk because luxtronik already runs pytest-hacc in this exact py314 env.

### 2. The #394-replacement tests (coordinator + entity, behavior-first)

Same two modules, asserted through the real harness instead of poking privates.

- **Backbone / setup test** — set up the integration via `setup_integration`; assert entities land in the state machine and registry. This exercises `entity.py`'s `__init__` / `unique_id` / `device_info` wiring as a side effect, with no direct instantiation.
- **Coordinator behavior:**
  - Successful refresh → entities have expected states after `coordinator.async_refresh()`.
  - Failed refresh (mock client raises) → `coordinator.last_update_success` False → entities report `unavailable` via `hass.states.get()`.
  - Poll scheduling (`trigger_poll`, charging-interval switch) → driven via public methods and the harness's time control (`freezer` / `async_fire_time_changed`), not a hand-attached loop.
- **Entity behavior:**
  - Value present → `hass.states.get("sensor.…").state` equals the expected transformed value (covers `_get_zaptec_value`, dotted keys, lowercasing through a real entity).
  - Key missing / non-mapping object → assert actual reported availability + no crash propagation.
  - Availability transition logging — kept, asserted on observable state where possible.

**Assertion style:** move from `entity._attr_available is False  # noqa: SLF001` to `hass.states.get("sensor.x").state == "unavailable"`. A **small residue** of white-box tests is acceptable for pure-logging helpers (e.g. `_log_value` dedup) that have no observable state effect — kept to a minimum.

**Coverage target:** match or beat current 100% / 98% on `coordinator.py` / `entity.py`, achieved via behavior.

### 3. The mocked Zaptec client & shared test data

**Patch at the `Zaptec` client boundary (Layer 2), not the HTTP/SignalR wire (Layer 1).**

The integration builds the client in `__init__.py` (`zaptec = Zaptec(...)`, then `.login()`, `ZaptecManager.first_time_setup(zaptec=...)`, `ZaptecManager(..., zaptec=...)`). We patch the `Zaptec` symbol where `__init__.py` uses it so construction returns `mock_zaptec`:

```python
with patch("custom_components.zaptec.Zaptec", return_value=mock_zaptec):
    await hass.config_entries.async_setup(entry.entry_id)
```

Everything *above* the client — `ZaptecManager`, `ZaptecUpdateCoordinator`, `ZaptecBaseEntity`, all platforms — runs as real code against the mock's data.

- **Layer 1 (HTTP wire) rejected:** would additionally exercise `api.py`, but couples every setup test to the cloud's JSON/SignalR format (login, installations, chargers, constants, state polls, SignalR handshake) — large and brittle. `api.py` already has dedicated tests in `tests/zaptec/test_api.py`, so re-testing it through the integration adds fragility for no coverage gain.
- **Layer 3 (mock the manager) rejected:** too high; would stop exercising the coordinator/entity code under test.

**`spec=` discipline:** `MagicMock(spec=Zaptec)` / `spec=Charger` / `spec=Installation` so a typo'd or renamed client method fails loudly instead of returning a fresh mock. Carried over as a deliberate strength of the current tests.

**Test-data source:** a small hand-authored dict suffices for #394 (coordinator/entity base behavior needs only a couple of keys). The **fuller** payload needed by the #395 replacement will be seeded from a **redacted real diagnostics dump** (the repo already has `diagnostics.py` + `redact.py`), stored as a JSON fixture, so snapshots reflect real-world data rather than invented values.

### 4. Bug #410 handling — test-only, deferred fix

Filed as custom-components/zaptec#410: `ZaptecBaseEntity` sets `_attr_available = False` on `KeyUnavailableError` but never overrides `available`, so the flag has no effect on reported availability.

Investigation showed the fix is **not obvious** and needs maintainer input:

1. **Mechanical gap:** `available` isn't overridden (trivial to add).
2. **Latent sticky-flag bug:** `_handle_coordinator_update` never resets `_attr_available = True` on the success path, so a naive override would leave recovered entities unavailable forever. Any fix must override `available` *and* reset the flag on success.
3. **Semantic design question:** many keys are legitimately absent for some charger models / installation types / roles (the code already has a skip-set for such keys in `_log_unavailable`). Making *any* `KeyUnavailableError` flip an entity to `unavailable` could make entities disappear for real users. Which keys are "required" vs. optional is a maintainer decision.

**Decision:** this PR stays **test-only**. The availability case is asserted as today's real behavior with an `xfail(reason="#410")` documenting the gap through the real harness. The fix is deferred to a separate PR after the semantics are decided. #410 has been updated with findings (2) and (3) and a request for input from @sveinse / @steinmn.

## Success criteria (this PR)

- Coverage on `coordinator.py` / `entity.py` ≥ current (100% / 98%), achieved via behavior.
- `pytest tests` green in native-Windows py314 (via shim) **and** Linux CI.
- `ruff format` + `ruff check` clean.
- hassfest / HACS unaffected (`requirements_test.txt` is not shipped in the component; the root `conftest.py` and pytest config are dev-only).

## PR / branch strategy

- #394 and #395 held as draft (done 2026-07-25); reply posted on #394 explaining the direction.
- New branch off `master` for this replacement PR (per repo convention: dedicated branch per unit of work).
- The #395 replacement is a **separate, later** PR that reuses this infrastructure.

## Open items carried into planning

- Confirm whether the repo has existing pytest config to reconcile (it does: `[tool.pytest.ini_options]` in `pyproject.toml`). HA version is managed by `requirements.txt` + validate.yaml's 3.13 sed-revert, not by `requirements_test.txt`.
- Confirm exact patch target (`custom_components.zaptec.Zaptec` import site) and the minimal `mock_zaptec` object-graph shape for #394.
- Feasibility probe (plan step 1) before writing real tests.
