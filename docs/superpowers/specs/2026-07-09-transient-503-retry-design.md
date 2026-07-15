# Design: Retry transient server errors on setup (issue #392)

## Problem

On a fresh install (e.g. dev-container), adding a Zaptec account intermittently
fails setup with:

```
RequestError: POST request to https://api.zaptec.com/oauth/token failed with status 503
ConfigEntryError: ... 503 Service Temporarily Unavailable
```

Restarting Home Assistant fixes it. Root cause is two-fold:

1. **No retry on transient 5xx for the token request.** `_refresh_token`
   (`zaptec/api.py`) branches only on `200` (return) and `400` (auth error);
   every other status — including `503` — falls straight through to
   `raise RequestError(...)` on the *first* response. The retry loop in
   `_request_worker` only re-loops on `TimeoutError`/`ClientConnectionError`,
   never on HTTP status. So a transient `503` on the token POST is fatal.

2. **A transient error maps to a permanent setup failure.** In
   `__init__.py::async_setup_entry`, a `RequestError` (subclass of
   `ZaptecApiError`) becomes `ConfigEntryError`, which HA treats as
   *permanent* (no auto-retry). Only `RequestTimeoutError`/
   `RequestConnectionError` map to `ConfigEntryNotReady` (which auto-retries).
   That is why a full HA restart is needed to recover.

## Fix A — Retry transient HTTP statuses in `_request_worker`

- Add `RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})` to
  `zaptec/const.py` (Too Many Requests, Bad Gateway, Service Unavailable,
  Gateway Timeout).
- In `_request_worker`, after logging the response and before yielding to the
  caller: if `response.status in RETRYABLE_HTTP_STATUSES` **and** this is not
  the final iteration, log and `continue` (retry via the existing exponential
  backoff in the `finally` block). Honor a `Retry-After` header (seconds) when
  present, capped at `_max_time`; otherwise use the existing backoff.
- On the **final** iteration, fall through to yield as today, so the caller
  raises its normal `RequestError(status)` — terminal behavior unchanged.
- Living in the shared worker, this covers both `_refresh_token` (the token
  POST in the issue) and every `request()` call. The existing `500`-on-GET-only
  logic in `request()` is untouched (`500` is not in the set), so
  non-idempotent POST/PUT are never double-executed on a `500`.

## Fix B — Map exhausted transient errors to `ConfigEntryNotReady`

- `RequestError` already carries `error_code` (the HTTP status). In
  `async_setup_entry`, if a caught `RequestError.error_code in
  RETRYABLE_HTTP_STATUSES`, raise `ConfigEntryNotReady` (HA auto-retries setup)
  instead of `ConfigEntryError`. All other `ZaptecApiError`s keep mapping to
  `ConfigEntryError`.

## Testing (TDD, mocked aiohttp)

- `_refresh_token`: `503` then `200` → succeeds after one retry.
- `_refresh_token`: persistent `503` → raises `RequestError` with
  `error_code == 503` after `API_RETRIES` attempts.
- `request()`: `503` then `200` → retries and returns data.
- `500` on a POST is still **not** retried (regression guard).
- `Retry-After` header is honored for the next delay.
- `async_setup_entry`: `RequestError(503)` → `ConfigEntryNotReady`;
  `RequestError(403)` → `ConfigEntryError`.

Retry sleeps are neutralized in tests by patching `asyncio.sleep`.

## Scope

Two source files (`zaptec/const.py`, `zaptec/api.py`, `__init__.py`) plus new
tests. No behavior change for success paths or existing `500` handling.
