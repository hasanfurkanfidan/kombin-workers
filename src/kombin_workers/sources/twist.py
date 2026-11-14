"""Twist (twist.com.tr) - Turkish women's fashion (İpekyol Group). Womenswear only.

Platform: Next.js. Category pages and product pages both ship `__NEXT_DATA__`.

- Discovery: robots.txt disallows `?page=*` / `&page=` (and size, colour, sort filters), so a category is read **once**:
  its first page, 24 products. That is the most a polite crawler can take from a category page; the sitemap
  (`/sitemap.xml`, listed in robots.txt) would be the way to reach the rest in a later task.
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
from typing import Any

from kombin_workers.http import FetchError
from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color, try_map_color
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text, title_case_tr
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience
from kombin_workers.sources.embedded import dig, extract_ld_json, extract_next_data

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
    notes = "Next.js (__NEXT_DATA__); robots.txt forbids ?page=, so one page (24 products) per category."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

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

    images = _images(product)
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


def _images(product: dict[str, Any]) -> list[str]:
    documents = [
        d for d in product.get("documents") or [] if isinstance(d, dict) and d.get("filePath") and d.get("documentType") == "Image"
    ]
    documents.sort(key=lambda d: (not d.get("isDefault"), int(d.get("order") or 0)))
    return [f"{IMAGE_BASE}{d['filePath']}" for d in documents]


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
