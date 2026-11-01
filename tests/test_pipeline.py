"""The pipeline end to end, with the backend faked by respx and the source replaced by an in-memory one."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, ClassVar

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from kombin_workers.backend import BackendClient
from kombin_workers.http import FetchError, PoliteClient, RobotsBlocked, SourceAborted
from kombin_workers.models import NormalizedOffer
from kombin_workers.pipeline import run_source
from kombin_workers.sources.base import BaseSource, ListingRef, SkipItem
from tests.conftest import make_offer

BASE = "http://backend.test"
SELLER = f"{BASE}/api/v1/internal/catalog/sellers/fake-store"
UPSERT = f"{BASE}/api/v1/internal/catalog/offers/upsert"

Behaviour = Callable[[int], Awaitable[NormalizedOffer | None]]


class FakeSource(BaseSource):
    slug: ClassVar[str] = "fake"
    brand_name: ClassVar[str] = "Fake"
    website_url: ClassVar[str] = "https://fake.test"
    seller_name: ClassVar[str] = "Fake"

    def __init__(self, count: int, behaviour: Behaviour | None = None, discover_error: Exception | None = None) -> None:
        super().__init__(http=None, max_pages=1)  # type: ignore[arg-type]
        self.count = count
        self.behaviour = behaviour
        self.discover_error = discover_error
        self.in_flight = 0
        self.max_in_flight = 0

    async def discover(self) -> AsyncIterator[ListingRef]:
        for n in range(self.count):
            yield ListingRef(url=f"https://fake.test/p/{n}", key=str(n), data={"n": n})
        if self.discover_error:
            raise self.discover_error

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.001)
            n = int(ref.data["n"])
            return await self.behaviour(n) if self.behaviour else make_offer(n)
        finally:
            self.in_flight -= 1


class Backend:
    """Records every call the pipeline makes, in order, and answers like the real endpoint would."""

    def __init__(self, router: respx.MockRouter, errors_for: Callable[[str], str | None] = lambda _id: None) -> None:
        self.calls: list[str] = []
        self.batches: list[list[str]] = []
        self.errors_for = errors_for
        router.put(SELLER).mock(side_effect=self._seller)
        router.post(UPSERT).mock(side_effect=self._upsert)

    def _seller(self, request: httpx.Request) -> httpx.Response:
        self.calls.append("PUT seller")
        return httpx.Response(200, json={"id": "s", "slug": "fake-store", "name": "Fake", "kind": "BrandStore", "created": True})

    def _upsert(self, request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        self.calls.append(f"POST upsert x{len(items)}")
        self.batches.append([item["externalId"] for item in items])
        results, errors = [], []
        for index, item in enumerate(items):
            code = self.errors_for(item["externalId"])
            if code:
                errors.append({"index": index, "externalId": item["externalId"], "errorCode": code, "message": f"{code} for {item['externalId']}"})
            else:
                results.append({"index": index, "externalId": item["externalId"], "productId": "p", "offerId": "o", "created": index % 2 == 0})
        created = sum(1 for r in results if r["created"])
        return httpx.Response(200, json={"created": created, "updated": len(results) - created, "snapshotsWritten": len(results), "results": results, "errors": errors})


@pytest.fixture
async def backend_client() -> AsyncIterator[BackendClient]:
    async with BackendClient(BASE, "k", retry_delay=0) as client:
        yield client


async def test_seller_is_registered_before_anything_is_upserted(router: respx.MockRouter, backend_client: BackendClient) -> None:
    backend = Backend(router)
    summary = await run_source(FakeSource(3), backend=backend_client)
    assert backend.calls == ["PUT seller", "POST upsert x3"]
    assert (summary.discovered, summary.fetched, summary.normalized) == (3, 3, 3)
    assert summary.created + summary.updated == 3


async def test_upserts_are_batched_at_200(router: respx.MockRouter, backend_client: BackendClient) -> None:
    backend = Backend(router)
    summary = await run_source(FakeSource(450), backend=backend_client)
    assert backend.calls == ["PUT seller", "POST upsert x200", "POST upsert x200", "POST upsert x50"]
    assert summary.batches == 3
    assert summary.created + summary.updated == 450
    assert sorted({i for batch in backend.batches for i in batch}) == sorted(f"ext-{n}" for n in range(450))


async def test_per_item_backend_errors_are_logged_and_counted_by_code(router: respx.MockRouter, backend_client: BackendClient) -> None:
    def errors_for(external_id: str) -> str | None:
        n = int(external_id.split("-")[1])
        return "Catalog.CategoryNotFound" if n % 5 == 0 else ("Catalog.ItemFailed" if n == 7 else None)

    backend = Backend(router, errors_for)
    with capture_logs() as logs:
        summary = await run_source(FakeSource(12), backend=backend_client)
    assert dict(summary.backend_errors) == {"Catalog.CategoryNotFound": 3, "Catalog.ItemFailed": 1}  # 0, 5, 10 and 7
    assert summary.created + summary.updated == 8
    logged = {entry["external_id"]: entry["code"] for entry in logs if entry["event"] == "upsert_item_error"}
    assert logged == {"ext-0": "Catalog.CategoryNotFound", "ext-5": "Catalog.CategoryNotFound", "ext-10": "Catalog.CategoryNotFound", "ext-7": "Catalog.ItemFailed"}
    assert backend.calls == ["PUT seller", "POST upsert x12"]


async def test_limit_stops_discovery(router: respx.MockRouter, backend_client: BackendClient) -> None:
    backend = Backend(router)
    summary = await run_source(FakeSource(50), backend=backend_client, limit=7)
    assert summary.discovered == 7
    assert backend.calls == ["PUT seller", "POST upsert x7"]


async def test_per_source_cap_applies_even_without_a_limit(router: respx.MockRouter, backend_client: BackendClient) -> None:
    Backend(router)
    assert (await run_source(FakeSource(50), backend=backend_client, max_items=10)).discovered == 10


async def test_skips_failures_and_invalid_items_are_counted_by_reason(router: respx.MockRouter, backend_client: BackendClient) -> None:
    async def behaviour(n: int) -> NormalizedOffer | None:
        if n % 6 == 0:
            raise SkipItem("unknown-category")
        if n % 6 == 1:
            raise SkipItem("no-images")
        if n % 6 == 2:
            raise FetchError("https://fake.test", 404, "HTTP 404")
        if n % 6 == 3:
            return None
        if n % 6 == 4:
            return NormalizedOffer.model_validate({"external_id": f"bad-{n}"})  # missing fields: raises ValidationError
        return make_offer(n)

    Backend(router)
    summary = await run_source(FakeSource(12, behaviour), backend=backend_client, collect=True, concurrency=1)
    assert dict(summary.skipped) == {"unknown-category": 2, "no-images": 2, "no-data": 2, "invalid-item": 2}
    assert dict(summary.failed) == {"http-404": 2}
    assert summary.normalized == 2
    assert [item.external_id for item in summary.items] == ["ext-5", "ext-11"]
    assert summary.fetched == 10  # everything except the two network failures


async def test_duplicate_external_ids_are_sent_once(router: respx.MockRouter, backend_client: BackendClient) -> None:
    async def behaviour(n: int) -> NormalizedOffer | None:
        return make_offer(n // 2)

    backend = Backend(router)
    summary = await run_source(FakeSource(6, behaviour), backend=backend_client, concurrency=1)
    assert summary.normalized == 3
    assert summary.skipped["duplicate"] == 3
    assert backend.calls == ["PUT seller", "POST upsert x3"]


async def test_dry_run_calls_no_backend_and_collects_items(router: respx.MockRouter) -> None:
    summary = await run_source(FakeSource(5), backend=None, collect=True)
    assert len(summary.items) == 5
    assert summary.batches == 0
    assert not router.calls


async def test_concurrency_is_bounded(router: respx.MockRouter, backend_client: BackendClient) -> None:
    Backend(router)
    source = FakeSource(40)
    await run_source(source, backend=backend_client, concurrency=4)
    assert 1 < source.max_in_flight <= 4


async def test_a_source_abort_flushes_what_was_fetched_and_reports(router: respx.MockRouter, backend_client: BackendClient) -> None:
    async def behaviour(n: int) -> NormalizedOffer | None:
        if n == 5:
            raise SourceAborted("20 consecutive failures")
        return make_offer(n)

    backend = Backend(router)
    summary = await run_source(FakeSource(10, behaviour), backend=backend_client, concurrency=1)
    assert summary.aborted == "20 consecutive failures"
    assert summary.normalized == 5
    assert backend.calls == ["PUT seller", "POST upsert x5"]


async def test_robots_blocking_during_discovery_marks_the_run_blocked(router: respx.MockRouter, backend_client: BackendClient) -> None:
    Backend(router)
    summary = await run_source(
        FakeSource(0, discover_error=RobotsBlocked("https://fake.test/collections/x")), backend=backend_client
    )
    assert summary.blocked == "robots.txt disallows https://fake.test/collections/x"
    assert summary.normalized == 0


async def test_a_robots_blocked_product_page_is_only_a_failed_fetch(router: respx.MockRouter, backend_client: BackendClient) -> None:
    async def behaviour(n: int) -> NormalizedOffer | None:
        if n == 1:
            raise RobotsBlocked("https://fake.test/p/1")
        return make_offer(n)

    Backend(router)
    summary = await run_source(FakeSource(3, behaviour), backend=backend_client)
    assert summary.failed["robots-blocked"] == 1
    assert summary.normalized == 2
    assert summary.blocked is None


async def test_a_blocked_source_does_nothing(router: respx.MockRouter, backend_client: BackendClient) -> None:
    class BlockedSource(FakeSource):
        blocked: ClassVar[bool] = True
        notes: ClassVar[str] = "robots.txt disallows /products"

    summary = await run_source(BlockedSource(5), backend=backend_client)
    assert summary.blocked == "robots.txt disallows /products"
    assert not router.calls


async def test_unexpected_errors_in_one_item_do_not_stop_the_run(router: respx.MockRouter, backend_client: BackendClient) -> None:
    async def behaviour(n: int) -> NormalizedOffer | None:
        if n == 2:
            raise KeyError("boom")
        return make_offer(n)

    Backend(router)
    summary = await run_source(FakeSource(5, behaviour), backend=backend_client)
    assert summary.failed["unexpected-error"] == 1
    assert summary.normalized == 4


async def test_summary_formats_and_serializes(router: respx.MockRouter, backend_client: BackendClient) -> None:
    Backend(router)
    summary = await run_source(FakeSource(3), backend=backend_client)
    text = summary.format()
    assert "discovered:      3" in text
    assert "upserted:" in text
    data: dict[str, Any] = summary.to_dict()
    assert data["source"] == "fake"
    assert data["upserted"]["batches"] == 1


async def test_pipeline_with_a_real_source_over_http(router: respx.MockRouter, backend_client: BackendClient) -> None:
    """Derimod fixture -> PoliteClient -> pipeline -> faked backend: the whole stack, no fake source."""
    from kombin_workers.sources.derimod import DerimodSource
    from tests.conftest import allow_robots, fixture_json, fixture_text

    allow_robots(router, "https://derimod.com.tr", fixture_text("derimod/robots.txt"))
    router.get(url__regex=r"https://derimod\.com\.tr/collections/.*").respond(
        200, json=fixture_json("derimod/collection_erkek-deri-sneaker.json")
    )
    backend_calls: list[Any] = []

    def seller(request: httpx.Request) -> httpx.Response:
        backend_calls.append(("PUT", json.loads(request.content)))
        return httpx.Response(200, json={"id": "s", "slug": "derimod-store", "name": "Derimod", "kind": "BrandStore", "created": False})

    def upsert(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        backend_calls.append(("POST", body))
        return httpx.Response(200, json={"created": len(body["items"]), "updated": 0, "snapshotsWritten": 0, "results": [], "errors": []})

    router.put(f"{BASE}/api/v1/internal/catalog/sellers/derimod-store").mock(side_effect=seller)
    router.post(UPSERT).mock(side_effect=upsert)

    async with PoliteClient(user_agent="KombinBot/test", requests_per_second=1000) as http:
        summary = await run_source(DerimodSource(http, max_pages=1), backend=backend_client)

    assert [method for method, _ in backend_calls] == ["PUT", "POST"]
    put_body, post_body = backend_calls[0][1], backend_calls[1][1]
    assert put_body["kind"] == "BrandStore"
    assert post_body["sellerSlug"] == "derimod-store"
    assert summary.normalized == len(post_body["items"]) == 5
    assert "WM790004DN-5637145360" in {item["product"]["modelCode"] for item in post_body["items"]}
