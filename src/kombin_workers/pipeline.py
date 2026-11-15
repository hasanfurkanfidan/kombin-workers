"""discover -> fetch -> normalize -> batch upsert -> summary, for one source."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError

from kombin_workers.backend import BackendClient
from kombin_workers.http import FetchError, RobotsBlocked, SourceAborted
from kombin_workers.models import NormalizedOffer
from kombin_workers.normalize.images import first_is_person_free
from kombin_workers.sources.base import BaseSource, ListingRef, SkipItem

log = structlog.get_logger(__name__)

DEFAULT_MAX_ITEMS = 1500


@dataclass
class RunSummary:
    source: str
    discovered: int = 0
    fetched: int = 0
    normalized: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    failed: Counter[str] = field(default_factory=Counter)
    created: int = 0
    updated: int = 0
    batches: int = 0
    backend_errors: Counter[str] = field(default_factory=Counter)
    normalized_by_category: Counter[str] = field(default_factory=Counter)
    upserted_by_category: Counter[str] = field(default_factory=Counter)
    upserted_by_audience: Counter[str] = field(default_factory=Counter)
    image_total: int = 0
    first_image_person_free: int = 0
    blocked: str | None = None
    aborted: str | None = None
    duration_seconds: float = 0.0
    items: list[NormalizedOffer] = field(default_factory=list)

    @property
    def average_images(self) -> float:
        return self.image_total / self.normalized if self.normalized else 0.0

    @property
    def person_free_first_share(self) -> float:
        """Share of normalized products whose first image is likely person-free (by `normalize.images` hints)."""
        return self.first_image_person_free / self.normalized if self.normalized else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "discovered": self.discovered,
            "fetched": self.fetched,
            "normalized": self.normalized,
            "skipped": dict(self.skipped),
            "failed": dict(self.failed),
            "upserted": {"created": self.created, "updated": self.updated, "batches": self.batches},
            "backendErrors": dict(self.backend_errors),
            "normalizedByCategory": dict(sorted(self.normalized_by_category.items())),
            "upsertedByCategory": dict(sorted(self.upserted_by_category.items())),
            "upsertedByAudience": dict(sorted(self.upserted_by_audience.items())),
            "images": {
                "average": round(self.average_images, 2),
                "firstLikelyPersonFree": round(self.person_free_first_share, 3),
            },
            "blocked": self.blocked,
            "aborted": self.aborted,
            "durationSeconds": round(self.duration_seconds, 1),
        }

    def format(self) -> str:
        def counts(counter: Counter[str]) -> str:
            return ", ".join(f"{key}={count}" for key, count in sorted(counter.items())) or "none"

        lines = [
            f"source:          {self.source}",
            f"discovered:      {self.discovered}",
            f"fetched:         {self.fetched}",
            f"normalized:      {self.normalized}",
            f"skipped:         {counts(self.skipped)}",
            f"fetch failures:  {counts(self.failed)}",
            f"upserted:        created={self.created} updated={self.updated} in {self.batches} batch(es)",
            f"backend errors:  {counts(self.backend_errors)}",
            f"by category:     {counts(self.upserted_by_category if self.batches else self.normalized_by_category)}",
            f"by audience:     {counts(self.upserted_by_audience)}",
            f"images:          {self.average_images:.1f} per product, first image likely person-free "
            f"{self.person_free_first_share:.0%}",
            f"duration:        {self.duration_seconds:.1f}s",
        ]
        if self.blocked:
            lines.append(f"BLOCKED:         {self.blocked}")
        if self.aborted:
            lines.append(f"ABORTED:         {self.aborted}")
        return "\n".join(lines)


async def run_source(
    source: BaseSource,
    *,
    backend: BackendClient | None,
    limit: int | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    concurrency: int = 4,
    collect: bool = False,
) -> RunSummary:
    """Run one source end to end.

    With `backend=None` nothing is sent anywhere (the `check` dry run); `collect=True` keeps the normalized items in
    `summary.items`. The seller is registered before anything is discovered. Fetches run `concurrency` at a time, but
    every request still passes the per-host rate limiter, so the site never sees more than the polite rate.
    """
    summary = RunSummary(source=source.slug)
    started = time.monotonic()
    cap = min(limit, max_items) if limit else max_items

    if source.blocked:
        summary.blocked = source.notes or "marked blocked in its module"
        return summary

    if backend is not None:
        await backend.put_seller(source.seller_slug, source.seller)
        log.info("seller_registered", seller=source.seller_slug)

    batch: list[NormalizedOffer] = []
    seen_ids: set[str] = set()
    pending: set[asyncio.Task[NormalizedOffer | None]] = set()
    refs: dict[asyncio.Task[NormalizedOffer | None], ListingRef] = {}
    gate = asyncio.Semaphore(concurrency)

    async def fetch_one(ref: ListingRef) -> NormalizedOffer | None:
        async with gate:
            return await source.fetch(ref)

    async def flush() -> None:
        if backend is None or not batch:
            return
        sent = list(batch)
        response = await backend.upsert(source.seller_slug, sent)
        batch.clear()
        summary.created += response.created
        summary.updated += response.updated
        summary.batches += 1
        rejected = {error.index for error in response.errors}
        for error in response.errors:
            summary.backend_errors[error.error_code] += 1
        for index, offer in enumerate(sent):
            if index not in rejected:
                summary.upserted_by_category[offer.product.category_slug] += 1
                summary.upserted_by_audience[offer.product.audience.value] += 1

    async def settle(done: set[asyncio.Task[NormalizedOffer | None]]) -> None:
        for task in done:
            item = _result_of(task, refs.pop(task, None), summary)
            if item is None:
                continue
            if item.external_id in seen_ids:
                summary.skipped["duplicate"] += 1
                continue
            seen_ids.add(item.external_id)
            summary.normalized += 1
            summary.normalized_by_category[item.product.category_slug] += 1
            summary.image_total += len(item.image_urls)
            summary.first_image_person_free += first_is_person_free(item.image_urls)
            if collect:
                summary.items.append(item)
            batch.append(item)
            if backend is not None and len(batch) >= backend.batch_size:
                await flush()

    try:
        async for ref in source.discover():
            if summary.discovered >= cap:
                break
            summary.discovered += 1
            task = asyncio.create_task(fetch_one(ref))
            pending.add(task)
            refs[task] = ref
            if len(pending) >= concurrency * 2:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                await settle(done)
        if pending:
            done, pending = await asyncio.wait(pending)
            await settle(done)
    except RobotsBlocked as exc:
        summary.blocked = f"robots.txt disallows {exc.url}"
        log.error("source_blocked", source=source.slug, url=exc.url)
    except SourceAborted as exc:
        summary.aborted = str(exc)
        log.error("source_aborted", source=source.slug, reason=str(exc))
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    try:
        await flush()
    finally:
        summary.duration_seconds = time.monotonic() - started
    log.info("run_finished", **summary.to_dict())
    return summary


def _result_of(
    task: asyncio.Task[NormalizedOffer | None], ref: ListingRef | None, summary: RunSummary
) -> NormalizedOffer | None:
    """Turn a finished fetch task into an item, or count why there is none. Re-raises a source abort."""
    url = ref.url if ref else "?"
    try:
        item = task.result()
    except SkipItem as skip:
        summary.fetched += 1
        summary.skipped[skip.reason] += 1
        return None
    except SourceAborted:
        raise
    except RobotsBlocked:
        summary.failed["robots-blocked"] += 1
        return None
    except FetchError as exc:
        summary.failed[f"http-{exc.status}" if exc.status else "fetch-error"] += 1
        log.warning("fetch_failed", url=url, error=str(exc))
        return None
    except ValidationError as exc:
        summary.fetched += 1
        summary.skipped["invalid-item"] += 1
        log.warning("invalid_item", url=url, problems=[f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3]])
        return None
    except asyncio.CancelledError:
        return None
    except Exception:
        summary.failed["unexpected-error"] += 1
        log.exception("unexpected_fetch_error", url=url)
        return None
    summary.fetched += 1
    if item is None:
        summary.skipped["no-data"] += 1
    return item
