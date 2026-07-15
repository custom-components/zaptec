# Validation Model Audit (upstream #359) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `custom_components/zaptec/zaptec/validate.py` so it only requires the API response fields `api.py` actually depends on, closing upstream [custom-components/zaptec#359](https://github.com/custom-components/zaptec/issues/359) ("Review all validation models and make sure we don't require something we don't use").

**Architecture:** `validate.py` defines pydantic models keyed by URL pattern; `Zaptec.request()` in `api.py` runs every successful response through `validate()` before returning it. #354/#357 already showed that requiring fields a User-role (non-Owner) account doesn't get back (e.g. `AuthenticationType`) turns a harmless reduced response into a hard integration failure. This plan re-derives, field by field, which fields `api.py` actually indexes without a safe default (`dict[...]` vs `dict.get(...)`) and relaxes everything else to optional — while *adding* validation for the one field that's currently indexed with **no** validation coverage at all (`Circuit.MaxCurrent`, a real crash-in-waiting) and fixing one field set that's stricter than the code's own defensive handling expects (`ChargerFirmware`'s optional-in-practice fields). Confirmed against the live Zaptec API docs (`docs.zaptec.com/reference/...`) via WebFetch on 2026-07-12, cross-checked against actual `api.py` indexing.

**Tech Stack:** Python 3.14, pydantic v2, pytest/pytest-asyncio, ruff.

