# Energy Statistics Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Energy Dashboard's misattributed-hour problem (upstream [issue #300](https://github.com/custom-components/zaptec/issues/300), see also #162/#103) by backdating each charger's hourly energy consumption directly into Home Assistant's long-term statistics store, instead of relying on the live, polling-delayed `Energy Meter` sensor.

**Architecture:** A new per-charger `ZaptecStatisticsCoordinator` (new file `custom_components/zaptec/statistics.py`) periodically fetches completed charge sessions from Zaptec's `GET /api/sessions/archived` endpoint (cursor-paginated, requires both a `From` and `To` bound filtering by session *end* time, includes a per-session `EnergyDetails` list of cumulative-energy/timestamp points), converts them into hourly deltas via a pure `bucket_sessions_hourly()` function, and writes them with `homeassistant.components.recorder.statistics.async_add_external_statistics()` under a stateless statistic id (`zaptec:energy_<chargerid>`) — mirroring the pattern used by HA core's Tibber integration (`homeassistant/components/tibber/coordinator.py::_insert_statistics`), which is the reference the issue itself points to. This entity never appears as a regular sensor; it only feeds the Energy Dashboard's statistics tables. Where to resume from is derived from the recorder's own `get_last_statistics()`, not separately-persisted state, so there's no new storage to keep in sync. The endpoint requires the **Owner role** on the queried charger, so the coordinator treats a 403 as a soft, logged skip rather than a hard failure — accounts using a non-Owner Zaptec user simply won't get this feature for that charger.

**Tech Stack:** `homeassistant.components.recorder.statistics` (`async_add_external_statistics`, `get_last_statistics`), `homeassistant.helpers.update_coordinator.DataUpdateCoordinator`, `pydantic` (existing `validate.py` convention), pytest/pytest-asyncio/unittest.mock (existing conventions in `tests/`). No new dependencies.

## Global Constraints

