# Stream reconnect on transient connection failure — design

**Issue:** [custom-components/zaptec#417](https://github.com/custom-components/zaptec/issues/417)

## Problem

`Installation.stream_main()` ([zaptec/api.py](../../../custom_components/zaptec/zaptec/api.py))
opens a persistent Azure Service Bus (AMQP) connection for live push updates.
On any exception — including a transient connection failure such as
`azure.servicebus.exceptions.ServiceBusConnectionError` — it catches the
exception with a blanket `except Exception: _LOGGER.exception("Stream
failed")` and returns normally.

`ZaptecManager.create_streams()` ([manager.py](../../../custom_components/zaptec/manager.py))
starts `stream_main()` exactly once per installation, as a background task,
during `async_setup_entry`. Nothing supervises that task or restarts it if it
exits. So a one-off network blip (e.g. a home router reboot) permanently
kills the live stream until the user reloads the integration or restarts
Home Assistant — a silent, indefinite degradation to poll-interval-only
freshness.

## Goals

- A transient stream failure (connection error, AMQP error, etc.) is
  retried automatically with backoff, indefinitely — the stream should
  self-heal without user intervention.
- A permanent condition (no permission to the stream, HTTP 403) is *not*
  retried — retrying forever would just be log/network noise for a
  situation retrying can't fix.
- `cancel_streams()` (integration unload/reload) must still cleanly stop
  the stream, including while a reconnect backoff is in progress.
- Reconnect activity is logged just enough to diagnose an outage, without
  spamming a stack trace on every backoff cycle during a prolonged outage.

## Non-goals

- Changing polling-coordinator behavior (already handles its own
  independent retry — see #393).
- Changing the fallback-poll cadence while the stream is down (tracked
  separately, see the "stream reconciliation gap" note referenced from
  issue #378 discussion — out of scope here).
- `Installation.stream()` (a second, currently-unused convenience entry
  point that wraps `stream_main()` in its own bare `asyncio.create_task`)
  is not otherwise used by production code (only `manager.create_streams()`
  is). Its behavior changes as a side effect of `stream_main()` no longer
  swallowing exceptions (see below), but no new supervision logic is added
  there — it isn't called from anywhere in this integration today.

## Design

### `stream_main()` contract change

Remove the outer `except Exception: _LOGGER.exception("Stream failed")`
that currently wraps the whole connect-and-consume body. After the change:

- **Returns normally** → permanent stop. Currently this is only the
  403/Forbidden case when fetching stream connection details (already
  handled today by logging a warning and `return`-ing early). No other
  code path returns normally after this change — reaching the end of the
  `async for msg in receiver:` loop only happens when the receiver itself
  ends the iteration, which in practice means the connection is closing.
- **Raises an exception** → transient failure. The exception propagates to
  whoever awaited `stream_main()`.
- **`asyncio.CancelledError`** → not caught by `except Exception` (it is a
  `BaseException`, not `Exception`, on the Python versions this integration
  targets), so it is unaffected by this change and continues to propagate
  straight through, as it does today.

The `finally` block (clearing `_stream_receiver`, `_stream_running`, and
logging "Servicebus stream stopped for %s") is unchanged — it still runs
on every exit path.

### Supervising wrapper

New coroutine `ZaptecManager._stream_supervisor(install: Installation)` in
`manager.py`, used as the task body in `create_streams()` in place of the
direct `install.stream_main(...)` call:

```python
async def _stream_supervisor(self, install: Installation) -> None:
    delay = STREAM_RECONNECT_INIT_DELAY
    connected_at: float | None = None
    warned = False
    while True:
        connected_at = time.monotonic()
        try:
            await install.stream_main(cb=self.stream_callback, ssl_context=get_default_context())
            return  # permanent stop (e.g. 403)
        except Exception:
            if time.monotonic() - connected_at >= STREAM_RECONNECT_MAX_DELAY:
                delay = STREAM_RECONNECT_INIT_DELAY  # reset after a long-lived connection
            if not warned:
                _LOGGER.warning(
                    "Stream for %s disconnected, reconnecting", install.qual_id, exc_info=True
                )
                warned = True
            else:
                _LOGGER.debug("Stream for %s still reconnecting", install.qual_id, exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * STREAM_RECONNECT_FACTOR, STREAM_RECONNECT_MAX_DELAY)
            delay = random.normalvariate(delay, delay * STREAM_RECONNECT_JITTER)
```

(Illustrative — final implementation may adjust variable names/structure to
match repo style, but the behavior above is the contract.)

`asyncio.CancelledError` is not caught here either, so `cancel_streams()`'s
existing `task.cancel()` + `await task` continues to stop the supervisor
(and whatever `stream_main()` call is in flight, or the backoff sleep)
immediately, unchanged from today.

`warned` resets to `False` implicitly each time the loop returns to the top
after a successful reconnect (a fresh `_stream_supervisor` iteration only
warns again if *this* connection attempt also fails) — i.e. only the first
failure of a given outage logs at `warning`; the rest of that outage's
retries log at `debug`. A brand new outage after a successful reconnect
warns again.

### New constants

In `zaptec/const.py`, alongside the existing `API_RETRY_*` constants:

```python
STREAM_RECONNECT_INIT_DELAY = 1.0
STREAM_RECONNECT_FACTOR = 2.0
STREAM_RECONNECT_JITTER = 0.1
STREAM_RECONNECT_MAX_DELAY = 300.0  # 5 minutes
```

Kept separate from `API_RETRY_*` since they govern a different thing (a
long-lived connection's reconnect cadence, not a single HTTP request's
retry count) even though the shape (exponential + jitter, capped) matches.

### Reconnected signal

No new logging plumbing needed: `stream_main()` already logs
`_LOGGER.info("Running service bus stream for %s", self.qual_id)` once it
successfully opens the receiver. That existing line now doubles as the
"reconnected" signal once a prior failure has warned — satisfying the
Home Assistant integration quality-scale guidance ("log a warning once
when unavailable, log once when reconnected") without adding a new log
statement.

## Error handling summary

| Condition | `stream_main()` behavior | Supervisor behavior |
|---|---|---|
| 403 fetching stream connection details | logs warning, returns | stops, no retry |
| `ServiceBusConnectionError` / other transient error | raises | logs once (warn), backs off, retries |
| Integration unload (`cancel_streams()`) | `CancelledError` propagates | `CancelledError` propagates, loop exits |
| Malformed individual stream message | already handled inside the `async for` loop (existing `except Exception: _LOGGER.exception("Couldn't process stream message")`, unchanged) — does not end the stream | n/a |

## Testing

- `stream_main()`: existing tests around `stream_update` routing are
  unaffected. Add a test confirming a non-403 exception now propagates
  out of `stream_main()` instead of being swallowed (behavior change).
- `_stream_supervisor()`: new tests —
  - retries with backoff on a raised exception, calling `stream_main()`
    again;
  - stops (single call, no retry) when `stream_main()` returns normally;
  - propagates `CancelledError` without retrying;
  - resets backoff delay after a connection that stayed up past
    `STREAM_RECONNECT_MAX_DELAY`;
  - logs at `warning` only for the first failure of an outage, `debug`
    for subsequent ones within the same outage.

## Open questions / risks

- `stream_main()` no longer catching its own exceptions means any *unexpected*
  bug in message processing that somehow escapes the inner per-message
  `try/except` would now also be treated as "transient, retry" by the
  supervisor rather than silently logged once and left stopped. This is
  considered acceptable — retrying is a reasonable default reaction to an
  unexpected stream failure, and the per-message handler already isolates
  normal message-processing errors from ending the stream at all.