**Governing rule for every field decision in this plan (apply consistently, don't re-litigate per field):**
- If `api.py` reads a field via hard subscript (`d["Key"]`, no default) → the model **must** require it (fixes crash risk).
- If `api.py` never reads a field via hard subscript (only through `set_attributes`/`ATTR_TYPES`, or not at all) → the model should **not** require it, regardless of what the official docs say is "required" — the docs have already proven unreliable for role-reduced responses (#357: `AuthenticationType` documented non-nullable, empirically absent for User-role accounts).

## Global Constraints

- Branch from the local fork's `master` (currently at `c8b6a1e`), **not** `upstream/master` — this repo's `master` already has upstream PRs #391 ("Add offline unit tests for the Zaptec API client"), #393 ("Retry transient server errors on setup", fixes #392), #394 ("coordinator/entity test coverage"), and #395 ("platform entity test coverage") merged in locally (as this fork's own PRs #2–#5), even though those four are still open against `custom-components/zaptec` upstream. Branching from `master` means this work builds on top of all four automatically.
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py tests/zaptec/test_api.py -v` per task; `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q` as the final full-suite check.
- Known, pre-existing, unrelated failures: `tests/zaptec/test_zconst.py` and `tests/zaptec/test_redact.py` always error in this dev environment (DNS-resolution gap documented in `CLAUDE.md`) — ignore those two files' failures, don't try to fix them here.
- Lint gate: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff` and `... -m ruff check custom_components/zaptec/zaptec/validate.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_validate.py tests/zaptec/test_api.py` must both be clean for touched files.
- Per this repo's `CLAUDE.md`: **never run `git commit` without explicit user approval.** Each task below ends with a "stage and propose commit" step, not an auto-commit — pause there and show the user the diff/message before committing.
- Out of scope (explicitly not touched by this plan, with rationale so it isn't re-litigated):
  - `InstallationConnectionDetails` (`messagingConnectionDetails`) — the endpoint is already marked deprecated in `api.py`'s own comment, all of its required fields (`Host`, `Password`, `Topic`, `Subscription`, `Username`) are hard-indexed in `stream_main()`, and there are no bug reports of it failing. No change needed.
  - `ChargerState`/`CHARGER_STATES` — already relaxed (only `StateId` required), and `state_to_attrs()` reads everything else via `.get()`. No change needed.
  - Adding role-based *blocking* of API calls (that's upstream #311, a separate, larger behavioral change — this plan only fixes validation strictness).
  - A `FullInstallation`/`LimitedInstallation` model split (raised as an idea in #359's issue body) — rejected in favor of simply making the role-dependent fields optional on one model, since `api.py` never branches its logic on which fields are present; a second model class would be an abstraction with no consumer, which the project's own coding conventions rule out (see `[[api-py-quality-plan]]`-style YAGNI guidance).

---

## File Structure

- Modify: `custom_components/zaptec/zaptec/validate.py` — relax `Installation`, `Installations`, `Charger`, `Circuit`, `Hierarchy`, `ChargerFirmware`; add new `HierarchyCharger` model; add `Circuit.MaxCurrent`.
- Modify: `custom_components/zaptec/zaptec/api.py` — harden `Installation.build()`'s hierarchy-parsing loop (`custom_components/zaptec/zaptec/api.py:272-297`) to tolerate a null `Chargers`/`Name` on a circuit, matching the newly-relaxed model.
- Test: `tests/zaptec/test_validate.py` — update `test_installation_validation`; add `test_charger_validation`, `test_hierarchy_validation`, `test_charger_firmware_validation`.
- Test: `tests/zaptec/test_api.py` — add one regression test for the `build()` hardening, reusing the existing `FakeSession`/`_make_zaptec` harness from PR #391.

---

### Task 1: Branch setup + relax `Installation`/`Installations` validation

**Files:**
- Modify: `custom_components/zaptec/zaptec/validate.py:14-30`
- Test: `tests/zaptec/test_validate.py` (modify `test_installation_validation`, starts at line 63 on `master`)

**Interfaces:**
- Produces: `Installation` model with only `Id: str` required; `Active`, `CurrentUserRoles`, `InstallationType`, `NetworkType` all `<type> | None = None`. `Installations.Pages` becomes `int | None = None`.

- [ ] **Step 1: Create the branch off the fork's `master`, without disturbing the in-progress work on the current branch**

```bash
git status --short
git stash push -u -m "wip: energy-statistics local files (unrelated to #359 audit)"
git checkout master
git pull origin master --ff-only
git checkout -b fix/issue-359-validation-audit
```

Expected: `git log --oneline -1` on the new branch shows `c8b6a1e Merge pull request #5 from rhammen/test/platform-entity-coverage` (or later, if `origin/master` has moved). When this workstream is done and you return to `feature/issue-300-energy-statistics`, remember to `git stash pop` there — it is *not* part of this plan.

- [ ] **Step 2: Update the existing installation test to reflect the new, relaxed requirements**

In `tests/zaptec/test_validate.py`, replace the whole `test_installation_validation` function with:

```python
def test_installation_validation() -> None:
    """Check validation of installation responses."""

    installation_list_url = "installation"
    single_installation_url = "installation/abcdef01-2345-6789-abcd-ef0123456789"

    valid_installation = {
        "Id": "abcdef01-2345-6789-abcd-ef0123456789",
        "InstallationType": 1,
        "MaxCurrent": 32.0,
        "Active": True,
        "NetworkType": 2,
        "CurrentUserRoles": 3,
        "AuthenticationType": 0,
    }
    validate(valid_installation, single_installation_url)

    valid_installation_list = {
        "Pages": 1,
        "Data": [valid_installation],
    }
    validate(valid_installation_list, installation_list_url)

    # check that any invalid object in the list of installations triggers validation fail
    invalid_installation_list = {
        "Pages": 1,
        "Data": [valid_installation, {}],
    }
    with pytest.raises(ValidationError):
        validate(invalid_installation_list, installation_list_url)

    # Users without the Owner/Service role get a reduced installation object
    # missing Active/CurrentUserRoles/InstallationType/NetworkType (see #357).
    # api.py only ever indexes Id directly, so this must still validate.
    limited_installation = {"Id": valid_installation["Id"]}
    validate(limited_installation, single_installation_url)

    limited_installation_list = {
        "Pages": 1,
        "Data": [limited_installation],
    }
    validate(limited_installation_list, installation_list_url)

    # Id is required: Zaptec.build() indexes inst_item["Id"] directly.
    invalid_installation = valid_installation.copy()
    invalid_installation.pop("Id")
    with pytest.raises(ValidationError):
        validate(invalid_installation, single_installation_url)

    invalid_installation_list2 = {
        "Pages": 1,
        "Data": [invalid_installation],
    }
    with pytest.raises(ValidationError):
        validate(invalid_installation_list2, installation_list_url)
```

This removes the old assertion that a missing `NetworkType` fails validation (that's now the whole point of the fix) and replaces it with an assertion that a `NetworkType`-less (and `Active`/`CurrentUserRoles`/`InstallationType`-less) installation validates successfully, while a still-missing `Id` continues to fail.

- [ ] **Step 3: Run the test to confirm it fails against the current (unrelaxed) model**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py::test_installation_validation -v`
Expected: FAIL — `validate(limited_installation, single_installation_url)` raises `ValidationError` because `Active`/`CurrentUserRoles`/`InstallationType`/`NetworkType` are still required on `master`.

- [ ] **Step 4: Relax the `Installation` and `Installations` models**

In `custom_components/zaptec/zaptec/validate.py`, replace lines 14-30:

```python
class Installation(BaseModel):
    """Pydantic model for a Zaptec installation."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Active: bool | None = None
    CurrentUserRoles: int | None = None
    InstallationType: int | None = None
    NetworkType: int | None = None


class Installations(BaseModel):
    """Pydantic model for a list of Zaptec installations."""

    model_config = ConfigDict(extra="allow")
    Data: list[Installation]
    Pages: int | None = None
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py::test_installation_validation -v`
Expected: PASS

- [ ] **Step 6: Stage the change and propose a commit (wait for approval before running `git commit`, per this repo's `CLAUDE.md`)**

```bash
git add custom_components/zaptec/zaptec/validate.py tests/zaptec/test_validate.py
```

Proposed message:
```
fix: relax Installation validation to fields api.py actually uses

Active, CurrentUserRoles, InstallationType and NetworkType are only
ever read through set_attributes()'s optional ATTR_TYPES conversion,
never hard-indexed. Requiring them meant a User-role-only account's
reduced installation object (already known to drop AuthenticationType,
see #357) would fail validation on whichever of these Zaptec's backend
also happens to omit for that role. Only Id is genuinely required --
it's the one field Zaptec.build() indexes directly.

Part of #359.
```

---

### Task 2: Split `Charger` from a new minimal `HierarchyCharger`; add `Circuit.MaxCurrent`; relax `Circuit`/`Hierarchy`

**Files:**
- Modify: `custom_components/zaptec/zaptec/validate.py:33-40` (Charger), `:59-75` (Circuit, Hierarchy)
- Test: `tests/zaptec/test_validate.py` (add `test_charger_validation`, `test_hierarchy_validation`)

**Interfaces:**
- Produces: `Charger` (used for `/chargers` and `/chargers/{id}`) with `Id: str` and `DeviceType: int` required, `Name`/`Active` optional. New `HierarchyCharger` model (`Id: str` only) used exclusively inside `Circuit.Chargers`. `Circuit` gains `MaxCurrent: float` (required) and relaxes `Name` to optional and `Chargers` to `list[HierarchyCharger] | None = None`. `Hierarchy` relaxes `Id`/`Name`/`NetworkType` to optional, keeps `Circuits: list[Circuit]` required.
- Consumes: Nothing from Task 1 except the same file.

**Why the split:** `custom_components/zaptec/zaptec/api.py:277` (`for charger_item in circuit["Chargers"]:`) only reads `charger_item["Id"]` at that point (`api.py:278`) — the rest of a charger's data is filled in later from the top-level `/chargers` list (`api.py:1302-1328`), which is validated separately by `Charger`/`Chargers`. Reusing the same `Charger` model (requiring `Name`, `Active`, `DeviceType`) for the hierarchy's embedded stub is validating data that was never guaranteed and is never read at that point — and per the live Zaptec docs (`api_installation_id_hierarchy_get.md`, checked 2026-07-12), the hierarchy's embedded charger's `Name`/`Active` are documented nullable while `DeviceType` there is documented required — a different contract than what our code actually needs at that call site.

- [ ] **Step 1: Write the failing tests**

Add to `tests/zaptec/test_validate.py`:

```python
def test_charger_validation() -> None:
    """Check validation of /chargers and /chargers/{id} responses."""

    chargers_list_url = "chargers"
    single_charger_url = "chargers/12345678-90ab-cdef-1234567890ab"

    valid_charger = {
        "Id": "12345678-90ab-cdef-1234567890ab",
        "Name": "Garage",
        "Active": True,
        "DeviceType": 4,
    }
    validate(valid_charger, single_charger_url)
    validate({"Pages": 1, "Data": [valid_charger]}, chargers_list_url)

    # Users without the Owner role get a reduced charger object missing
    # Name/Active; only Id and DeviceType are consumed directly by api.py.
    limited_charger = {"Id": valid_charger["Id"], "DeviceType": 4}
    validate(limited_charger, single_charger_url)
    validate({"Pages": 1, "Data": [limited_charger]}, chargers_list_url)

    # DeviceType is required: Zaptec.build() indexes chg["DeviceType"] on
    # every registered charger once merged from the /chargers list.
    missing_device_type = {"Id": valid_charger["Id"]}
    with pytest.raises(ValidationError):
        validate(missing_device_type, single_charger_url)

    # Id is required: Zaptec.build() indexes charger_item["Id"] directly.
    missing_id = {"DeviceType": 4}
    with pytest.raises(ValidationError):
        validate(missing_id, single_charger_url)


def test_hierarchy_validation() -> None:
    """Check validation of installation/{id}/hierarchy responses."""

    hierarchy_url = "installation/abcdef01-2345-6789-abcd-ef0123456789/hierarchy"

    valid_hierarchy = {
        "Id": "abcdef01-2345-6789-abcd-ef0123456789",
        "Name": "Main hierarchy",
        "NetworkType": 2,
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "Name": "Circuit 1",
                "MaxCurrent": 32.0,
                "Chargers": [
                    {"Id": "12345678-90ab-cdef-1234567890ab"},
                ],
            },
        ],
    }
    validate(valid_hierarchy, hierarchy_url)

    # Id/Name/NetworkType on the hierarchy itself aren't read by api.py, and
    # per the Zaptec API docs a circuit's Name/Chargers may be null -- all
    # of this must still validate.
    minimal_hierarchy = {
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "MaxCurrent": 32.0,
                "Chargers": None,
            },
        ],
    }
    validate(minimal_hierarchy, hierarchy_url)

    # MaxCurrent is required: Installation.build() indexes
    # circuit["MaxCurrent"] directly with no validation coverage today --
    # exactly the class of bug #359 asks to close.
    missing_max_current = {
        "Circuits": [{"Id": "11111111-1111-1111-1111-111111111111"}],
    }
    with pytest.raises(ValidationError):
        validate(missing_max_current, hierarchy_url)

    # A circuit's Id is required: Installation.build() indexes circuit["Id"].
    missing_circuit_id = {
        "Circuits": [{"MaxCurrent": 32.0}],
    }
    with pytest.raises(ValidationError):
        validate(missing_circuit_id, hierarchy_url)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py::test_charger_validation tests/zaptec/test_validate.py::test_hierarchy_validation -v`
Expected: FAIL — `limited_charger`/`minimal_hierarchy` raise `ValidationError` (fields still required), and `missing_max_current` does *not* raise (no `MaxCurrent` field exists on `Circuit` yet, so the extra key is silently ignored by `extra="allow"`).

- [ ] **Step 3: Implement the model changes**

In `custom_components/zaptec/zaptec/validate.py`, replace the `Charger` class (lines 33-40) with:

```python
class Charger(BaseModel):
    """Pydantic model for a Zaptec charger, as returned by /chargers and /chargers/{id}."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str | None = None
    Active: bool | None = None
    DeviceType: int


class HierarchyCharger(BaseModel):
    """Pydantic model for the minimal charger stub embedded in a hierarchy Circuit.

    This is a distinct, smaller shape than Charger: at parse time in
    Installation.build() only Id is read from it -- the rest of a charger's
    data is filled in later from the /chargers list response.
    """

    model_config = ConfigDict(extra="allow")
    Id: str
```

Then replace the `Circuit` and `Hierarchy` classes (originally lines 59-75) with:

```python
class Circuit(BaseModel):
    """Pydantic model for a Zaptec circuit."""

    model_config = ConfigDict(extra="allow")
    Id: str
    Name: str | None = None
    MaxCurrent: float
    Chargers: list[HierarchyCharger] | None = None


class Hierarchy(BaseModel):
    """Pydantic model for the hierarchy of Zaptec objects in an installation."""

    model_config = ConfigDict(extra="allow")
    Id: str | None = None
    Name: str | None = None
    NetworkType: int | None = None
    Circuits: list[Circuit]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py -v`
Expected: PASS (all tests in the file, including Task 1's)

- [ ] **Step 5: Stage the change and propose a commit (wait for approval)**

```bash
git add custom_components/zaptec/zaptec/validate.py tests/zaptec/test_validate.py
```

Proposed message:
```
fix: add missing Circuit.MaxCurrent validation, split hierarchy charger model

Circuit.MaxCurrent is hard-indexed in Installation.build()
(circuit["MaxCurrent"]) but was never declared on the Circuit model, so a
response missing it would pass validation and then crash with a raw
KeyError deep in build(). Also splits a minimal HierarchyCharger model
out of Charger for the hierarchy endpoint's embedded charger stubs,
since only Id is read there -- Name/Active/DeviceType requirements on
that path were validating data the code never uses at that point.

Part of #359.
```

---

### Task 3: Harden `Installation.build()` for a null `Circuit.Chargers`/`Name`

**Files:**
- Modify: `custom_components/zaptec/zaptec/api.py:277,284`
- Test: `tests/zaptec/test_api.py` (new test, append near the other `Installation`/hierarchy-adjacent tests)

**Interfaces:**
- Consumes: `Circuit.Chargers: list[HierarchyCharger] | None = None` and `Circuit.Name: str | None = None` from Task 2 — without this task, a real API response with `"Chargers": null` would now pass validation but then crash `build()` with `TypeError: 'NoneType' object is not iterable`, which is worse than today's behavior (a clean `ValidationError`). This task is what actually makes Task 2's relaxation safe.
- Uses existing test helpers `FakeResponse`, `_make_zaptec` from `tests/zaptec/test_api.py:66-155` (added in PR #391, no changes needed to them).

- [ ] **Step 1: Write the failing test**

Add to `tests/zaptec/test_api.py` (needs `from http import HTTPStatus` and `Installation`, already imported at the top of the file):

```python
@pytest.mark.asyncio
async def test_build_hierarchy_handles_null_circuit_chargers() -> None:
    """A circuit with a null Chargers list (nullable per the Zaptec API docs)
    must not crash Installation.build(); it should contribute no chargers
    rather than raising a TypeError."""

    hierarchy_payload = {
        "Circuits": [
            {
                "Id": "11111111-1111-1111-1111-111111111111",
                "MaxCurrent": 32.0,
                "Chargers": None,
            },
        ],
    }
    zap, _ = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=hierarchy_payload)])
    inst = Installation({"Id": "abcdef01-2345-6789-abcd-ef0123456789"}, zap)
    zap.register(inst.id, inst)

    await inst.build()

    assert inst.chargers == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py::test_build_hierarchy_handles_null_circuit_chargers -v`
Expected: FAIL — `TypeError: 'NoneType' object is not iterable` from `for charger_item in circuit["Chargers"]:`.

- [ ] **Step 3: Harden the loop**

In `custom_components/zaptec/zaptec/api.py`, in `Installation.build()`, change:

```python
            for charger_item in circuit["Chargers"]:
                chgid = charger_item["Id"]
                redact.add_uid(chgid, "Charger")

                # Inject additional attributes
                charger_item["InstallationId"] = self.id
                charger_item["CircuitId"] = ctid
                charger_item["CircuitName"] = circuit["Name"]
                charger_item["CircuitMaxCurrent"] = circuit["MaxCurrent"]
```

to:

```python
            # Chargers and Name are nullable per the Zaptec API docs.
            for charger_item in circuit.get("Chargers") or []:
                chgid = charger_item["Id"]
                redact.add_uid(chgid, "Charger")

                # Inject additional attributes
                charger_item["InstallationId"] = self.id
                charger_item["CircuitId"] = ctid
                charger_item["CircuitName"] = circuit.get("Name")
                charger_item["CircuitMaxCurrent"] = circuit["MaxCurrent"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Stage the change and propose a commit (wait for approval)**

```bash
git add custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
```

Proposed message:
```
fix: tolerate a null Chargers/Name on a hierarchy Circuit

Circuit.Chargers and Circuit.Name are documented nullable by the Zaptec
API and are now validated as optional (see the preceding validate.py
commit); Installation.build() was still hard-indexing both, which would
crash with a TypeError the moment either one is actually null instead of
absent.

Part of #359.
```

---

### Task 4: Relax `ChargerFirmware` validation to match its own defensive call site

**Files:**
- Modify: `custom_components/zaptec/zaptec/validate.py:78-87`
- Test: `tests/zaptec/test_validate.py` (add `test_charger_firmware_validation`)

**Interfaces:**
- Produces: `ChargerFirmware` with only `ChargerId: str` required; `DeviceType`, `IsOnline`, `CurrentVersion`, `AvailableVersion`, `IsUpToDate` all optional.

**Why:** `custom_components/zaptec/zaptec/api.py:326-338` (`Installation.poll_firmware_info()`) already contains defensive code — `if fm.get("CurrentVersion") is None or fm.get("AvailableVersion") is None or fm.get("IsUpToDate") is None:` — that logs "the charger hasn't been initialized yet, safe to ignore" and skips it. But `validate()` runs *before* that code, inside `Zaptec.request()`, and today's model requires all three as non-optional strict fields — so a not-yet-initialized charger's firmware response raises a `ValidationError` and that defensive branch can never actually run; the request fails hard instead of being safely skipped. The live Zaptec docs (`api_chargerfirmware_installation_installationid_get.md`, checked 2026-07-12) confirm `IsOnline`/`CurrentVersion`/`AvailableVersion`/`IsUpToDate` are all documented `nullable: true`; only `ChargerId` and `DeviceType` are non-nullable there. Since `DeviceType` also isn't read anywhere in `poll_firmware_info()`, it's relaxed too per this plan's governing rule, at no cost.

- [ ] **Step 1: Write the failing test**

Add to `tests/zaptec/test_validate.py`:

```python
def test_charger_firmware_validation() -> None:
    """Check validation of chargerFirmware/installation/{id} responses."""

    firmware_url = "chargerFirmware/installation/abcdef01-2345-6789-abcd-ef0123456789"

    valid_firmware = [
        {
            "ChargerId": "12345678-90ab-cdef-1234567890ab",
            "DeviceType": 4,
            "IsOnline": True,
            "CurrentVersion": "1.2.3",
            "AvailableVersion": "1.2.4",
            "IsUpToDate": False,
        },
    ]
    validate(valid_firmware, firmware_url)

    # A charger added to the platform but not yet initialized reports only
    # ChargerId, per api.py's poll_firmware_info(), which treats
    # CurrentVersion/AvailableVersion/IsUpToDate as optional and skips the
    # charger if any are missing. Per the Zaptec API docs, all fields
    # except ChargerId are nullable, so validation must not reject this
    # before that defensive code ever gets to run.
    uninitialized_firmware = [{"ChargerId": "12345678-90ab-cdef-1234567890ab"}]
    validate(uninitialized_firmware, firmware_url)

    # ChargerId is required: poll_firmware_info() indexes fm["ChargerId"] directly.
    missing_charger_id = [{"DeviceType": 4}]
    with pytest.raises(ValidationError):
        validate(missing_charger_id, firmware_url)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py::test_charger_firmware_validation -v`
Expected: FAIL — `uninitialized_firmware` raises `ValidationError` (still-required fields missing).

- [ ] **Step 3: Relax the model**

In `custom_components/zaptec/zaptec/validate.py`, replace the `ChargerFirmware` class (lines 78-87) with:

```python
class ChargerFirmware(BaseModel):
    """Pydantic model for the firmware information of a Zaptec charger."""

    model_config = ConfigDict(extra="allow")
    ChargerId: str
    DeviceType: int | None = None
    IsOnline: bool | None = None
    CurrentVersion: str | None = None
    AvailableVersion: str | None = None
    IsUpToDate: bool | None = None
```

- [ ] **Step 4: Run the full validate.py test file and the whole suite**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_validate.py -v`
Expected: PASS (all tests)

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: same pass/fail counts as on `master` plus the new tests passing, with failures confined to `tests/zaptec/test_zconst.py`/`tests/zaptec/test_redact.py` (the known DNS-fixture gap).

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`
Expected: no diff output for the touched files.

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/validate.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_validate.py tests/zaptec/test_api.py`
Expected: no new errors introduced (pre-existing `api.py` errors tracked in #258 are not this plan's concern).

- [ ] **Step 5: Stage the change and propose a commit (wait for approval)**

```bash
git add custom_components/zaptec/zaptec/validate.py tests/zaptec/test_validate.py
```

Proposed message:
```
fix: relax ChargerFirmware validation to match its own defensive call site

poll_firmware_info() already treats CurrentVersion/AvailableVersion/
IsUpToDate as optional (a charger added but not yet initialized omits
them) and skips the charger with a warning -- but validate() ran first
and rejected the response outright, so that defensive branch was
unreachable in practice. The Zaptec API docs confirm all of IsOnline/
CurrentVersion/AvailableVersion/IsUpToDate/DeviceType are nullable;
only ChargerId is genuinely required.

Fixes #359.
```

(This is the last task, so mark #359 "Fixes" here; the earlier three commits should say "Part of #359.")

---

## Self-Review

**Spec coverage against #359's stated asks:**
- "make sure we don't require something we don't use" → Tasks 1, 2, 4 (Installation, Charger/HierarchyCharger, ChargerFirmware all relaxed to match actual `api.py` usage).
- "check if there is anything we currently don't require in validation that should be added" → Task 2 (`Circuit.MaxCurrent`, previously undeclared and hard-indexed — the clearest concrete finding of this audit).
- "make sure to run a test with a User-role-only test-user before merging" → not automatable here (no live restricted-role credentials in this dev environment); flagged below as a manual follow-up rather than silently dropped.
- "consider distinguishing FullInstallation/LimitedInstallation" → explicitly addressed and declined in Global Constraints, with reasoning (no code branches on which fields are present, so a second model class is unused abstraction).

**Manual follow-up (not a task in this plan, needs a human with real Zaptec credentials):** before merging, if at all possible, run `SKIP_ZAPTEC_API_TEST=false` against a real User-role-only Zaptec account once, the way `steinmn` did in the #311 thread, to confirm live responses now pass. This plan's evidence is: the exact bug reports in #354/#357, the live API docs fetched 2026-07-12, and full-coverage code reading of every hard subscript in `api.py` — but it is not a substitute for one real end-to-end run against a restricted account.

**Placeholder scan:** none found — every step has literal code, exact file paths/line numbers, and runnable commands.

**Type consistency:** `HierarchyCharger` is introduced in Task 2 and consumed only within that same task's `Circuit.Chargers` field; no later task references it by a different name. `Circuit.MaxCurrent: float` matches the `float` cast already implicit in `circuit["MaxCurrent"]`'s use as `CircuitMaxCurrent` (consumed later via `ATTR_TYPES["circuit_max_current"] = float` in `api.py`).
