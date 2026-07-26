# HA Test-Harness Migration — Linux-native + Option C rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pivot the already-implemented pytest-hacc migration on branch `test/ha-test-harness-migration` from its committed native-Windows shim to **Linux-native** test infrastructure, and scope the harness to the HA-integration tests only (**Option C**, per issue #257) via two pytest invocations — so `tests/zaptec/*` (the future-standalone API client) run as plain pytest with their live constants call intact.

**This is a delta plan, not a from-scratch migration.** The branch (head `221406a`, off `master`) already contains the full harness migration: `requirements_test.txt` (per-Python pytest-hacc pins), the relaxed pydantic pin in `requirements.txt`, `tests/conftest.py` (mock_zaptec/setup_integration/zaptec_constants), and the behavior tests (`test_coordinator.py`, `test_entity.py` with correct #410 assertions, `test_init.py`, `test_diagnostics.py`). What this plan changes is **only** the harness activation mechanism and the test runners. Do NOT rewrite the tests or fixtures.

**Architecture:** On Linux (CI + devcontainer) pytest-hacc autoloads via its `pytest11` entry point, so activating the harness needs no conftest machinery. Remove the committed native-Windows shim (root `conftest.py`) and the global `-p no:homeassistant` (pyproject `addopts`). Run the suite as **two invocations**: `pytest tests --ignore=tests/zaptec` (harness autoloads; integration tests use mocked client, no network) and `pytest tests/zaptec -p no:homeassistant` (harness disabled → plain pytest → live `api.zaptec.com/api/constants` call works exactly as on `master`), combining coverage with `--cov-append`.

**Tech Stack:** Python 3.13/3.14 (CI matrix), Home Assistant 2026.4.3 (3.14) / 2026.2.3 (3.13 revert), `pytest-homeassistant-custom-component` (0.13.324 / 0.13.316 per-Python markers — already pinned, do not touch), pytest 9.x (pinned by pytest-hacc), `ruff` 0.15.22.

## Execution environment

**Run this plan inside the project's VS Code Dev Container (Linux).** That is where the harness autoloads natively, both invocations run, and the live constants call reaches the network. Ensure deps are installed first (`scripts/setup`, or `pip install -r requirements.txt -r requirements_test.txt`). Do **not** attempt the integration-test invocation on native Windows — that environment is intentionally no longer supported by tracked files (see spec §3). Commands below use plain `pytest` / `ruff` as available in the devcontainer.

## Global Constraints

- **Linux-native only.** No native-Windows accommodation may be (re)introduced into tracked files: no root `conftest.py` shim, no `pytest_plugins` force-load, no global `-p no:homeassistant`. Native-Windows local runs are handled outside the repo (uncommitted shim or devcontainer) and are out of scope here.
- **Two-invocation contract.** The harness must be active for `tests/test_*.py` and inactive for `tests/zaptec/*`. This is a per-process choice, so the suite always runs as the two invocations below. A bare `pytest` (which would collect `tests/zaptec/*` under the autoloaded harness and re-trip the socket block) is intentionally no longer the entry point.
  - Integration: `pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch`
  - API client: `pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append`
- **Do NOT modify** `requirements_test.txt` (per-Python pytest-hacc markers are correct), the `pydantic` range in `requirements.txt`, `tests/conftest.py` fixtures, or any `tests/test_*.py` / `tests/zaptec/test_*.py` content. This plan changes activation + runners only.
- No production code changes in `custom_components/**`. This branch is test/infra-only. Bug #410 is already handled (tests assert correct behavior, no xfail — do not reintroduce one).
- Ruff (format + check) must be clean on all changed files, pinned ruff `0.15.22`, scoped to the whole repo (`src: "."`).
- **Commit policy:** committing per task locally is pre-approved for this plan's execution (SDD auto-commit). **Pushing to any remote and opening/altering any PR requires explicit user approval** (project CLAUDE.md) — Task 5 stops for it.
- `[tool.pytest.ini_options]` also sets `pythonpath`, `testpaths=["tests"]`, `log_format`, `log_date_format`, `filterwarnings`, `asyncio_mode="auto"`, `asyncio_default_fixture_loop_scope="function"`. Preserve all of these; only the `addopts` line is removed.

---

### Task 1: Go Linux-native — remove the committed shim and global plugin-disable

**Files:**
- Delete: `conftest.py` (repo root)
- Modify: `pyproject.toml` (remove one line from `[tool.pytest.ini_options]`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: a repo where, on Linux, pytest-hacc autoloads for `pytest tests --ignore=tests/zaptec` and is disabled by `-p no:homeassistant` for `pytest tests/zaptec`. No tracked Windows shim remains.

- [ ] **Step 1: Delete the repo-root conftest (the Windows shim + explicit plugin load)**

Remove the file entirely:

```bash
git rm conftest.py
```

Rationale: on Linux the plugin autoloads; this file existed only to load it after a `win32` fcntl/resource/socketpair shim. It is the root cause of the session-wide harness load that Option C must avoid.

- [ ] **Step 2: Remove the global plugin-disable from pyproject.toml**

In `pyproject.toml`, inside `[tool.pytest.ini_options]`, delete exactly this line:

```toml
addopts = "-p no:homeassistant"
```

Leave every other key in that table unchanged (`pythonpath`, `testpaths`, `log_format`, `log_date_format`, `filterwarnings`, `asyncio_mode`, `asyncio_default_fixture_loop_scope`). Do not add a replacement `addopts`.

- [ ] **Step 3: Verify the integration invocation (harness autoloads)**

Run:
```bash
pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch
```
Expected: the real `hass` fixture works (harness autoloaded), and `test_coordinator.py`, `test_entity.py`, `test_init.py`, `test_diagnostics.py` all pass. If you see `fixture 'hass' not found` or `No module named 'pytest_homeassistant_custom_component'`, the harness didn't autoload — confirm `requirements_test.txt` is installed in this environment (`pip show pytest-homeassistant-custom-component`).

- [ ] **Step 4: Verify the API-client invocation (harness disabled, plain pytest)**

Run:
```bash
pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append
```
Expected: `test_zconst.py` / `test_redact.py` make the live `api.zaptec.com/api/constants` call and pass (devcontainer has network); `test_utils.py` / `test_validate.py` pass; `test_api.py` login tests behave exactly as on `master` (skipped without creds, or set `SKIP_ZAPTEC_API_TEST=true` to skip them). Crucially: **no `SocketBlockedError`** — `-p no:homeassistant` turned the harness off for this run. If you see `SocketBlockedError`, the harness is still active — confirm Step 2 removed the global `-p no:homeassistant` and that you passed `-p no:homeassistant` on this command.

- [ ] **Step 5: Confirm combined coverage did not regress**

Run:
```bash
coverage report --include="*/coordinator.py,*/entity.py"
```
Expected: `coordinator.py` ≥ 100%, `entity.py` ≥ 98% (the combined figure from Steps 3+4's `--cov-append`). If lower, do NOT add tests here — stop and report; a regression means the two-invocation split dropped coverage the single run had, which is a wiring problem to diagnose, not a test gap.

- [ ] **Step 6: Ruff + commit**

```bash
ruff format . --diff
ruff check
git add -A
git commit -m "test: drop committed Windows shim; rely on Linux pytest-hacc autoload"
```
(If `ruff format .` reports diffs, apply `ruff format .` and re-stage.)

---

### Task 2: Wire the two-invocation structure into CI (validate.yaml)

**Files:**
- Modify: `.github/workflows/validate.yaml` (the `tests` job's "Tests suite" step, ~lines 107-109)

**Interfaces:**
- Consumes: the Linux-native repo from Task 1.
- Produces: a CI `tests` job that runs both invocations with combined coverage, on both the 3.13 and 3.14 matrix legs.

- [ ] **Step 1: Replace the single test step with the two invocations**

In `.github/workflows/validate.yaml`, replace the existing step:

```yaml
      - name: Tests suite
        run: |
          pytest --cov=./custom_components/zaptec --cov-branch
```

with:

```yaml
      - name: Tests suite (HA integration — harness)
        run: |
          pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch

      - name: Tests suite (API client — plain pytest, no harness)
        run: |
          pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append
```

Leave the rest of the `tests` job untouched: the matrix (`["3.13", "3.14"]`), the 3.13 HA sed-revert, and the `pip install -r requirements.txt -r requirements_test.txt` step all stay.

- [ ] **Step 2: Sanity-check the YAML**

Run (in the devcontainer, if `python` + `pyyaml` are present):
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yaml')); print('yaml ok')"
```
Expected: `yaml ok`. (If pyyaml isn't available, visually confirm indentation matches the surrounding steps — two spaces under `steps:` items.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/validate.yaml
git commit -m "ci: run harness + API-client tests as two scoped pytest invocations"
```

---

### Task 3: Wire scripts/test to the two-invocation structure

**Files:**
- Modify: `scripts/test`

**Interfaces:**
- Consumes: the Linux-native repo from Task 1.
- Produces: a `./scripts/test` that mirrors CI (two invocations, combined coverage) and still supports `--skip-api` and the html/xml coverage reports.

- [ ] **Step 1: Update scripts/test**

Replace the single `pytest ...` line in `scripts/test` so the file reads:

```bash
#!/usr/bin/env bash

set -e

if [ "$1" == "--skip-api" ]; then
    export SKIP_ZAPTEC_API_TEST="true"
fi

# HA-integration tests run under the pytest-hacc harness (autoloads on Linux).
# API-client tests (tests/zaptec/*, the future-standalone client per #257) run
# as plain pytest with the harness disabled, so their live constants call is not
# socket-blocked. Coverage from both is combined via --cov-append.
# run tests with -s to display printouts and --log-cli-level to get logger output
pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch --log-cli-level=INFO -s
pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append --log-cli-level=INFO -s

# generate coverage report in html and xml
coverage html
coverage xml
```

Keep the file executable (`git` preserves the mode; if needed `chmod +x scripts/test`).

- [ ] **Step 2: Run it end-to-end**

Run:
```bash
./scripts/test --skip-api
```
Expected: both invocations run and pass (with API-login tests skipped), then `htmlcov/` and `coverage.xml` are generated. Confirm the terminal shows both invocations executing (two pytest runs), not one.

- [ ] **Step 3: Commit**

```bash
git add scripts/test
git commit -m "test: scripts/test runs harness + API-client invocations, combined coverage"
```

---

### Task 4: Document the split in DEVELOPMENT.md

**Files:**
- Modify: `DEVELOPMENT.md` (the "## Running tests" section, ~line 139)

**Interfaces:**
- Consumes: the runners from Tasks 2-3.
- Produces: contributor docs that explain the two-invocation split and why `tests/zaptec/*` are separate.

- [ ] **Step 1: Expand the "Running tests" section**

In `DEVELOPMENT.md`, under `## Running tests`, after the existing `./scripts/test` bullet, add an explanatory paragraph (adjust wording to match the file's voice):

```markdown
The suite runs as **two pytest invocations**, and `./scripts/test` runs both:

- **HA-integration tests** (`tests/test_*.py`) run under the
  `pytest-homeassistant-custom-component` harness, which autoloads on Linux.
  Run directly with:
  `pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch`
- **API-client tests** (`tests/zaptec/*`) test the vendored `zaptec/` client,
  which is destined to become a standalone PyPI library (issue #257) and has no
  Home Assistant dependency. They run as plain pytest with the harness disabled
  (the harness blocks non-localhost sockets, which would break their live
  `api.zaptec.com/api/constants` call):
  `pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append`

Because the harness (and its socket block) is process-wide, a bare `pytest`
is not the entry point — use `./scripts/test` or the two commands above. The
HA-integration tests require Linux; run them in the Dev Container (native
Windows is not supported for that half). `tests/zaptec/*` run anywhere.
```

- [ ] **Step 2: Commit**

```bash
git add DEVELOPMENT.md
git commit -m "docs: explain two-invocation test split (harness vs API-client, #257)"
```

---

### Task 5: Final gate, then push + verify CI on the fork (approval required)

**Files:**
- Verify only (no new edits unless a gate fails).

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a pushed, CI-green branch on the fork, ready for PR packaging (PR itself deferred to the user).

- [ ] **Step 1: Guard — nothing outside tests/zaptec triggers the live call**

Run:
```bash
grep -rln "zaptec_constants" tests --include="*.py"
```
Expected: only `tests/conftest.py`, `tests/zaptec/test_zconst.py`, `tests/zaptec/test_redact.py`. If any `tests/test_*.py` (integration) requests `zaptec_constants`, it would hit the live call under the harness in invocation 1 → stop and report (the fixture would need moving to `tests/zaptec/conftest.py`).

- [ ] **Step 2: Full local (devcontainer) gate**

Run both invocations fresh and the linters:
```bash
pytest tests --ignore=tests/zaptec --cov=./custom_components/zaptec --cov-branch
pytest tests/zaptec -p no:homeassistant --cov=./custom_components/zaptec --cov-branch --cov-append
coverage report --include="*/coordinator.py,*/entity.py"
ruff format . --diff
ruff check
```
Expected: both green; coordinator.py ≥ 100%, entity.py ≥ 98%; ruff clean.

- [ ] **Step 3: hassfest/HACS sanity (manual)**

Confirm no shipped-component files changed on this branch:
```bash
git diff --stat master -- custom_components/
```
Expected: empty (the migration is test/infra-only). `requirements_test.txt`, `pyproject.toml`, `scripts/test`, `.github/`, `DEVELOPMENT.md` are dev-only and not shipped in the component. (Use the `hassfest-hacs-check` skill for the checklist; note `requirements.txt`'s pydantic-range change is a range within `manifest.json`'s supported bounds, not a manifest change.)

- [ ] **Step 4: Push to the fork — STOP for explicit user approval first**

Do not run this until the user approves the push (Global Constraints):
```bash
git push origin test/ha-test-harness-migration
```
Then watch the fork's Actions run and confirm BOTH matrix legs (3.13 and 3.14) are green on both invocations. If a leg fails, diagnose against that leg's HA/pytest-hacc pairing (0.13.316↔2026.2.3 on 3.13; 0.13.324↔2026.4.3 on 3.14) before any further change.

- [ ] **Step 5: PR packaging — user-driven, not automatic**

Leave PR creation to the user. When they ask, the replacement PR targets `custom-components/zaptec:master`, supersedes draft #394, references #257 as the rationale for the `tests/zaptec/*` split, and notes the upstream-PR-stack dependency. No autonomous PR/issue/comment submission (AI-policy provisional compliance).

---

## Self-Review

**Spec coverage:**
- Linux-native infra, no committed shim (spec §3): Task 1 (delete root conftest + global `-p no:homeassistant`). ✓
- Two-invocation harness scoping (spec §1): Task 1 Steps 3-4 verify; Tasks 2-3 wire CI + scripts. ✓
- `tests/zaptec/*` unchanged, live call intact, per #257 (spec "two-audience", §2): Task 1 Step 4, Task 5 Step 1 guard. ✓
- Combined coverage via `--cov-append` (spec §1): Tasks 1-3, verified Task 1 Step 5 / Task 5 Step 2. ✓
- validate.yaml + scripts/test + DEVELOPMENT.md (spec §7): Tasks 2, 3, 4. ✓
- #410 already correct, no xfail (spec §6): Global Constraints forbid reintroducing one; no task touches the tests. ✓
- Success criteria (spec): both invocations green on Linux CI, no native-Windows in tracked files, ruff clean, hassfest/HACS unaffected — Task 5. ✓

**Placeholder scan:** No "TBD"/"handle later" — every step is a concrete file op, command, or exact snippet.

**Type/consistency:** The two invocation commands are byte-identical everywhere they appear (Global Constraints, Tasks 1-3, Task 5), so CI, `scripts/test`, and the docs cannot drift. `--cov-append` is present on the second invocation and absent on the first in every occurrence.

## Known risks carried into execution

1. **Coverage combine.** If `coverage report` after the split shows less than the pre-split single-run numbers, the cause is almost always a missing `--cov-append` on run 2 (erasing run 1's data) or a stray `.coverage` from a prior run — check both before treating it as a real coverage gap (Task 1 Step 5).
2. **Autoload assumption.** The whole design rests on pytest-hacc autoloading on Linux. If it does not (e.g. deps not installed), Task 1 Step 3 fails fast with a clear fixture/import error — install `requirements_test.txt` and retry; do not add back a conftest force-load.
3. **`test_api.py` without creds.** Behavior must match `master` (skip without creds). If it errors instead, that is pre-existing to how `tests/zaptec` runs on `master`, not introduced here — note it, don't fix it in this plan.
