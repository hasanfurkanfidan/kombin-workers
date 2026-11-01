"""The daemon's loop: `run-all` every N hours, `sweep` every M hours, both with jitter. Deliberately tiny."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

Job = Callable[[], Awaitable[None]]


async def daemon_loop(
    *,
    run_all: Job,
    sweep: Job,
    run_every: float,
    sweep_every: float,
    jitter: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
    max_cycles: int | None = None,
) -> None:
    """Run forever (or `max_cycles` wake-ups, for tests). `run_all` starts at once; the first `sweep` is due after
    `sweep_every`, since there is nothing to sweep before the first run. A failing job is logged and retried at its
    next due time; it never stops the loop."""
    next_run = clock()
    next_sweep = clock() + sweep_every
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        now = clock()
        if now >= next_run:
            await _guarded("run-all", run_all)
            next_run = clock() + run_every + rand() * jitter
        if now >= next_sweep:
            await _guarded("sweep", sweep)
            next_sweep = clock() + sweep_every + rand() * jitter
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        delay = max(0.0, min(next_run, next_sweep) - clock())
        log.info("daemon_sleeping", seconds=round(delay))
        await sleep(delay)


async def _guarded(name: str, job: Job) -> None:
    try:
        log.info("job_started", job=name)
        await job()
        log.info("job_finished", job=name)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("job_failed", job=name)
