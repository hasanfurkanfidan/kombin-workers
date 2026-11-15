"""Hotiç (hotic.com.tr) - Turkish footwear and bags, women and men.

Platform: Next.js. Every listing page ships `__NEXT_DATA__` whose `productList.products.data` holds the complete record of
each product (20 a page): price and old price, one variant per size with its stock quantity, pictures, colour, category
breadcrumb. So `fetch` makes no request - a listing page is the product record, like Derimod and Network.
robots.txt: `Allow: /`; it disallows account/basket paths and the query strings `*category`, `*ordering`,
`*searchValue`, `*defaultMinPrice`, `*defaultMaxPrice`, `*script*`. We only add `?pageIndex=N` (zero-based).

Where each field comes from (see docs/sources.md):
- externalId / modelCode  `erpCode`, e.g. `02AYH333890A100` = style `02AYH33389` + colour `0A100` -> `02AYH33389-0A100`.
- price                   `price.newPrice`; crossed-out price = `price.oldPrice` when higher.
- sizes / stock           `variants[]`: `specValueName` where `quantity > 0`; at most 3 pairs left -> LowStock.
- audience / category     `categories[].name` ("Erkek Loafer Ayakkabı") and `productName`.
- colour                  `relatedVariants[]` entry with `isSelected` (`specValueName`: "SİYAH").
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color
from kombin_workers.normalize.images import order_gallery
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience
from kombin_workers.sources.embedded import dig, extract_next_data

BASE = "https://www.hotic.com.tr"
BRAND_SLUG = "hotic"
LOW_STOCK_UNITS = 3

_WOMEN_SHOES = "kadin-ayakkabi-kadin-gunluk-ayakkabi-kadin-"
_MEN_SHOES = "erkek-ayakkabi-erkek-tum-ayakkabilar-erkek-"

# Women's and men's listings alternate so a capped run takes an even share of both. Sandals, slippers, flats, heels and
# wallets are left out: they have no category in the taxonomy.
LISTINGS = (
    Listing(f"{_WOMEN_SHOES}spor-ayakkabi", "ayakkabi/sneaker"),
    Listing(f"{_MEN_SHOES}spor-ayakkabi", "ayakkabi/sneaker"),
    Listing(f"{_WOMEN_SHOES}loafer-ayakkabi", "ayakkabi/loafer"),
    Listing(f"{_MEN_SHOES}loafer-ayakkabi", "ayakkabi/loafer"),
    Listing(f"{_WOMEN_SHOES}oxford-ayakkabi", "ayakkabi/loafer"),
    Listing(f"{_MEN_SHOES}klasik-ayakkabi", "ayakkabi/loafer"),
    Listing("kadin-ayakkabi-kadin-bot-kadin-duz-bot", "ayakkabi/bot"),
    Listing("erkek-ayakkabi-erkek-bot-erkek-casual-bot", "ayakkabi/bot"),
    Listing("kadin-ayakkabi-cizme-duz-cizme", "ayakkabi/bot"),
    Listing("erkek-ayakkabi-erkek-bot-erkek-chelsea-bot", "ayakkabi/bot"),
    Listing("cantaaksesuar-kadin-cantaaksesuar-kadin-omuz-cantasi", "aksesuar/canta"),
    Listing("cantaaksesuar-erkek-cantaaksesuar-erkek-postaci-cantasi", "aksesuar/canta"),
    Listing("cantaaksesuar-kadin-cantaaksesuar-kadin-sirt-cantasi", "aksesuar/canta"),
    Listing("cantaaksesuar-erkek-cantaaksesuar-erkek-kemer", "aksesuar/kemer"),
)


class HoticSource(BaseSource):
    slug = "hotic"
    brand_name = "Hotiç"
    seller_name = "Hotiç"
    website_url = BASE
    notes = "Next.js: listing pages embed full product records (__NEXT_DATA__); robots.txt allows category and product pages."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        html = await self.http.get_text(f"{BASE}/{listing.path}?pageIndex={page - 1}")
        products = dig(extract_next_data(html), "props", "pageProps", "productList", "products") or {}
        refs = []
        for record in products.get("data") or []:
            if isinstance(record, dict) and record.get("erpCode") and record.get("slug"):
                refs.append(
                    ListingRef(
                        url=f"{BASE}/{record['slug']}", key=str(record["erpCode"]), category_hint=listing.hint, data=record
                    )
                )
        return Page(refs=refs, has_more=bool(products.get("hasNextPage")))

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        return normalize_product(dict(ref.data), ref.url)


def gallery_urls(record: dict[str, Any]) -> list[str]:
    """Every `pictures[]` entry by `displayOrder`, packshot hints first, at most 8 (see `normalize.images`)."""
    pictures = [p for p in record.get("pictures") or [] if isinstance(p, dict) and p.get("url")]
    return order_gallery(clean_text(p["url"]) for p in sorted(pictures, key=lambda p: int(p.get("displayOrder") or 0)))


def normalize_product(record: dict[str, Any], url: str) -> NormalizedOffer:
    if record.get("brandSlug") not in (None, BRAND_SLUG):
        raise SkipItem("other-brand")
    erp = clean_text(record.get("erpCode"))
    if not erp:
        raise SkipItem("no-model-code")

    title = clean_text(record.get("productName"))
    category_names = [clean_text(c.get("name")) for c in record.get("categories") or [] if isinstance(c, dict)]
    category = map_category(category_names[-1] if category_names else None, title, source="hotic")
    if category is None:
        raise SkipItem("unknown-category")
    audience = adult_audience(*category_names, title)

    price_block: dict[str, Any] = record.get("price") or {}
    price = _money(price_block.get("newPrice"))
    if price is None:
        raise SkipItem("no-price")
    old = _money(price_block.get("oldPrice"))
    original = old if old is not None and old > price else None

    images = gallery_urls(record)
    if not images:
        raise SkipItem("no-images")

    in_stock = [v for v in record.get("variants") or [] if isinstance(v, dict) and int(v.get("quantity") or 0) > 0]
    sizes = normalize_sizes(v.get("specValueName") for v in in_stock)
    units = sum(int(v["quantity"]) for v in in_stock)
    if not in_stock:
        stock = StockStatus.OUT_OF_STOCK
    elif units <= LOW_STOCK_UNITS:
        stock = StockStatus.LOW_STOCK
    else:
        stock = StockStatus.IN_STOCK

    related = [r for r in record.get("relatedVariants") or [] if isinstance(r, dict)]
    selected = next((r for r in related if r.get("isSelected")), related[0] if related else {})
    color = map_color(selected.get("specValueName"))

    model_code = f"{erp[:-5]}-{erp[-5:]}" if len(erp) >= 10 else erp
    description = clean_text(record.get("shortDescription"))
    inferred = infer_attributes(category, title, description, material_texts=(title,))

    return NormalizedOffer(
        external_id=erp,
        url=url,
        title=title[:300],
        price=price,
        original_price=original,
        stock_status=stock,
        available_sizes=sizes,
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Hotiç",
            name=title[:200],
            model_code=model_code,
            category_slug=category,
            audience=audience,
            primary_color=color,
            pattern=inferred.pattern,
            fit=inferred.fit,
            formality=inferred.formality,
            seasons=inferred.seasons,
            material=inferred.material,
            description=description[:4000] or None,
        ),
    )


def _money(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None
