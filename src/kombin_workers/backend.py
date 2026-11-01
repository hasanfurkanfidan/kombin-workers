"""Typed client for the backend's internal catalog endpoints (all carry `X-Internal-Key`)."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Annotated, Any, Self

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from kombin_workers.models import NormalizedOffer, SellerRegistration

log = structlog.get_logger(__name__)

INTERNAL = "/api/v1/internal/catalog"
MAX_BATCH = 500
_RETRY_STATUSES = frozenset({502, 503, 504})


class BackendError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"backend answered {status}: {message}")
        self.status = status


class _Response(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class UpsertItemResult(_Response):
    index: int
    external_id: str
    product_id: str
    offer_id: str
    created: bool


class UpsertItemError(_Response):
    index: int
    external_id: str
    error_code: str
    message: str


class UpsertResponse(_Response):
    created: int
    updated: int
    snapshots_written: int = 0
    results: list[UpsertItemResult] = Field(default_factory=list)
    errors: list[UpsertItemError] = Field(default_factory=list)


class PutSellerResponse(_Response):
    id: str
    slug: str
    name: str
    created: bool


class SellerOfferItem(_Response):
    external_id: str
    product_id: str
    model_code: str | None = None
    current_price: Annotated[float, Field(ge=0)]
    stock_status: str
    last_seen_at: str
    is_active: bool


class SellerOffersPage(_Response):
    items: list[SellerOfferItem]
    page: int
    page_size: int
    total_count: int
    total_pages: int = 0


class SweepResponse(_Response):
    offers_deactivated: int
    products_deactivated: int
    products_reactivated: int


@dataclass
class UpsertTotals:
    """What a whole run's batches added up to."""

    created: int = 0
    updated: int = 0
    batches: int = 0
    errors_by_code: Counter[str] = field(default_factory=Counter)

    def add(self, response: UpsertResponse) -> None:
        self.created += response.created
        self.updated += response.updated
        self.batches += 1
        for error in response.errors:
            self.errors_by_code[error.error_code] += 1


class BackendClient:
    def __init__(
        self,
        base_url: str,
        internal_key: str,
        *,
        batch_size: int = 200,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 2.0,
    ) -> None:
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH}")
        self.batch_size = batch_size
        self._retry_delay = retry_delay
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Internal-Key": internal_key},
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def put_seller(self, slug: str, seller: SellerRegistration) -> PutSellerResponse:
        data = await self._send("PUT", f"{INTERNAL}/sellers/{slug}", json=seller.to_wire())
        return PutSellerResponse.model_validate(data)

    async def upsert(self, seller_slug: str, items: Sequence[NormalizedOffer]) -> UpsertResponse:
        """One call, at most `batch_size` items. Per-item errors are logged and returned, not raised."""
        if len(items) > self.batch_size:
            raise ValueError(f"{len(items)} items exceed the batch size of {self.batch_size}")
        body = {"sellerSlug": seller_slug, "items": [item.to_wire() for item in items]}
        response = UpsertResponse.model_validate(await self._send("POST", f"{INTERNAL}/offers/upsert", json=body))
        for error in response.errors:
            log.warning(
                "upsert_item_error",
                seller=seller_slug,
                external_id=error.external_id,
                index=error.index,
                code=error.error_code,
                message=error.message,
            )
        return response

    async def upsert_all(self, seller_slug: str, items: Iterable[NormalizedOffer]) -> UpsertTotals:
        """Upsert in batches of `batch_size`, in order."""
        totals = UpsertTotals()
        batch: list[NormalizedOffer] = []
        for item in items:
            batch.append(item)
            if len(batch) == self.batch_size:
                totals.add(await self.upsert(seller_slug, batch))
                batch = []
        if batch:
            totals.add(await self.upsert(seller_slug, batch))
        return totals

    async def list_offers(
        self, seller_slug: str, *, page: int = 1, page_size: int = 100, updated_after: str | None = None
    ) -> SellerOffersPage:
        params: dict[str, Any] = {"Page": page, "PageSize": page_size}
        if updated_after:
            params["UpdatedAfter"] = updated_after
        data = await self._send("GET", f"{INTERNAL}/sellers/{seller_slug}/offers", params=params)
        return SellerOffersPage.model_validate(data)

    async def sweep(self, seller_slug: str | None = None) -> SweepResponse:
        data = await self._send("POST", f"{INTERNAL}/offers/sweep", json={"sellerSlug": seller_slug})
        return SweepResponse.model_validate(data)

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        attempts = 3
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise BackendError(0, f"{type(exc).__name__}: {exc}") from exc
            else:
                if response.status_code not in _RETRY_STATUSES or attempt + 1 == attempts:
                    if response.is_success:
                        return response.json()
                    raise BackendError(response.status_code, _problem_text(response))
            await asyncio.sleep(self._retry_delay * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover


def _problem_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or body)[:300]
    return str(body)[:300]
