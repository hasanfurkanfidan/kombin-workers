from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from kombin_workers.backend import BackendClient, BackendError
from kombin_workers.models import SellerKind, SellerRegistration
from tests.conftest import make_offer

BASE = "http://backend.test"
UPSERT = f"{BASE}/api/v1/internal/catalog/offers/upsert"


def ok_upsert(request: httpx.Request) -> httpx.Response:
    items = json.loads(request.content)["items"]
    return httpx.Response(
        200,
        json={
            "created": len(items),
            "updated": 0,
            "snapshotsWritten": len(items),
            "results": [
                {"index": i, "externalId": it["externalId"], "productId": "p", "offerId": "o", "created": True}
                for i, it in enumerate(items)
            ],
            "errors": [],
        },
    )


@pytest.fixture
async def client() -> AsyncIterator[BackendClient]:
    async with BackendClient(BASE, "secret-key", retry_delay=0) as backend:
        yield backend


async def test_put_seller_sends_the_key_and_the_whole_seller(router: respx.MockRouter, client: BackendClient) -> None:
    route = router.put(f"{BASE}/api/v1/internal/catalog/sellers/koton-store").respond(
        200, json={"id": "abc", "slug": "koton-store", "name": "Koton", "kind": "BrandStore", "created": True}
    )
    result = await client.put_seller(
        "koton-store", SellerRegistration(name="Koton", kind=SellerKind.BRAND_STORE, website_url="https://www.koton.com")
    )
    assert result.created is True
    request = route.calls.last.request
    assert request.headers["X-Internal-Key"] == "secret-key"
    body = json.loads(request.content)
    assert body["kind"] == "BrandStore"
    assert body["websiteUrl"] == "https://www.koton.com"
    assert body["affiliateParams"] is None


async def test_upsert_body_shape(router: respx.MockRouter, client: BackendClient) -> None:
    route = router.post(UPSERT).mock(side_effect=ok_upsert)
    response = await client.upsert("koton-store", [make_offer(1), make_offer(2)])
    assert (response.created, response.updated) == (2, 0)
    body = json.loads(route.calls.last.request.content)
    assert body["sellerSlug"] == "koton-store"
    assert [item["externalId"] for item in body["items"]] == ["ext-1", "ext-2"]
    assert body["items"][0]["product"]["modelCode"] == "MC-1"
    assert route.calls.last.request.headers["X-Internal-Key"] == "secret-key"


async def test_batches_never_exceed_the_batch_size(router: respx.MockRouter, client: BackendClient) -> None:
    route = router.post(UPSERT).mock(side_effect=ok_upsert)
    totals = await client.upsert_all("koton-store", (make_offer(n) for n in range(450)))
    sizes = [len(json.loads(call.request.content)["items"]) for call in route.calls]
    assert sizes == [200, 200, 50]
    assert (totals.created, totals.batches) == (450, 3)


async def test_oversized_batch_is_refused_locally(client: BackendClient) -> None:
    with pytest.raises(ValueError, match="batch size"):
        await client.upsert("koton-store", [make_offer(n) for n in range(201)])
    with pytest.raises(ValueError, match="between 1 and 500"):
        BackendClient(BASE, "k", batch_size=501)


async def test_per_item_errors_are_logged_and_counted(router: respx.MockRouter, client: BackendClient) -> None:
    router.post(UPSERT).respond(
        200,
        json={
            "created": 1,
            "updated": 0,
            "snapshotsWritten": 1,
            "results": [{"index": 0, "externalId": "ext-1", "productId": "p", "offerId": "o", "created": True}],
            "errors": [
                {"index": 1, "externalId": "ext-2", "errorCode": "Catalog.CategoryNotFound", "message": "no such category"},
                {"index": 2, "externalId": "ext-3", "errorCode": "Catalog.CategoryNotFound", "message": "no such category"},
            ],
        },
    )
    with capture_logs() as logs:
        totals = await client.upsert_all("koton-store", [make_offer(1), make_offer(2), make_offer(3)])
    assert dict(totals.errors_by_code) == {"Catalog.CategoryNotFound": 2}
    item_errors = [entry for entry in logs if entry["event"] == "upsert_item_error"]
    assert [entry["external_id"] for entry in item_errors] == ["ext-2", "ext-3"]
    assert item_errors[0]["code"] == "Catalog.CategoryNotFound"
    assert item_errors[0]["message"] == "no such category"
    assert item_errors[0]["log_level"] == "warning"


async def test_list_offers_and_sweep(router: respx.MockRouter, client: BackendClient) -> None:
    list_route = router.get(f"{BASE}/api/v1/internal/catalog/sellers/koton-store/offers").respond(
        200,
        json={
            "items": [
                {
                    "externalId": "a",
                    "productId": "p",
                    "modelCode": "M1",
                    "currentPrice": 10,
                    "stockStatus": "InStock",
                    "lastSeenAt": "2026-11-01T00:00:00Z",
                    "isActive": True,
                }
            ],
            "page": 2,
            "pageSize": 50,
            "totalCount": 51,
            "totalPages": 2,
        },
    )
    page = await client.list_offers("koton-store", page=2, page_size=50)
    assert page.items[0].model_code == "M1"
    assert dict(list_route.calls.last.request.url.params) == {"Page": "2", "PageSize": "50"}

    sweep_route = router.post(f"{BASE}/api/v1/internal/catalog/offers/sweep").respond(
        200, json={"offersDeactivated": 3, "productsDeactivated": 1, "productsReactivated": 0}
    )
    result = await client.sweep()
    assert (result.offers_deactivated, result.products_deactivated) == (3, 1)
    assert json.loads(sweep_route.calls.last.request.content) == {"sellerSlug": None}
    await client.sweep("koton-store")
    assert json.loads(sweep_route.calls.last.request.content) == {"sellerSlug": "koton-store"}


async def test_backend_gateway_errors_are_retried(router: respx.MockRouter, client: BackendClient) -> None:
    attempts: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503) if len(attempts) == 1 else ok_upsert(request)

    route = router.post(UPSERT).mock(side_effect=flaky)
    await client.upsert("koton-store", [make_offer(1)])
    assert route.call_count == 2


async def test_errors_carry_the_problem_detail(router: respx.MockRouter, client: BackendClient) -> None:
    router.post(UPSERT).respond(401, json={"title": "Unauthorized", "detail": "bad key"})
    with pytest.raises(BackendError, match="401: bad key"):
        await client.upsert("koton-store", [make_offer(1)])