- **Branch:** `feature/issue-300-energy-statistics`, created from `master` (which already contains `test/platform-entity-coverage`, merged via PR #5) — already checked out, do not create another branch.
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Lint gate: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff` and `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components tests` must stay clean for every file this plan touches. The repo's ruff config uses `select = ["ALL"]`; run `ruff check` after writing each file and add `# noqa` comments to whatever it flags from actual output — don't pre-guess every one.
- Never commit without explicit user approval (per repo CLAUDE.md) — stop before each commit step and wait for approval.
- **Field casing — CONFIRMED via live test (2026-07-12):** every other Zaptec endpoint in this codebase returns PascalCase JSON (`Id`, `Name`, `DeviceType`...) even though `swagger.json` *declares* those same fields in camelCase — but `/api/sessions/archived` is the exception: a real account's response, captured via HA debug logging in the devcontainer, is genuinely **camelCase**, matching its swagger declaration for once: `{"sessions": [{"id": "b9b...", ...}], "cursor": null, "hasMore": false}` (confirmed top-level keys `sessions`/`cursor`/`hasMore` and the nested `id` key directly from a pydantic `ValidationError`'s `input_value`; the remaining nested field names — `chargerId`, `startDateTime`, `endDateTime`, `energy`, `energyDetails`, `timestamp`, `voided`, `aborted` — are inferred by extrapolation, since .NET's `System.Text.Json` camelCase policy is applied uniformly across a response, not selectively, but were not each individually observed in the captured log). All code and tests below use this confirmed camelCase shape. The request's *query parameters* (`ChargerId`, `PageSize`, `From`, `To`, `Cursor`) are unaffected and stay PascalCase as originally written — the live test's request succeeded (200 response), only the response-body validation failed, confirming query-parameter casing is a separate concern from JSON body casing for this API.
- Do not touch the existing `ZaptecEnengySensor`/`Energy Meter` sensor (`sensor.py`) or the `Session total charge` sensor — they stay as-is; this plan adds a parallel, invisible statistics feed, it doesn't replace the live sensors.
- Do not implement `/api/chargehistory` (the older, `PageIndex`-paginated endpoint) — `/api/sessions/archived` is the newer replacement with simpler cursor pagination, a larger page size (200 vs 100), and per-point cumulative energy always included (no `DetailLevel` flag needed). This isn't just a preference: Zaptec has an (undiscoverable-except-by-web-search, not linked from `llms.txt` or the docs SPA) deprecation notice at `docs.zaptec.com/page/archived-session-endpoints-and-legacy-session-deprecation-planned` stating legacy closed-session endpoints (including `chargehistory`) lose access to sessions older than 2 years on **2026-08-01** and are removed entirely on **2027-01-01** — `/api/sessions/archived` is explicitly named as the durable replacement.

---

## File Structure

- **Modify: `custom_components/zaptec/zaptec/api.py`** — `Zaptec.request()` gains an optional `params` kwarg; `Charger` gains `get_archived_sessions()`.
- **Modify: `custom_components/zaptec/zaptec/validate.py`** — new pydantic models for the `/api/sessions/archived` response.
- **Modify: `custom_components/zaptec/const.py`** — `ZAPTEC_STATISTICS_POLL_INTERVAL`, `ZAPTEC_STATISTICS_BACKFILL_DAYS`.
- **Create: `custom_components/zaptec/statistics.py`** — `bucket_sessions_hourly()` pure function and `ZaptecStatisticsCoordinator`.
- **Modify: `custom_components/zaptec/manager.py`** — `ZaptecManager` gains a `statistics_coordinators: dict[str, ZaptecStatisticsCoordinator]` attribute.
- **Modify: `custom_components/zaptec/__init__.py`** — create one `ZaptecStatisticsCoordinator` per tracked charger and run its first refresh during setup.
- **Modify: `README.md`** — update the known-issues note (lines 58-63) once the feature ships.
- **Modify: `tests/zaptec/test_api.py`** — tests for `params` passthrough and `get_archived_sessions()`.
- **Create: `tests/test_statistics.py`** — tests for `bucket_sessions_hourly()` and `ZaptecStatisticsCoordinator`.

---

### Task 1: `Zaptec.request()` query-parameter support

**Files:**
- Modify: `custom_components/zaptec/zaptec/api.py:1151-1165`
- Test: `tests/zaptec/test_api.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Zaptec.request(url, *, method="get", data=None, params: dict[str, Any] | None = None, base_url=API_URL) -> Any` — Task 3's `Charger.get_archived_sessions()` calls this with `params=`.

- [ ] **Step 1: Write the failing test**

Add to `tests/zaptec/test_api.py` (near the other `test_request_*` tests, e.g. after `test_request_ok_returns_json`):

```python
@pytest.mark.asyncio
async def test_request_passes_params_to_session() -> None:
    """Query params are forwarded to the underlying session.request() call."""
    payload = {"value": "answer"}
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=payload)])
    await zap.request("unregistered/url", params={"Foo": "bar", "PageSize": 200})
    assert session.calls[0][2]["params"] == {"Foo": "bar", "PageSize": 200}


@pytest.mark.asyncio
async def test_request_omits_params_when_not_given() -> None:
    """No params kwarg is passed to session.request() when none are given."""
    payload = {"value": "answer"}
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=payload)])
    await zap.request("unregistered/url")
    assert "params" not in session.calls[0][2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k params -v`
Expected: FAIL — `TypeError: Zaptec.request() got an unexpected keyword argument 'params'`

- [ ] **Step 3: Implement `params` support**

In `custom_components/zaptec/zaptec/api.py`, change:

```python
    async def request(
        self, url: str, *, method: str = "get", data: Any = None, base_url: str = API_URL
    ) -> Any:
        """Make a request to the API."""

        full_url = base_url + url
        kwargs = {
            "timeout": self._timeout,
            "headers": {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        }
        if data is not None:
            kwargs["json"] = data
```

to:

```python
    async def request(
        self,
        url: str,
        *,
        method: str = "get",
        data: Any = None,
        params: dict[str, Any] | None = None,
        base_url: str = API_URL,
    ) -> Any:
        """Make a request to the API."""

        full_url = base_url + url
        kwargs = {
            "timeout": self._timeout,
            "headers": {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        }
        if data is not None:
            kwargs["json"] = data
        if params is not None:
            kwargs["params"] = params
```

`aiohttp.ClientSession.request()` (invoked via `self._client.request(method=method, url=url, **kwargs)` inside `_request_worker`) natively accepts a `params` kwarg and appends it as a query string, so no other change to `_request_worker`/`request()` is needed. `validate(json_result, url=url)` continues to receive the path-only `url` (no query string), so existing `URLS` regex patterns in `validate.py` are unaffected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k params -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run full test suite and lint**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py`
Expected: both clean (fix any `noqa`-worthy findings from actual ruff output)

- [ ] **Step 6: Commit**

```bash
git add custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
git commit -m "feat: support query params in Zaptec.request()"
```

---

### Task 2: Validation models for `/api/sessions/archived`

**Files:**
- Modify: `custom_components/zaptec/zaptec/validate.py:104-127`

**Interfaces:**
- Consumes: nothing new (existing `BaseModel`, `ConfigDict`, `TypeAdapter` imports).
- Produces: registers `"sessions/archived"` in the `URLS` validation map (Task 3's `get_archived_sessions()` calls `zaptec.request("sessions/archived", ...)`, which routes through this map). No new names are exported for other tasks to consume — validation failures only log, per existing `validate()` behavior.

- [ ] **Step 1: Add the models**

In `custom_components/zaptec/zaptec/validate.py`, insert before the `CHARGER_FIRMWARES = TypeAdapter(...)` line (currently line 104):

```python
class ArchivedSession(BaseModel):
    """Pydantic model for a single archived (completed) charge session."""

    model_config = ConfigDict(extra="allow")
    id: str
    chargerId: str
    startDateTime: str


class GetArchivedSessionsResponse(BaseModel):
    """Pydantic model for a page of archived charge sessions."""

    model_config = ConfigDict(extra="allow")
    sessions: list[ArchivedSession]
    hasMore: bool
```

Then add to the `URLS` dict (after the `chargerFirmware/installation/[0-9a-f\-]+` entry, currently line 126):

```python
    "sessions/archived": GetArchivedSessionsResponse,
```

**Update (confirmed live, 2026-07-12):** unlike every other Zaptec endpoint in this codebase, `/api/sessions/archived` genuinely returns **camelCase** JSON — confirmed via a real account's response captured in HA debug logs (`{"sessions": [{"id": "b9b...", ...}], "cursor": null, "hasMore": false}`). Field names above (`id`/`chargerId`/`startDateTime`/`sessions`/`hasMore`) reflect this. `ArchivedSession` still intentionally omits `endDateTime`/`energy`/`energyDetails` as *required* fields (unlike `id`/`chargerId`/`startDateTime`) — `extra="allow"` passes them through unchecked, and `statistics.py` (Task 4) reads those via plain `dict.get()`, not through this model. Do **not** add a separate `ArchivedSessionEnergyPoint` model to type `energyDetails` — an earlier execution of this plan tried exactly that and a reviewer caught that a strict nested model can raise `ValidationError` for the *entire page* if a shape assumption is ever wrong; leave `energyDetails` unvalidated under `extra="allow"`.

- [ ] **Step 2: Run lint**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/validate.py`
Expected: clean

- [ ] **Step 3: Run full test suite**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: PASS (no behavior change yet, nothing calls this URL until Task 3)

- [ ] **Step 4: Commit**

```bash
git add custom_components/zaptec/zaptec/validate.py
git commit -m "feat: add validation model for sessions/archived endpoint"
```

---

### Task 3: `Charger.get_archived_sessions()`

**Files:**
- Modify: `custom_components/zaptec/zaptec/api.py:758` (insert after `authorize_charge`, before `set_permanent_cable_lock`)
- Modify: `custom_components/zaptec/zaptec/api.py:3` (add `datetime` import if not already present — it isn't; `api.py` currently has no `datetime` import)
- Test: `tests/zaptec/test_api.py`

**Interfaces:**
- Consumes: `Zaptec.request(url, *, params=...)` from Task 1.
- Produces: `Charger.get_archived_sessions(self, *, from_time: datetime, to_time: datetime, cursor: str | None = None, page_size: int = 200) -> TDict` returning the raw page dict (`{"sessions": [...], "cursor": ..., "hasMore": ...}` — confirmed camelCase, see Global Constraints) — consumed by Task 5's `ZaptecStatisticsCoordinator._async_update_data()`.

Per the endpoint's own docs (`https://docs.zaptec.com/reference/api_sessions_archived_get.md`, confirmed directly, not just inferred from swagger): **`From` and `To` are both required** ("Inclusive lower bound for session end time" / "Exclusive upper bound for session end time. Required, must be after `from`") — they filter by session **end** time, not start time. The endpoint also **requires the Owner role** on the queried charger/installation, so accounts using a non-Owner Zaptec user will get a 403 on every call — Task 5 must treat that as a soft failure (log + skip), not a hard `UpdateFailed`, or every non-Owner user's log will fill with coordinator errors every poll.

- [ ] **Step 1: Write the failing tests**

Add to `tests/zaptec/test_api.py`:

```python
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_get_archived_sessions_builds_params() -> None:
    """get_archived_sessions() sends ChargerId, PageSize, From, To and Cursor as query params."""
    payload = {"sessions": [], "cursor": None, "hasMore": False}
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=payload)])
    charger = Charger({"Id": "charger-1"}, zap, installation=None)

    result = await charger.get_archived_sessions(
        from_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        to_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
        cursor="abc",
    )

    assert result == payload
    method, url, kwargs = session.calls[0]
    assert method == "get"
    assert url == "https://api.zaptec.com/api/sessions/archived"
    assert kwargs["params"] == {
        "ChargerId": "charger-1",
        "PageSize": 200,
        "From": "2026-01-01T00:00:00+00:00",
        "To": "2026-01-02T00:00:00+00:00",
        "Cursor": "abc",
    }


@pytest.mark.asyncio
async def test_get_archived_sessions_omits_cursor_when_not_given() -> None:
    """cursor is omitted from params when not given; From/To are always sent."""
    payload = {"sessions": [], "cursor": None, "hasMore": False}
    zap, session = _make_zaptec([FakeResponse(HTTPStatus.OK, json_data=payload)])
    charger = Charger({"Id": "charger-1"}, zap, installation=None)

    await charger.get_archived_sessions(
        from_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        to_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert session.calls[0][2]["params"] == {
        "ChargerId": "charger-1",
        "PageSize": 200,
        "From": "2026-01-01T00:00:00+00:00",
        "To": "2026-01-02T00:00:00+00:00",
    }
```

Check what `API_URL` resolves to before trusting the exact URL assertion above:

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -c "from custom_components.zaptec.zaptec.const import API_URL; print(API_URL)"`

Adjust the `url ==` assertion in the test to match whatever this prints (it is expected to be `https://api.zaptec.com/api/`, giving `https://api.zaptec.com/api/sessions/archived` — fix the literal if the actual value differs).

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k archived_sessions -v`
Expected: FAIL — `AttributeError: 'Charger' object has no attribute 'get_archived_sessions'`

- [ ] **Step 3: Implement**

Add `from datetime import datetime` to the imports at the top of `custom_components/zaptec/zaptec/api.py` (alongside the existing `import time` etc., around line 14).

Insert into the `Charger` class, after `authorize_charge()` (ends at line 758) and before `set_permanent_cable_lock()`:

```python
    async def get_archived_sessions(
        self,
        *,
        from_time: datetime,
        to_time: datetime,
        cursor: str | None = None,
        page_size: int = 200,
    ) -> TDict:
        """Fetch one page of archived (completed) charge sessions for this charger.

        Wraps `GET /api/sessions/archived`, filtered to this charger and
        ordered oldest-first by the API. `from_time`/`to_time` are required by
        the endpoint and filter by session *end* time (not start time), so a
        long-running session only appears once it closes within the window.
        Returns the page as-is (`Sessions`, `Cursor`, `HasMore`); the caller
        follows `Cursor` while `HasMore` is true to page through the full
        result set.

        Requires the Owner role on this charger; raises `RequestError` with
        `error_code == HTTPStatus.FORBIDDEN` otherwise (see
        `ZaptecStatisticsCoordinator._async_update_data` in statistics.py for
        how that's handled).
        """
        params: TDict = {
            "ChargerId": self.id,
            "PageSize": page_size,
            "From": from_time.isoformat(),
            "To": to_time.isoformat(),
        }
        if cursor is not None:
            params["Cursor"] = cursor
        return await self.zaptec.request("sessions/archived", params=params)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k archived_sessions -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add the live-credential smoke test (manual, run once against a real account)**

Add to `tests/zaptec/test_api.py`, near the existing `test_api` function:

```python
@pytest.mark.asyncio
async def test_get_archived_sessions_live(zaptec_username: str, zaptec_password: str) -> None:
    """Smoke-test the real field casing of /api/sessions/archived.

    Skipped in CI and when SKIP_ZAPTEC_API_TEST=true, same as test_api above.
    Run this manually against a real account at least once: swagger.json
    *declares* this endpoint's fields in camelCase, but every other Zaptec
    endpoint actually returns PascalCase despite the same swagger mismatch
    (see validate.py's Charger/Installation models vs swagger's
    ChargerListModel). If this test fails after fixing obvious typos, the
    casing assumption in validate.py/statistics.py needs correcting. It will
    also surface a 403 here if the test account lacks the Owner role this
    endpoint requires - that's expected for non-Owner accounts, not a bug.
    """
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    async with Zaptec(zaptec_username, zaptec_password) as zaptec:
        await zaptec.login()
        await zaptec.build()
        chargers = list(zaptec.chargers)
        if not chargers:
            pytest.skip("Account has no chargers to test against")

        now = dt_util.utcnow()
        page = await chargers[0].get_archived_sessions(
            from_time=now - timedelta(days=730), to_time=now, page_size=5
        )
        assert "sessions" in page
        assert "hasMore" in page
        if page["sessions"]:
            session = page["sessions"][0]
            assert "id" in session
            assert "startDateTime" in session
            _LOGGER.info("Sample archived session: %s", session)
```

Run once with real credentials: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -k live -v -s` (requires `ZAPTEC_USERNAME`/`ZAPTEC_PASSWORD` env vars). Read the logged sample session and confirm the field names match what Task 2/4/5 assume before proceeding — this is the one step in this plan that cannot be verified automatically in this dev environment (see CLAUDE.md's note on the aiohttp async DNS-resolver gap, which affects live calls here even with credentials — this test may need to be run in the devcontainer instead).

- [ ] **Step 6: Run full test suite and lint**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
git commit -m "feat: add Charger.get_archived_sessions()"
```

---

### Task 4: `bucket_sessions_hourly()` pure function

**Files:**
- Create: `custom_components/zaptec/statistics.py`
- Test: `tests/test_statistics.py`

**Interfaces:**
- Consumes: `homeassistant.components.recorder.models.StatisticData` (HA core type, no local import needed beyond that).
- Produces: `bucket_sessions_hourly(sessions: list[dict[str, Any]], *, after: datetime | None, running_sum: float) -> list[StatisticData]` — consumed by Task 5's `ZaptecStatisticsCoordinator._async_update_data()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_statistics.py`:

```python
"""Tests for statistics.py."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.zaptec.statistics import bucket_sessions_hourly


def _session(
    session_id: str, points: list[tuple[str, float]], *, end: str | None = None, energy: float = 0.0
) -> dict:
    """Build a raw archived-session dict with the given energyDetails points."""
    return {
        "id": session_id,
        "endDateTime": end,
        "energy": energy,
        "energyDetails": [{"timestamp": ts, "energy": e} for ts, e in points],
    }


def test_single_session_within_one_hour() -> None:
    """A session with all points inside one hour produces a single bucket."""
    session = _session("s1", [("2026-01-01T10:10:00+00:00", 1.0), ("2026-01-01T10:40:00+00:00", 2.5)])

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert result[0]["state"] == 2.5
    assert result[0]["sum"] == 2.5


def test_session_spanning_two_hours_creates_two_buckets() -> None:
    """Points either side of an hour boundary land in different buckets."""
    session = _session(
        "s1",
        [
            ("2026-01-01T10:50:00+00:00", 1.0),
            ("2026-01-01T11:20:00+00:00", 1.6),
        ],
    )

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert [r["start"] for r in result] == [
        datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 11, tzinfo=timezone.utc),
    ]
    assert result[0]["state"] == 1.0
    assert result[0]["sum"] == 1.0
    assert result[1]["state"] == 0.6000000000000001  # noqa: PLR2004
    assert result[1]["sum"] == 1.6


def test_after_cutoff_excludes_already_imported_points() -> None:
    """Points at or before `after` are skipped, avoiding double-counting."""
    session = _session(
        "s1",
        [
            ("2026-01-01T10:10:00+00:00", 1.0),
            ("2026-01-01T11:10:00+00:00", 2.0),
        ],
    )
    cutoff = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    result = bucket_sessions_hourly([session], after=cutoff, running_sum=5.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 11, tzinfo=timezone.utc)
    assert result[0]["state"] == 1.0
    assert result[0]["sum"] == 6.0


def test_session_without_energy_details_falls_back_to_total() -> None:
    """A legacy session with no energyDetails uses its total energy at endDateTime."""
    session = {
        "id": "s1",
        "endDateTime": "2026-01-01T10:45:00+00:00",
        "energy": 3.0,
        "energyDetails": [],
    }

    result = bucket_sessions_hourly([session], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    assert result[0]["state"] == 3.0


def test_running_sum_carries_across_sessions() -> None:
    """The running sum accumulates across multiple sessions, oldest first."""
    session1 = _session("s1", [("2026-01-01T10:10:00+00:00", 1.0)])
    session2 = _session("s2", [("2026-01-01T12:10:00+00:00", 2.0)])

    result = bucket_sessions_hourly([session1, session2], after=None, running_sum=10.0)

    assert [r["sum"] for r in result] == [11.0, 13.0]


def test_voided_and_aborted_sessions_are_skipped() -> None:
    """Voided/aborted sessions have no meaningful energy and must not be counted."""
    voided = _session("s1", [("2026-01-01T10:10:00+00:00", 1.0)])
    voided["voided"] = True
    aborted = _session("s2", [("2026-01-01T11:10:00+00:00", 2.0)])
    aborted["aborted"] = True
    real = _session("s3", [("2026-01-01T12:10:00+00:00", 3.0)])

    result = bucket_sessions_hourly([voided, aborted, real], after=None, running_sum=0.0)

    assert len(result) == 1
    assert result[0]["start"] == datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    assert result[0]["state"] == 3.0
```

Note: `StatisticData` (`homeassistant.components.recorder.models`) is a `TypedDict`, i.e. a plain `dict` at runtime — access fields with `result[0]["start"]`, not `result[0].start`. The construction code in Step 3 below already gets this right (`StatisticData(start=hour, state=..., sum=...)` is a valid `TypedDict` constructor call that returns a real dict); only the test assertions needed the dict-key form. Do not introduce a wrapper dataclass to support attribute access — that would return an object `async_add_external_statistics()` (Task 5) doesn't actually expect.

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_statistics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zaptec.statistics'`

- [ ] **Step 3: Implement**

Create `custom_components/zaptec/statistics.py`:

```python
"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData
from homeassistant.util import dt as dt_util


def _floor_hour(value: datetime) -> datetime:
    """Floor a datetime down to the start of its hour, in UTC."""
    return dt_util.as_utc(value).replace(minute=0, second=0, microsecond=0)


def bucket_sessions_hourly(
    sessions: list[dict[str, Any]],
    *,
    after: datetime | None,
    running_sum: float,
) -> list[StatisticData]:
    """Convert archived charge sessions into hourly external-statistics points.

    Each session's `energyDetails` is a list of `{timestamp, energy}` points
    (camelCase - confirmed live, see Global Constraints) where `energy` is
    cumulative *within that session* (starts near 0). This turns those into
    per-hour consumption deltas, bucketed by the hour containing each point's
    timestamp. That's an approximation: a delta between two points less than
    an hour apart can span an hour boundary (Zaptec's default meter-reporting
    interval is 30 minutes), so a small amount of energy can be attributed to
    the following hour rather than split proportionally. This is still a
    large accuracy improvement over the live sensor, which can lag by 1+ hour
    (upstream issue #300).

    Sessions without `energyDetails` (e.g. pre-3.2 firmware) fall back to a
    single point at `endDateTime` using the session's total `energy`. Sessions
    marked `voided` or `aborted` are skipped entirely - per the API docs,
    "voided sessions have no meaningful duration or energy" (they exist when
    replaced by a corrected session, or when charging never actually started).

    `sessions` must be sorted oldest-first (the archived-sessions API
    guarantees this). `after` is the start of the last hour already imported
    by a previous run (its `sum` already reflects that whole hour) - so any
    point whose *floored hour* is at or before `after` is skipped, not just
    points with a raw timestamp at or before `after`. Skipping by raw
    timestamp would under-skip: a point at, say, 11:10 has a later raw
    timestamp than `after=11:00` and would slip through, getting re-added to
    an hour whose total was already stored - corrupting the external
    statistic with compounding phantom energy on every subsequent poll,
    since `/sessions/archived`'s `From` filter keeps returning the same
    session until a newer one supersedes it as the resume point. `running_sum`
    is the total energy (kWh) imported so far; the returned points chain onto
    it so the statistic's `sum` keeps increasing monotonically.
    """
    hourly_deltas: dict[datetime, float] = defaultdict(float)

    for session in sessions:
        if session.get("voided") or session.get("aborted"):
            continue

        details = session.get("energyDetails") or []
        if not details:
            end = session.get("endDateTime")
            energy = session.get("energy") or 0.0
            if end and energy:
                details = [{"timestamp": end, "energy": energy}]

        prev_energy = 0.0
        for point in details:
            timestamp = dt_util.parse_datetime(point["timestamp"])
            if timestamp is None:
                continue
            energy = point["energy"]
            delta = energy - prev_energy
            prev_energy = energy
            hour = _floor_hour(timestamp)
            if after is not None and hour <= after:
                continue
            hourly_deltas[hour] += delta

    statistics: list[StatisticData] = []
    for hour in sorted(hourly_deltas):
        running_sum += hourly_deltas[hour]
        statistics.append(StatisticData(start=hour, state=hourly_deltas[hour], sum=running_sum))
    return statistics
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_statistics.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full test suite and lint**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/statistics.py tests/test_statistics.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add custom_components/zaptec/statistics.py tests/test_statistics.py
git commit -m "feat: add hourly bucketing for archived charge sessions"
```

---

### Task 5: `ZaptecStatisticsCoordinator`

**Files:**
- Modify: `custom_components/zaptec/const.py` (append new constants)
- Modify: `custom_components/zaptec/statistics.py` (add the coordinator class)
- Test: `tests/test_statistics.py`

**Interfaces:**
- Consumes: `bucket_sessions_hourly()` (Task 4), `Charger.get_archived_sessions()` (Task 3), `Charger.id`/`.name`/`.qual_id` (existing `ZaptecBase` properties).
- Produces: `ZaptecStatisticsCoordinator(hass, *, entry: ZaptecConfigEntry, charger: Charger)`, a `DataUpdateCoordinator[None]` subclass with a public `.statistic_id: str` attribute — consumed by Task 6's `__init__.py`/`manager.py` wiring.

- [ ] **Step 1: Add constants**

Append to `custom_components/zaptec/const.py`:

```python
ZAPTEC_STATISTICS_POLL_INTERVAL = 60 * 60
"""Interval in seconds between imports of archived charge sessions into HA statistics."""

ZAPTEC_STATISTICS_BACKFILL_DAYS = 730
"""How far back (in days) to backfill energy statistics on first import."""
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_statistics.py`:

```python
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.zaptec.statistics import ZaptecStatisticsCoordinator
from custom_components.zaptec.zaptec import Charger


def _make_charger(charger_id: str = "charger-1") -> MagicMock:
    """A fake Charger exposing only what the coordinator touches."""
    charger = MagicMock(spec=Charger)
    charger.id = charger_id
    charger.name = "My Charger"
    charger.qual_id = f"Charger[{charger_id}]"
    return charger


@pytest.mark.asyncio
async def test_statistic_id_derived_from_charger_id(hass: MagicMock, config_entry: Any) -> None:
    """The statistic_id is stable and derived from the charger's id."""
    charger = _make_charger("abc-123")
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)
    assert coordinator.statistic_id == "zaptec:energy_abc123"


@pytest.mark.asyncio
async def test_first_run_backfills_from_zero_sum(hass: MagicMock, config_entry: Any) -> None:
    """With no prior statistics, the coordinator starts from sum=0 and pages through results."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={
            "sessions": [
                {
                    "id": "s1",
                    "energyDetails": [{"timestamp": "2026-01-01T10:10:00+00:00", "energy": 2.0}],
                }
            ],
            "cursor": None,
            "hasMore": False,
        }
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    assert mock_add.call_count == 1
    _hass_arg, metadata, statistics = mock_add.call_args[0]
    # StatisticMetaData and StatisticData are both TypedDicts (plain dicts at
    # runtime) - use dict-key access, not attribute access.
    assert metadata["statistic_id"] == "zaptec:energy_charger1"
    assert metadata["name"] == "My Charger Energy"
    assert len(statistics) == 1
    assert statistics[0]["sum"] == 2.0
    charger.get_archived_sessions.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_new_sessions_does_not_call_add_statistics(
    hass: MagicMock, config_entry: Any
) -> None:
    """If there's nothing new to import, async_add_external_statistics is not called."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={"sessions": [], "cursor": None, "hasMore": False}
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_resumes_from_last_statistics(hass: MagicMock, config_entry: Any) -> None:
    """A prior statistics entry sets the resume point and running sum."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        return_value={"sessions": [], "cursor": None, "hasMore": False}
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)
    last_start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch(
            "custom_components.zaptec.statistics.get_last_statistics",
            return_value={
                coordinator.statistic_id: [
                    {"start": last_start.timestamp(), "sum": 42.0}
                ]
            },
        ),
        patch("custom_components.zaptec.statistics.async_add_external_statistics"),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    call_kwargs = charger.get_archived_sessions.call_args.kwargs
    assert call_kwargs["from_time"] == last_start - timedelta(hours=26)
    # to_time is "now" at call time - assert it's recent rather than exact.
    assert (dt_util.utcnow() - call_kwargs["to_time"]) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_forbidden_error_is_logged_not_raised(hass: MagicMock, config_entry: Any) -> None:
    """A 403 (non-Owner account) is logged and skipped, not raised as UpdateFailed.

    /api/sessions/archived requires the Owner role - many Zaptec accounts
    won't have it on every charger, and that shouldn't repeatedly fail the
    coordinator/spam the log with UpdateFailed errors on every poll.
    """
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        side_effect=RequestError("forbidden", HTTPStatus.FORBIDDEN)
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
        patch("custom_components.zaptec.statistics.async_add_external_statistics") as mock_add,
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        await coordinator._async_update_data()  # noqa: SLF001

    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_other_request_errors_raise_update_failed(hass: MagicMock, config_entry: Any) -> None:
    """A non-403 RequestError still raises UpdateFailed, so HA surfaces it normally."""
    charger = _make_charger()
    charger.get_archived_sessions = AsyncMock(
        side_effect=RequestError("server error", HTTPStatus.INTERNAL_SERVER_ERROR)
    )
    coordinator = ZaptecStatisticsCoordinator(hass, entry=config_entry, charger=charger)

    with (
        patch("custom_components.zaptec.statistics.get_instance") as mock_get_instance,
        patch("custom_components.zaptec.statistics.get_last_statistics", return_value={}),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *args: func(*args)
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()  # noqa: SLF001
```

Add these imports to the test file: `from homeassistant.helpers.update_coordinator import UpdateFailed`, `from homeassistant.util import dt as dt_util`, `from http import HTTPStatus`, and `from custom_components.zaptec.zaptec.exceptions import RequestError`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_statistics.py -k Coordinator -v`
Expected: FAIL — `ImportError: cannot import name 'ZaptecStatisticsCoordinator'`

- [ ] **Step 4: Implement the coordinator**

`custom_components/zaptec/statistics.py` currently starts with (from Task 4):

```python
"""Import Zaptec charge history into Home Assistant's long-term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from homeassistant.components.recorder.models import StatisticData
from homeassistant.util import dt as dt_util


def _floor_hour(value: datetime) -> datetime:
```

Replace that header (everything from `from __future__ import annotations` down to, but not including, `def _floor_hour`) with:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN, ZAPTEC_STATISTICS_BACKFILL_DAYS, ZAPTEC_STATISTICS_POLL_INTERVAL
from .zaptec import Charger, RequestError, ZaptecApiError

if TYPE_CHECKING:
    from .manager import ZaptecConfigEntry

_LOGGER = logging.getLogger(__name__)

RESUME_MARGIN = timedelta(hours=26)
"""Extra lookback window (beyond the last imported hour) when querying the API.

/api/sessions/archived filters by session *end* time, not start time,
so a session that closes slightly before our nominal `From` cutoff - but
still has energy points after it - would otherwise be missed. Correctness is
enforced by bucket_sessions_hourly()'s own `after` filter regardless; this is
purely a fetch-window optimization to avoid re-scanning all of history."""


def _floor_hour(value: datetime) -> datetime:
```

`_floor_hour()` and `bucket_sessions_hourly()` themselves (the rest of the file from Task 4) are unchanged — this only replaces the module header above them. Then append the new class at the end of the file, after `bucket_sessions_hourly()`:

```python
class ZaptecStatisticsCoordinator(DataUpdateCoordinator[None]):
    """Coordinator that imports one charger's archived sessions into HA statistics.

    Runs independently of the live-state coordinators in coordinator.py: it
    backdates hourly energy consumption from `/api/sessions/archived` into
    HA's external-statistics store, fixing the Energy Dashboard's
    misattributed-hour problem (upstream issue #300) caused by the live
    Energy Meter sensor's polling cadence.
    """

    config_entry: ZaptecConfigEntry

    def __init__(
        self, hass: HomeAssistant, *, entry: ZaptecConfigEntry, charger: Charger
    ) -> None:
        """Initialize the statistics coordinator for one charger."""
        self.charger = charger
        self.statistic_id = f"{DOMAIN}:energy_{charger.id.replace('-', '')}"
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}-statistics-{charger.qual_id}",
            update_interval=timedelta(seconds=ZAPTEC_STATISTICS_POLL_INTERVAL),
        )

    async def _async_update_data(self) -> None:
        """Fetch new archived sessions and import them as external statistics."""
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, self.statistic_id, True, {"sum"}
        )
        if last_stats:
            last = last_stats[self.statistic_id][0]
            last_start = dt_util.utc_from_timestamp(last["start"])
            running_sum = last["sum"] or 0.0
        else:
            last_start = dt_util.utcnow() - timedelta(days=ZAPTEC_STATISTICS_BACKFILL_DAYS)
            running_sum = 0.0

        sessions: list[dict[str, Any]] = []
        cursor: str | None = None
        try:
            while True:
                page = await self.charger.get_archived_sessions(
                    from_time=last_start - RESUME_MARGIN,
                    to_time=dt_util.utcnow(),
                    cursor=cursor,
                )
                sessions.extend(page.get("sessions") or [])
                if not page.get("hasMore"):
                    break
                cursor = page.get("cursor")
        except RequestError as err:
            if err.error_code == HTTPStatus.FORBIDDEN:
                # /api/sessions/archived requires the Owner role. Many Zaptec
                # accounts won't have it on every charger - log once per poll
                # rather than failing the coordinator every hour.
                _LOGGER.warning(
                    "No permission to read charge history for %s (requires Owner role), "
                    "skipping energy statistics import",
                    self.charger.qual_id,
                )
                return
            raise UpdateFailed(err) from err
        except ZaptecApiError as err:
            raise UpdateFailed(err) from err

        statistics = bucket_sessions_hourly(sessions, after=last_start, running_sum=running_sum)
        if not statistics:
            return

        metadata_kwargs: dict[str, Any] = {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": f"{self.charger.name} Energy",
            "source": DOMAIN,
            "statistic_id": self.statistic_id,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        }
        if _SUPPORTS_UNIT_CLASS:
            metadata_kwargs["unit_class"] = EnergyConverter.UNIT_CLASS
        metadata = StatisticMetaData(**metadata_kwargs)
        async_add_external_statistics(self.hass, metadata, statistics)
```

**Update (found live, 2026-07-12):** `unit_class` on `StatisticMetaData`/`StatisticsMeta` is a recent HA core addition — optional-but-recommended as of HA 2026.4.3 (a `report_usage()` deprecation warning if omitted, becoming a hard requirement in HA 2026.11 per that warning's `breaks_in_ha_version`), but genuinely absent as a `StatisticsMeta` column in older HA (confirmed live against a real HA 2025.10.2 devcontainer: passing `unit_class` raises `TypeError: 'unit_class' is an invalid keyword argument for StatisticsMeta`, a hard crash — not a warning — since that HA version's ORM class doesn't have the column at all). Since this integration doesn't pin a minimum HA core version in `manifest.json`, it must support both. Add this module-level feature-detection flag near the top of `statistics.py` (after the imports, before `_floor_hour`), rather than comparing version strings (fragile - the exact version boundary isn't precisely known, feature-detection is robust regardless):

```python
from homeassistant.components.recorder.db_schema import StatisticsMeta

_SUPPORTS_UNIT_CLASS = hasattr(StatisticsMeta, "unit_class")
"""Whether the installed HA core's StatisticsMeta accepts unit_class - absent
on HA < ~2026, a required kwarg (with a deprecation warning if omitted) as of
2026.4.3, moving to a hard requirement in HA 2026.11. Feature-detected rather
than version-compared since the exact introduction version isn't pinned down."""
```

Add `from homeassistant.components.recorder.db_schema import StatisticsMeta` to the imports block above (Task 5 Step 4's header replacement) alongside the existing `homeassistant.components.recorder` imports.

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_statistics.py -v`
Expected: PASS (all tests in the file, 13 total: 7 from Task 4 + 6 coordinator tests from this task)

- [ ] **Step 6: Run full test suite and lint**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/statistics.py custom_components/zaptec/const.py tests/test_statistics.py`
Expected: both clean

- [ ] **Step 7: Commit**

```bash
git add custom_components/zaptec/statistics.py custom_components/zaptec/const.py tests/test_statistics.py
git commit -m "feat: add ZaptecStatisticsCoordinator for energy statistics import"
```

---

### Task 6: Wire into manager/setup, update README

**Files:**
- Modify: `custom_components/zaptec/manager.py:53-54` (attribute declaration), `:74` (`__init__`)
- Modify: `custom_components/zaptec/__init__.py:25` (import), `:185-189` (after device coordinators loop)
- Modify: `README.md:58-63`
- Create: `tests/test_manager.py` (there is no existing test file for `manager.py`; every other module in `custom_components/zaptec/` has a matching `tests/test_*.py`, so this follows that convention)

**Interfaces:**
- Consumes: `ZaptecStatisticsCoordinator` (Task 5).
- Produces: `ZaptecManager.statistics_coordinators: dict[str, ZaptecStatisticsCoordinator]`, populated and first-refreshed during `async_setup_entry`. Nothing downstream depends on this beyond HA's own coordinator lifecycle.

- [ ] **Step 1: Add the attribute to `ZaptecManager`**

In `custom_components/zaptec/manager.py`, add the import:

```python
from .statistics import ZaptecStatisticsCoordinator
```

next to the existing `from .coordinator import ZaptecUpdateCoordinator` line. Add the class attribute declaration after `device_coordinators` (currently lines 53-54):

```python
    statistics_coordinators: dict[str, ZaptecStatisticsCoordinator]
    """Coordinators that backdate hourly energy statistics, one per tracked charger."""
```

and initialize it in `__init__` next to `self.device_coordinators = {}` (currently line 74):

```python
        self.statistics_coordinators = {}
```

- [ ] **Step 2: Wire creation and first-refresh into `async_setup_entry`**

In `custom_components/zaptec/__init__.py`, add to the import block (near line 25-27):

```python
from .statistics import ZaptecStatisticsCoordinator
```

After the existing "Setup the device coordinators for each tracked device" loop (ends at line 185) and before the "Initialize the coordinators" comment (line 187), insert:

```python
    # Setup the statistics coordinators, one per tracked charger, to backdate
    # hourly energy consumption into HA's Energy Dashboard statistics (fixes
    # the live sensor's misattributed-hour delay, see issue #300).
    for deviceid in tracked_devices:
        zaptec_obj = zaptec[deviceid]
        if isinstance(zaptec_obj, Charger):
            manager.statistics_coordinators[deviceid] = ZaptecStatisticsCoordinator(
                hass, entry=entry, charger=zaptec_obj
            )
```

`Charger` is already imported in `__init__.py` (line 31, `from .zaptec import (..., Installation, ...)` — check the exact import list and add `Charger` alongside `Installation` if it isn't already there; the `isinstance(zaptec_obj, Installation)` check earlier in the same loop above confirms `Installation` is imported, `Charger` needs the same treatment).

Then, separately from the existing "Initialize the coordinators" loop (currently lines 187-189, which is left untouched), add:

```python
    # First-refresh the statistics coordinators with async_refresh() (not
    # async_config_entry_first_refresh()) - the latter turns any failure into
    # ConfigEntryNotReady, aborting setup of the *entire* integration (every
    # sensor/switch/button) over a hiccup on this secondary, Owner-only
    # endpoint. async_refresh() logs and marks the coordinator unavailable
    # instead, so a charge-history outage only degrades the Energy Dashboard
    # feed, not the whole config entry.
    for co in manager.statistics_coordinators.values():
        await co.async_refresh()
```

Do not add this second loop's body to the existing `all_coordinators` loop above it, and do not change `async_config_entry_first_refresh()` to `async_refresh()` for anything in `all_coordinators` — this distinction is deliberate and specific to the statistics coordinators.

- [ ] **Step 3: Write the test for the new attribute**

Note on scope: `async_setup_entry()` in `__init__.py` (where the actual per-charger creation loop from Step 2 lives) has **no existing unit test coverage** in this repo — `tests/test_init.py` only tests the pure `_config_entry_error()` helper, because exercising `async_setup_entry` needs a real `hass.config_entries`/`ConfigEntry` that (per `conftest.py`'s `FakeConfigEntry` docstring) "pulls in HA's full test-harness machinery, which cannot run on native Windows in this dev environment." This plan doesn't change that boundary. What *is* directly unit-testable is `ZaptecManager.__init__` itself, which is plain Python with no HA config-entry machinery involved.

Create `tests/test_manager.py`:

```python
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
```

The per-tracked-charger creation loop added to `async_setup_entry()` in Step 2 is validated instead by manual testing (see Step 6) — flag this to the user as a known coverage gap inherited from the existing test suite boundary, not one this plan introduces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: PASS, no regressions

- [ ] **Step 5: Update README known-issues note**

In `README.md`, replace lines 58-63:

```markdown
* Using the _Energy Meter_ entity as an input to the Energy Dashboard will give values that are delayed by 1 hour
  in the graphs (see [issue 162](https://github.com/custom-components/zaptec/issues/162) for details).
  There is a plan to solve this in [issue 300](https://github.com/custom-components/zaptec/issues/300), but until that is implemented,
  a workaround is to use the more frequently updated _Session total charge_ entity instead. This reduces the delay-issue,
  but has a separate drawback where a restart of Home Assistant during a charging session can give a fake spike in the logged
  consumption that needs to be manually edited using "Adjust sum" in the Statistics tab of the Developer tools dashboard.
```

with:

```markdown
* Using the _Energy Meter_ entity directly as an input to the Energy Dashboard will still give values delayed by up to an hour
  in the graphs, since that entity reflects live (polling-delayed) state (see [issue 162](https://github.com/custom-components/zaptec/issues/162)).
  Each tracked charger now also gets a separate, invisible statistics feed (backdated hourly from Zaptec's charge history,
  see [issue 300](https://github.com/custom-components/zaptec/issues/300)) that appears in the Energy Dashboard's device picker
  as "<charger name> Energy" - use that entry instead of _Energy Meter_ or _Session total charge_ for accurate,
  correctly-timed consumption graphs.
```

- [ ] **Step 6: Final full verification**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest --cov=./custom_components/zaptec --cov-branch tests`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`
Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components tests`
Expected: tests pass (aside from the known `test_zconst.py`/`test_redact.py` DNS gap per CLAUDE.md), format diff empty, no new ruff errors beyond the pre-existing ~76-error baseline in `zaptec/api.py`

Then manually verify the actual `async_setup_entry()` wiring (Step 2), since it has no automated coverage: run Home Assistant in the devcontainer against a real account (F1 → "Tasks: Run Task" → "Run Home Assistant on port 8123", per `DEVELOPMENT.md`), check the HA log for one `zaptec-statistics-Charger[...]` coordinator refresh per configured charger, then check Developer Tools → Statistics for a new `<charger name> Energy` entry with populated hourly values.

- [ ] **Step 7: Commit**

```bash
git add custom_components/zaptec/manager.py custom_components/zaptec/__init__.py README.md tests/test_manager.py
git commit -m "feat: wire up ZaptecStatisticsCoordinator per tracked charger"
```

---

## Follow-up (not in this plan)

- Once merged and confirmed working against a real account (Task 3 Step 5's live smoke test, plus watching a real Energy Dashboard for a day), consider filing a small doc PR against upstream to close #300, referencing this implementation.
- If `bucket_sessions_hourly()`'s hour-boundary approximation (Task 4) turns out to matter in practice (e.g. long sessions with sparse detail points), a follow-up could split deltas proportionally by time overlap instead of assigning them wholly to the later hour — deliberately deferred as YAGNI for v1.
