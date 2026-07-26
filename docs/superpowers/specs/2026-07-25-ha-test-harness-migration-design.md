# Design: Migrate coordinator/entity tests to the HA test harness

**Date:** 2026-07-25 (revised 2026-07-26: Linux-native infra + harness scoping per #257)
**Status:** Approved (brainstorming complete)
**Scope of this spec:** the replacement for PR #394 (coordinator + entity tests). Establishes the reusable infrastructure that a later, separate PR (replacing #395, the platform-entity tests) will build on.

## Background & motivation

Maintainer review on PR #394 (CHANGES_REQUESTED, 2026-07-25) asked how gold/platinum HA integrations test coordinators and entities, aiming at a high-quality standard.

The current test suite is hand-rolled: it instantiates `ZaptecUpdateCoordinator` and `ZaptecBaseEntity` directly and asserts against private methods (`# noqa: SLF001` throughout `tests/test_entity.py`), with a `MagicMock`-based fake `hass` and a `FakeConfigEntry` in `tests/conftest.py`. This is white-box, implementation-coupled testing.

Gold/platinum HA integrations instead use `pytest-homeassistant-custom-component` (pytest-hacc): a real `hass`, `MockConfigEntry`, and tests that set the integration up through the normal `async_setup` path with the cloud API mocked, then assert on **public state** (`hass.states.get(...)`, entity/device registries), often via `syrupy` snapshot tests.

PRs #394 and #395 have been converted to **draft** and will be replaced by PRs built on this approach.

## The two-audience problem this spec must solve

The test suite has two structurally different halves, and pytest-hacc changes the rules for one of them:

1. **HA-integration tests** (`tests/test_*.py`: coordinator, entity, init, diagnostics) — these want the real `hass` harness. pytest-hacc is exactly right here.
2. **API-client tests** (`tests/zaptec/*`: `test_zconst`, `test_redact`, `test_api`, `test_utils`, `test_validate`) — these test the vendored `zaptec/` client, which per **issue #257** is a **standalone-PyPI-library-in-waiting** (sveinse: *"the API access parts will have to be a separate library on pypi… namespace `zaptec`, such as `from zaptec import Zaptec`"*). They have **no HA dependency** and must not acquire one. Two of them (`test_zconst`, `test_redact`) make a **live** call to `api.zaptec.com/api/constants`.

pytest-hacc blocks non-localhost sockets **unconditionally** on every test in any process where its plugin is active (verified empirically: `disable_socket()` + a `127.0.0.1` allow-list run in `pytest_runtest_setup` before every test; neither the `enable_socket` marker nor a manual `pytest_socket.enable_socket()` defeats the host allow-list). So the moment the harness is active in a process, the live constants call raises `SocketBlockedError`.

Interweaving the two — running `tests/zaptec/*` under the harness — both couples the future-standalone library to HA (against #257) and breaks its live call. The design keeps them **separated**: the harness governs only the HA-integration tests; the API-client tests run as plain pytest, exactly as today.

## Goals

- Bring the coordinator + entity tests to gold/platinum shape: behavior-first, through the real HA harness.
- Establish reusable test infrastructure (`mock_zaptec` + `setup_integration`) that the #395 replacement reuses without re-solving anything.
- Match or beat current coverage on `coordinator.py` / `entity.py` (100% / 98%) — but via observable behavior, not private-method assertions.
- Keep the `tests/zaptec/*` API-client tests running exactly as today (plain pytest, live constants call intact), per #257.
- Shipped test infra is **Linux-native**: it matches CI and the maintainers' devcontainer, and carries **no native-Windows accommodation in tracked files**.

## Non-goals (out of scope for this spec)

- The six platform files (`sensor/switch/number/button/binary_sensor/update`) — that is the #395 replacement, a separate PR.
- Config-flow / `__init__` coverage.
- Snapshot tests (deferred to the #395 replacement, where full-state snapshots pay off).
- Fixing bug #410 (this PR stays test-only; see "Bug #410" below).
- An offline constants snapshot. The shelved `fix/constants-snapshot-fixture` design existed only to dodge the socket block; Option C avoids the block entirely by never running those tests under the harness, so the snapshot is unnecessary.

## Approach (selected): Linux-native harness, scoped to the integration tests

Adopt pytest-hacc, patch the integration at the `Zaptec` client boundary, and assert on public state — but **scope the harness to the HA-integration tests only** ("Option C"), so `tests/zaptec/*` stays plain pytest.

The scoping mechanism is deliberately minimal and standard: on Linux, pytest-hacc autoloads via its normal `pytest11` entry point, so the HA-integration run needs **no conftest machinery** to activate it. The API-client run disables it with a single, per-invocation `-p no:homeassistant`. Two pytest invocations, nothing more.

Rejected alternatives:
- **(B) like-for-like fixture swap / (C-stay) hand-rolled mocks** — don't reach the target standard; keep the white-box coupling the review flagged.
- **Snapshot-under-harness** — runs `tests/zaptec/*` under the harness and dodges the socket block with a committed offline snapshot; keeps the two concerns interwoven (against #257) and adds a fixture to maintain.
- **Per-test socket opt-out** — empirically does not defeat pytest-hacc's `127.0.0.1` allow-list.
- **Committed native-Windows shim** — see "Why the shim is not committed" below.

## Design

### 1. Harness scope: two pytest invocations

The harness must be active for `tests/test_*.py` and inactive for `tests/zaptec/*`. Because pytest-hacc's plugin (and its socket block) is process-wide, this is a **per-process** choice — one pytest run cannot have the harness on for some tests and off for others. So the suite runs as **two invocations**:

```bash
# 1. HA-integration tests — harness autoloads (Linux pytest11 entry point)
pytest tests --ignore=tests/zaptec --cov=custom_components/zaptec --cov-branch

# 2. API-client tests — harness disabled → plain pytest → live constants works
pytest tests/zaptec -p no:homeassistant --cov=custom_components/zaptec --cov-branch --cov-append
```

- `-p no:homeassistant` disables pytest-hacc's autoloaded plugin for run 2 only, so there is no socket block and the live `api.zaptec.com/api/constants` call behaves exactly as today.
- `--cov-append` on run 2 merges the two runs' coverage into one report, preserving the combined `coordinator.py`/`entity.py` numbers.
- No global `-p no:homeassistant` and no root `conftest.py` `pytest_plugins` line — committing either would disable autoload for run 1 and defeat the harness. Scoping lives entirely in the two commands.

### 2. Test infrastructure (the foundation)

- **`requirements_test.txt`** — add `pytest-homeassistant-custom-component` pinned **per-Python via environment markers** (`==0.13.324` for `python_version >= "3.14"`, `==0.13.316` for `< "3.14"`). Do NOT add `homeassistant` here: it is already pinned in `requirements.txt`, and validate.yaml sed-reverts it to `2026.2.3` on the 3.13 leg. pytest-hacc pins an exact `homeassistant==`, so its version MUST match the HA of each Python leg — 0.13.324↔2026.4.3 (py≥3.14), 0.13.316↔2026.2.3 (py≥3.13). Leaving it unpinned makes pip backtrack to an ancient release (pytest 6.2.2 → crashes on 3.13); single-pinning the newest is uninstallable on 3.13.
- **`requirements.txt`** — relax the `pydantic` pin from an exact `==` to the manifest's supported range (`>=2.11.7,<2.14`) so pytest-hacc's transitive `pydantic==2.12.2` resolves alongside it. The devcontainer's `scripts/setup` installs both `requirements.txt` and `requirements_test.txt`, so they must co-resolve.
- **No committed root `conftest.py` for the harness.** On Linux (CI + devcontainer) the plugin autoloads; no shim, no `pytest_plugins`, no global `-p no:homeassistant`. (See "Why the shim is not committed.")
- **`tests/conftest.py`** — replace the hand-rolled `hass` / `FakeConfigEntry` with:
  - the harness's real `hass` fixture (available via autoload; no import needed in the conftest),
  - a **`mock_zaptec`** fixture: `MagicMock(spec=Zaptec)` pre-populated with a representative installation + charger object graph (and, because `Zaptec` is a `Mapping[str, ZaptecBase]`, implementing `__getitem__` / `__iter__` / `values()` to yield the fake `Charger` / `Installation` objects the platforms enumerate),
  - a **`mock_config_entry`** (`MockConfigEntry`) and a **`setup_integration(hass, mock_zaptec)`** helper that patches the client into the setup path and awaits `async_setup`.
  - the existing **`zaptec_constants`** fixture stays as-is (live call). It is only requested by `tests/zaptec/test_zconst.py` / `test_redact.py`, which run in invocation 2 (no harness → no socket block). It is never triggered in invocation 1 (that run `--ignore`s `tests/zaptec`), so it needs no socket guard. The event-loop save/restore added earlier stays (it protects the async fetch regardless of harness).

### 3. Why the shim is not committed (native-Windows is a local-only concern)

Home Assistant imports `fcntl` (Unix-only), so pytest-hacc's plugin cannot autoload on native Windows. Earlier iterations of this migration carried a `win32`-guarded shim (fcntl/resource/socketpair stubs) in a root `conftest.py`, plus a `pytest_plugins` line and a global `-p no:homeassistant`, purely so the maintainer's — and this assistant's — native-Windows environment could run the integration tests.

That machinery is **not committed**, for three reasons:

1. **It contradicts the maintainers' stated workflow.** They promote the devcontainer and have pushed back on native-Windows accommodation (steinmn on #398); a standalone committed Windows shim (PR #403) was already **closed**.
2. **It is the root cause of the scoping complexity.** The shim must run before pytest-hacc imports `fcntl`, which forces a root-conftest `pytest_plugins` + global `-p no:homeassistant` and thus a **session-wide** harness load — which is exactly what makes scoping `tests/zaptec/*` away from the harness hard. Dropping the shim lets Linux autoload the plugin, so scoping collapses to one per-invocation `-p no:homeassistant` (§1).
3. **CI and the devcontainer are both Linux**, so nothing shipped needs the shim.

**Local native-Windows runs** (this assistant's environment, and any contributor on native Windows) are handled outside tracked files:
- `tests/zaptec/*` already run natively today: `pytest tests/zaptec -p no:homeassistant` (this is the existing convention; the harness is off, `fcntl` is never imported).
- The **HA-integration tests** need Linux: run them in the **devcontainer** (what the maintainers promote), or, for a quick local check, under an **uncommitted, untracked** local shim conftest. Neither path ships.

### 4. The #394-replacement tests (coordinator + entity, behavior-first)

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

### 5. The mocked Zaptec client & shared test data

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

### 6. Bug #410 handling — test-only, deferred fix

Filed as custom-components/zaptec#410: `ZaptecBaseEntity` sets `_attr_available = False` on `KeyUnavailableError` but never overrides `available`. Contributor steinmn responded that this is **not a bug**, and the exact mechanism was confirmed against installed HA (2026.4.3): `ZaptecBaseEntity` subclasses `CoordinatorEntity`, whose `available` property is first in the MRO and returns `self.coordinator.last_update_success` — it never reads `_attr_available`. So a single missing key does not (and by design should not) take an entity `unavailable`; only a failed coordinator poll does. (steinmn's comment cited the *base* `Entity.available` reading `_attr_available`, but that base property is overridden by `CoordinatorEntity.available`, so it doesn't govern these entities — the conclusion holds via the override.) steinmn also noted the HA entity-vs-device distinction: one entity going unavailable would not make its charger/installation *device* unavailable.

**Status:** the reporter (rhammen) has concluded #410 is not a bug and intends to close it; it is currently still open. The earlier concern that `_attr_available` is "never reset to True on success" is also refuted: each derived platform's `_update_from_zaptec` sets it back to `True` on a good update (e.g. `binary_sensor.py:34`).

**Decision:** this PR stays **test-only** and asserts the **observed, correct** behavior (no xfail — the observed behavior is definitive regardless of the still-open semantic discussion): an entity with a single missing key stays available; an entity is `unavailable` only when the coordinator poll fails. (Reworked from the earlier xfail-encoded premise; see commit 0ecebab.)

### 7. CI + scripts wiring

- **`.github/workflows/validate.yaml`** — the test job installs `requirements.txt` + `requirements_test.txt` (co-resolving per §2) and runs the **two** pytest invocations (§1), preserving the existing 3.13 HA sed-revert. Coverage combines via `--cov-append`.
- **`scripts/test`** — mirror the two-invocation structure so local (Linux/devcontainer) runs match CI. Keep the `--skip-api` path (`SKIP_ZAPTEC_API_TEST=true`) working for invocation 2's login-gated tests.
- **`DEVELOPMENT.md`** — document the two-invocation split and that the HA-integration tests require Linux (devcontainer); `tests/zaptec/*` run natively with `-p no:homeassistant`.

## Success criteria (this PR)

- Coverage on `coordinator.py` / `entity.py` ≥ current (100% / 98%), achieved via behavior.
- `pytest tests --ignore=tests/zaptec` (harness) **and** `pytest tests/zaptec -p no:homeassistant` (plain) both green on Linux CI; combined coverage via `--cov-append`.
- `tests/zaptec/*` behavior unchanged — live constants call still runs, no socket block, no HA import.
- No native-Windows accommodation in tracked files (Linux-native infra).
- `ruff format` + `ruff check` clean.
- hassfest / HACS unaffected (`requirements_test.txt` is not shipped in the component; no root `conftest.py` / pytest-config changes are shipped for the harness).

## PR / branch strategy

- #394 and #395 held as draft (done 2026-07-25); reply posted on #394 explaining the direction.
- Migration lands on `test/ha-test-harness-migration` (already off `master`); it replaces #394. The #395 replacement is a **separate, later** PR reusing this infrastructure.
- Reference #257 in the PR body as the rationale for keeping `tests/zaptec/*` out of the harness. Note the upstream-PR-stack dependency (see [[upstream-pr-stack]]).

## Open items carried into planning

- Confirm the two-invocation coverage numbers combine correctly under `--cov-append` (branch coverage merges).
- Confirm `pyproject.toml`'s existing `[tool.pytest.ini_options]` has no global option that conflicts with the per-invocation `-p no:homeassistant` (e.g. no committed `addopts` that force-loads or force-disables the plugin).
- Confirm exact patch target (`custom_components.zaptec.Zaptec` import site) and the minimal `mock_zaptec` object-graph shape for #394.
