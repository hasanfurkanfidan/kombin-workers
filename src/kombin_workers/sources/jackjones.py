"""Jack & Jones Turkey (jackjones.com.tr) - menswear and a little womenswear (Bestseller group storefront).

Platform: a Vite/Vue storefront that is server-rendered. Every page ships the data it was rendered from as
`window.__INITIAL_STATE__ = {...}` (plain JSON), and product pages also carry `application/ld+json` Product markup.
robots.txt disallows only login/account/checkout/search paths (`/tr-tr/arama*`); listings and products are allowed.

- Discovery: `/tr-tr/<category>?page=N` - 60 products a page. The listing's state (`$getProducts-*.results`) already
  has name, url, price and colour for each product, but only one size per colour, so...
- Fetch: ... the product page's state (`$initialContentResponse-*.product`) has every size variant with its stock.

Where each field comes from (see docs/sources.md):
- externalId     product `id` (`<style>_<colour>`, e.g. 12298420_2161).
- modelCode      `<styleNumber>-<colour id>`, e.g. 12298420-2161 (Bestseller's article number plus colour).
- price          `price.salesPrice.amount`. A crossed-out price would be a `listPrice`-like field next to it; none of
                 the products seen while building this had one, so `_original_price` only reads the obvious names.
- sizes/stock    `variants[].size.name` where `inStockEcom`.
- audience       `englishGender`; category `ediCategory`/`ediSubCategory`; fit from the `*fit` tags.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.audience import map_audience
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color, try_map_color
from kombin_workers.normalize.images import order_gallery
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text, strip_brand_prefix
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem

BASE = "https://www.jackjones.com.tr"
STATE_MARKER = "window.__INITIAL_STATE__"
_ORIGINAL_PRICE_KEYS = ("listPrice", "originalPrice", "beforePrice", "regularPrice", "wasPrice")

LISTINGS = (
    Listing("erkek-giyim/tisort", "ust-giyim/tisort"),
    Listing("erkek-giyim/gomlek", "ust-giyim/gomlek"),
    Listing("erkek-giyim/pantolon", "alt-giyim/pantolon"),
    Listing("erkek-giyim/erkek-denim/jean", "alt-giyim/jean"),
    Listing("erkek-giyim/kazak", "ust-giyim/triko"),
    Listing("erkek-giyim/triko", "ust-giyim/triko"),
    Listing("erkek-giyim/mont-ceket", "dis-giyim/ceket"),
    Listing("erkek-giyim/ayakkabi", "ayakkabi/sneaker"),
    Listing("erkek-giyim/aksesuar", "aksesuar/kemer"),
    Listing("kadin-giyim/gomlek", "ust-giyim/gomlek"),
    Listing("kadin-giyim/pantolon", "alt-giyim/pantolon"),
    Listing("kadin-giyim/jean", "alt-giyim/jean"),
    Listing("kadin-giyim/mont-ve-ceket", "dis-giyim/ceket"),
)


def extract_initial_state(html: str) -> dict[str, Any]:
    """The `window.__INITIAL_STATE__` JSON object of a page, or {} if the page has none."""
    marker = html.find(STATE_MARKER)
    if marker < 0:
        return {}
    start = html.find("{", marker)
    if start < 0:
        return {}
    try:
        state, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


class JackJonesSource(BaseSource):
    slug = "jack-jones"
    brand_name = "Jack & Jones"
    seller_name = "Jack & Jones"
    website_url = BASE
    notes = "Server-rendered storefront: JSON state embedded in listing and product pages; robots.txt allows them."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        html = await self.http.get_text(f"{BASE}/tr-tr/{listing.path}?page={page}")
        results, total, start = _listing_results(extract_initial_state(html))
        refs = [
            ListingRef(url=BASE + record["url"], key=str(record["id"]), category_hint=listing.hint, data={})
            for record in results
            if record.get("id") and record.get("url")
        ]
        return Page(refs=refs, has_more=start + len(results) < total)

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        html = await self.http.get_text(ref.url)
        return normalize_page(html, ref.url)


def _listing_results(state: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    for key, value in state.items():
        if key.startswith("$getProducts") and isinstance(value, dict):
            results = [r for r in value.get("results") or [] if isinstance(r, dict)]
            return results, int(value.get("totalResults") or 0), int(value.get("startingFrom") or 0)
    return [], 0, 0


def normalize_page(html: str, url: str) -> NormalizedOffer:
    state = extract_initial_state(html)
    content = next((v for k, v in state.items() if k.startswith("$initialContentResponse-") and isinstance(v, dict)), None)
    product = (content or {}).get("product")
    if not isinstance(product, dict) or not product.get("id"):
        raise SkipItem("no-product-data")
    return normalize_product(product, url)


def gallery_urls(product: dict[str, Any]) -> list[str]:
    """Every `media[].imageUrl` in the site's order, cut-out (`.png`) shots first, at most 8 (see `normalize.images`)."""
    return order_gallery(
        clean_text(media["imageUrl"])
        for media in product.get("media", [])
        if isinstance(media, dict) and media.get("imageUrl")
    )


def normalize_product(product: dict[str, Any], url: str) -> NormalizedOffer:
    title = clean_text(product.get("name"))
    description = clean_text(product.get("description"))
    tags = [clean_text(tag) for tag in product.get("tags", []) if isinstance(tag, str)]

    category = map_category(
        title,
        product.get("ediSubCategory"),
        product.get("ediCategory"),
        product.get("englishCategory"),
        source="jack-jones",
    )
    if category is None:
        raise SkipItem("unknown-category")

    price_block: dict[str, Any] = product.get("price") or {}
    price = _amount(price_block.get("salesPrice"))
    if price is None or price <= 0:
        raise SkipItem("no-price")
    original = _original_price(price_block, price)

    images = gallery_urls(product)
    if not images:
        raise SkipItem("no-images")

    audience = map_audience(product.get("englishGender"), product.get("targetGroup"), *tags)
    if audience is None:
        raise SkipItem("unknown-audience")

    variants = [v for v in product.get("variants", []) if isinstance(v, dict)]
    in_stock = [v for v in variants if v.get("inStockEcom", v.get("inStock"))]
    sizes = normalize_sizes((v.get("size") or {}).get("name") for v in in_stock)

    colour = product.get("colour") or {}
    color = try_map_color((colour.get("base") or {}).get("name")) or map_color(colour.get("name"))

    style, colour_id = clean_text(product.get("styleNumber")), clean_text(colour.get("id"))
    model_code = f"{style}-{colour_id}" if style and colour_id else (style or None)

    name = strip_brand_prefix(strip_brand_prefix(title, "Jack & Jones"), "Jack&Jones")
    inferred = infer_attributes(
        category,
        name,
        description,
        " ".join(tags),
        material_texts=(product.get("fabricComposition"),),
    )

    return NormalizedOffer(
        external_id=str(product["id"]),
        url=url,
        title=name[:300],
        price=price,
        original_price=original,
        stock_status=StockStatus.IN_STOCK if in_stock else StockStatus.OUT_OF_STOCK,
        available_sizes=sizes,
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Jack & Jones",
            name=name[:200],
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


def _amount(block: object) -> Decimal | None:
    if isinstance(block, dict):
        block = block.get("amount")
    try:
        return Decimal(str(block))
    except (InvalidOperation, ValueError):
        return None


def _original_price(price_block: dict[str, Any], price: Decimal) -> Decimal | None:
    for key in _ORIGINAL_PRICE_KEYS:
        candidate = _amount(price_block.get(key))
        if candidate is not None and candidate > price:
            return candidate
    return None
