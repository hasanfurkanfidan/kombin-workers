"""Twist (twist.com.tr) - Turkish women's fashion (İpekyol Group). Womenswear only.

Platform: Next.js. Category pages and product pages both ship `__NEXT_DATA__`.

- Discovery: robots.txt disallows `?page=*` / `&page=` (and size, colour, sort filters), so category pages can only be
  read once (their first page, 24 products). The way past page one is the sitemap robots.txt lists (`/sitemap.xml` ->
  `products-1.xml`, ~1260 `/urun/<slug>` URLs, which robots.txt allows): the product sitemap is read (2 requests),
  slugs that name a category we do not have (dress, blouse, earrings ...) are dropped *before* any product request, and
  the rest are dealt out one category at a time so the per-source cap takes an even share. If the sitemap cannot be read
  the source falls back to the first page of every category.
- Fetch: `/urun/<routePath>` - `__NEXT_DATA__.props.pageProps.data.response.product` has the per-size barcodes with stock,
  price, images (`documents`), attributes.

Where each field comes from (see docs/sources.md):
- externalId     `sku` (e.g. TW6260025124003): one colourway.
- modelCode      `<style>-<colour code>`: the sku without its last three digits, a dash, `attr_ColorCode`.
- price          `salesPrice`; crossed-out price = `basePrice` when higher.
- sizes / stock  `barcodes[].stockTypeValues[0].name` where `stocksByBarcode[barcode id] > 0`.
- category       `categories[0].name` ("Gömlek") and the `attr_UrunAltGrubu` attribute.
- images         `documents[]` (type Image) on `https://cdn.twist.com.tr/mnresize/2400/-`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
from typing import Any
from urllib.parse import urlsplit

import structlog

from kombin_workers.http import FetchError, RobotsBlocked, SourceAborted
from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color, try_map_color
from kombin_workers.normalize.images import order_gallery
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text, title_case_tr
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience
from kombin_workers.sources.embedded import dig, extract_ld_json, extract_next_data
from kombin_workers.sources.sitemap import parse_sitemap

log = structlog.get_logger(__name__)

BASE = "https://www.twist.com.tr"
IMAGE_BASE = "https://cdn.twist.com.tr/mnresize/2400/-"
LOW_STOCK_UNITS = 3

# First page only (see the module docstring). Dresses, blouses, sweatshirts and vests have no category in the taxonomy.
LISTINGS = (
    Listing("kadin-gomlek-modelleri", "ust-giyim/gomlek"),
    Listing("kadin-tshirt-modelleri", "ust-giyim/tisort"),
    Listing("kadin-triko-kazak", "ust-giyim/triko"),
    Listing("kadin-hirka-modeller", "ust-giyim/triko"),
    Listing("kadin-pantolon-modelleri", "alt-giyim/pantolon"),
    Listing("kot-pantolon-modelleri", "alt-giyim/jean"),
    Listing("etek-modelleri", "alt-giyim/etek"),
    Listing("kadin-ceket-modelleri", "dis-giyim/ceket"),
    Listing("kadin-dis-giyim", "dis-giyim/mont"),
    Listing("aksesuar/kadin-canta-modelleri", "aksesuar/canta"),
)


class TwistSource(BaseSource):
    slug = "twist"
    brand_name = "Twist"
    seller_name = "Twist"
    website_url = BASE
    notes = "Next.js (__NEXT_DATA__); robots.txt forbids ?page=, so products come from the product sitemap."

    async def discover(self) -> AsyncIterator[ListingRef]:
        refs = await self._sitemap_refs()
        if refs:
            for ref in refs:
                yield ref
            return
        log.warning("twist_sitemap_unavailable_using_category_pages")
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _sitemap_refs(self) -> list[ListingRef]:
        """Product refs from the sitemaps listed in robots.txt, balanced by the category their slug names; [] if none."""
        try:
            roots = (await self.http.robots_for(BASE)).sitemaps or [f"{BASE}/sitemap.xml"]
            urls: list[str] = []
            for root in roots:
                urls.extend(await self._product_urls(root))
        except SourceAborted:
            raise
        except (FetchError, RobotsBlocked) as exc:
            log.warning("twist_sitemap_failed", error=str(exc))
            return []
        buckets: dict[str, list[ListingRef]] = {}
        for url in dict.fromkeys(urls):
            slug = url.rsplit("/", 1)[-1]
            hint = map_category(slug.replace("-", " "), source="twist")
            if hint is None or not await self.http.is_allowed(url):
                continue  # a category we do not have, or a URL robots.txt disallows
            buckets.setdefault(hint, []).append(ListingRef(url=url, key=slug, category_hint=hint))
        # one product of each category in turn, so a capped run is an even share of every category
        return [ref for round_ in zip_longest(*buckets.values()) for ref in round_ if ref is not None]

    async def _product_urls(self, sitemap_url: str, depth: int = 0) -> list[str]:
        """`/urun/` URLs of a sitemap; an index is followed one level down, into product sitemaps only."""
        if urlsplit(sitemap_url).hostname != urlsplit(BASE).hostname:
            return []
        sitemap = parse_sitemap(await self.http.get_text(sitemap_url))
        found = [u for u in sitemap.urls if urlsplit(u).path.startswith("/urun/")]
        if depth == 0:
            for child in sitemap.children:
                if "product" in urlsplit(child).path.lower():
                    found.extend(await self._product_urls(child, depth + 1))
        return found

    async def _read_page(self, listing: Listing, page: int) -> Page:
        if page > 1:  # `?page=` is disallowed by robots.txt
            return Page(refs=[], has_more=False)
        html = await self.http.get_text(f"{BASE}/{listing.path}")
        products = dig(extract_next_data(html), "props", "pageProps", "data", "response", "products") or []
        refs = [
            ListingRef(url=f"{BASE}/urun/{p['routePath']}", key=str(p["sku"]), category_hint=listing.hint)
            for p in products
            if isinstance(p, dict) and p.get("routePath") and p.get("sku")
        ]
        return Page(refs=refs, has_more=False)

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        try:
            html = await self.http.get_text(ref.url)
        except FetchError as exc:
            if exc.status in (404, 410):
                raise SkipItem("gone") from exc
            raise
        return normalize_page(html, ref.url)


def normalize_page(html: str, url: str) -> NormalizedOffer:
    product = dig(extract_next_data(html), "props", "pageProps", "data", "response", "product")
    if not isinstance(product, dict) or not product.get("sku"):
        raise SkipItem("no-product-data")
    ld = next((n for n in extract_ld_json(html) if n.get("@type") == "Product"), {})
    return normalize_product(product, url, ld)


def normalize_product(product: dict[str, Any], url: str, ld: dict[str, Any] | None = None) -> NormalizedOffer:
    ld = ld or {}
    attributes = _attributes(product)
    sku = clean_text(product.get("sku"))
    title = title_case_tr(product.get("name") or "")  # the site writes names in capitals
    description = clean_text(product.get("routeDescription") or ld.get("description"))

    categories = [clean_text(c.get("name")) for c in product.get("categories") or [] if isinstance(c, dict)]
    category = map_category(*categories[:1], attributes.get("attr_UrunAltGrubu"), title, source="twist")
    if category is None:
        raise SkipItem("unknown-category")

    audience = adult_audience(attributes.get("gender"), dig(ld, "audience", "suggestedGender"))

    price = _money(product.get("salesPrice"))
    if price is None:
        raise SkipItem("no-price")
    base = _money(product.get("basePrice"))
    original = base if base is not None and base > price else None

    images = gallery_urls(product)
    if not images:
        raise SkipItem("no-images")

    in_stock_sizes, units = _sizes_in_stock(product)
    if not in_stock_sizes:
        stock = StockStatus.OUT_OF_STOCK
    elif units <= LOW_STOCK_UNITS:
        stock = StockStatus.LOW_STOCK
    else:
        stock = StockStatus.IN_STOCK

    colour_name = (attributes.get("color") or attributes.get("attr_FilterColor") or clean_text(product.get("shortName")))
    color = try_map_color(attributes.get("attr_FilterColor")) or map_color(colour_name)

    colour_code = attributes.get("attr_ColorCode") or ""
    style = sku[: -len(colour_code)] if colour_code and sku.endswith(colour_code) else sku
    model_code = f"{style}-{colour_code}" if colour_code and style else sku

    material = clean_text(ld.get("material"))
    inferred = infer_attributes(category, title, description, material_texts=(material, attributes.get("attr_PrdKumasTipi")))

    return NormalizedOffer(
        external_id=sku,
        url=url,
        title=title[:300],
        price=price,
        original_price=original,
        stock_status=stock,
        available_sizes=normalize_sizes(in_stock_sizes),
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Twist",
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


def _attributes(product: dict[str, Any]) -> dict[str, str]:
    """`integrationCode -> first value` of the product's attribute list."""
    result: dict[str, str] = {}
    for entry in product.get("attributes") or []:
        if not isinstance(entry, dict):
            continue
        code = dig(entry, "attribute", "integrationCode")
        values = [clean_text(v.get("value")) for v in entry.get("attributeValues") or [] if isinstance(v, dict)]
        texts = [clean_text(t) for t in entry.get("attributeValueTexts") or []]
        value = next((v for v in (*values, *texts) if v), "")
        if code and value:
            result.setdefault(str(code), value)
    return result


def gallery_urls(product: dict[str, Any]) -> list[str]:
    """Every Image `documents[]` entry (default first, then `order`); `Stillife` shots first, at most 8 (see `normalize.images`)."""
    documents = [
        d for d in product.get("documents") or [] if isinstance(d, dict) and d.get("filePath") and d.get("documentType") == "Image"
    ]
    documents.sort(key=lambda d: (not d.get("isDefault"), int(d.get("order") or 0)))
    return order_gallery(f"{IMAGE_BASE}{d['filePath']}" for d in documents)


def _sizes_in_stock(product: dict[str, Any]) -> tuple[list[str], int]:
    stocks: dict[str, Any] = product.get("stocksByBarcode") or {}
    labels: list[str] = []
    units = 0
    for barcode in product.get("barcodes") or []:
        if not isinstance(barcode, dict):
            continue
        quantity = stocks.get(str(barcode.get("id")), 0)
        label = dig(barcode, "stockTypeValues", 0, "name")
        if isinstance(quantity, int) and quantity > 0 and label:
            labels.append(str(label))
            units += quantity
    return labels, units


def _money(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None
