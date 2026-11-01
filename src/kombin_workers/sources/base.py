"""The `Source` contract and the pieces every source module shares."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

import structlog

from kombin_workers.http import FetchError, PoliteClient, RobotsBlocked, SourceAborted
from kombin_workers.models import NormalizedItem, SellerKind, SellerRegistration

log = structlog.get_logger(__name__)


class SkipItem(Exception):
    """The page was read fine but the item is not for us. `reason` becomes a key in the run summary."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ListingRef:
    """A product found during discovery.

    `key` identifies the product within the source (used to drop duplicates found in several categories). `data` is
    whatever discovery already read (a JSON record, a listing card) so `fetch` can avoid a request when it is enough.
    """

    url: str
    key: str
    category_hint: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Listing:
    """One category page of a site, mapped to our category (`hint` is only a hint; the item decides)."""

    path: str
    hint: str


@dataclass(frozen=True)
class Page:
    refs: list[ListingRef]
    has_more: bool


@runtime_checkable
class Source(Protocol):
    slug: str
    seller_slug: str
    brand_name: str
    seller: SellerRegistration
    blocked: bool
    notes: str

    def discover(self) -> AsyncIterator[ListingRef]: ...

    async def fetch(self, ref: ListingRef) -> NormalizedItem | None: ...


class BaseSource:
    """Shared plumbing: the HTTP client, the page budget, page-major discovery.

    Subclasses set the class attributes and implement `discover` (usually via `paged_discovery`) and `fetch`.
    """

    slug: ClassVar[str]
    brand_name: ClassVar[str]
    website_url: ClassVar[str]
    seller_name: ClassVar[str]
    blocked: ClassVar[bool] = False
    notes: ClassVar[str] = ""

    def __init__(self, http: PoliteClient, max_pages: int = 5) -> None:
        self.http = http
        self.max_pages = max_pages

    @property
    def seller_slug(self) -> str:
        """The backend creates `<brand-slug>-store` on demand; we register exactly that slug."""
        return f"{self.slug}-store"

    @property
    def seller(self) -> SellerRegistration:
        return SellerRegistration(
            name=self.seller_name,
            kind=SellerKind.BRAND_STORE,
            website_url=self.website_url,
            affiliate_network=None,
            affiliate_url_template=None,
            affiliate_params=None,
            is_active=True,
        )

    def discover(self) -> AsyncIterator[ListingRef]:
        """Yield every product to fetch (usually through `paged_discovery`)."""
        raise NotImplementedError

    async def fetch(self, ref: ListingRef) -> NormalizedItem | None:
        """Read one product and normalize it; raise `SkipItem` for products that are not for us."""
        raise NotImplementedError

    async def paged_discovery(
        self,
        listings: Sequence[Listing],
        read_page: Callable[[Listing, int], Awaitable[Page]],
    ) -> AsyncIterator[ListingRef]:
        """Page 1 of every listing, then page 2 of every listing, ... up to `max_pages`.

        Going page-major keeps categories balanced when a run is cut short by `--limit` or the per-source cap.
        A listing that fails (404, robots) is dropped; if robots blocks every listing the source is blocked.
        """
        active = list(listings)
        seen: set[str] = set()
        yielded = 0
        robots_blocked: RobotsBlocked | None = None
        for page_number in range(1, self.max_pages + 1):
            still_active: list[Listing] = []
            for listing in active:
                try:
                    page = await read_page(listing, page_number)
                except SourceAborted:
                    raise
                except RobotsBlocked as exc:
                    log.warning("listing_blocked_by_robots", source=self.slug, listing=listing.path)
                    robots_blocked = exc
                    continue
                except FetchError as exc:
                    log.warning("listing_failed", source=self.slug, listing=listing.path, page=page_number, error=str(exc))
                    continue
                for ref in page.refs:
                    if ref.key in seen:
                        continue
                    seen.add(ref.key)
                    yielded += 1
                    yield ref
                if page.has_more:
                    still_active.append(listing)
            active = still_active
            if not active:
                break
        if yielded == 0 and robots_blocked is not None:
            raise robots_blocked
