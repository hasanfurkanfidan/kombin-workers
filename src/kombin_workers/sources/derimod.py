"""Derimod (derimod.com.tr) - leather shoes, bags, belts and leather outerwear.

Platform: Shopify. Its storefront JSON is public and linked from nowhere but meant for themes/apps:
`/collections/<handle>/products.json?limit=..&page=..` returns whole products (variants with price, compare_at_price,
availability; images; tags; vendor). robots.txt allows it. Discovery and fetch are therefore the same request: a
collection page *is* the data, so `fetch` never touches the network.

Where each field comes from (see docs/sources.md):
- externalId     Shopify product id (a Derimod product is one colourway).
- modelCode      `<style tag>-<colour code>`, e.g. WM790004DN-5637145360. The style (WM790004DN) is a tag; the colour code
                 is the trailing number of the product handle.
- price/stock    variants: `price`, `compare_at_price` ("0.00" = not crossed out), `available`.
- sizes          the variants' "Beden" option, available ones only.
- audience       `feed-gender-*` / "Erkek" / "Kadın" tags.
Derimod also resells other brands (vendor != "Derimod"); those items are skipped.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from kombin_workers.models import (
    Audience,
    NormalizedOffer,
    NormalizedProduct,
    PrimaryColor,
    StockStatus,
)
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.audience import map_audience
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color, try_map_color
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text, html_to_text, strip_brand_prefix
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem

BASE = "https://derimod.com.tr"
PAGE_SIZE = 100
_STYLE_TAG = re.compile(r"^[A-Z]{1,3}\d{5,}[A-Z]{0,3}$")
_COLOR_CODE = re.compile(r"-(\d{8,12})$")

LISTINGS = (
    Listing("erkek-deri-sneaker", "ayakkabi/sneaker"),
    Listing("kadin-deri-sneaker", "ayakkabi/sneaker"),
    Listing("erkek-bot", "ayakkabi/bot"),
    Listing("kadin-bot", "ayakkabi/bot"),
    Listing("erkek-loafer", "ayakkabi/loafer"),
    Listing("kadin-loafer", "ayakkabi/loafer"),
    Listing("erkek-deri-ceket", "dis-giyim/ceket"),
    Listing("kadin-deri-ceket", "dis-giyim/ceket"),
    Listing("erkek-deri-mont", "dis-giyim/mont"),
    Listing("kadin-deri-mont", "dis-giyim/mont"),
    Listing("erkek-canta", "aksesuar/canta"),
    Listing("kadin-canta", "aksesuar/canta"),
    Listing("erkek-kemer", "aksesuar/kemer"),
)


class DerimodSource(BaseSource):
    slug = "derimod"
    brand_name = "Derimod"
    seller_name = "Derimod"
    website_url = BASE
    notes = "Shopify storefront JSON (collections/<handle>/products.json); robots.txt allows it. Resold brands are skipped."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        url = f"{BASE}/collections/{listing.path}/products.json?limit={PAGE_SIZE}&page={page}"
        data = await self.http.get_json(url)
        products: list[dict[str, Any]] = data.get("products", []) if isinstance(data, dict) else []
        refs = [
            ListingRef(
                url=f"{BASE}/products/{product['handle']}",
                key=str(product["id"]),
                category_hint=listing.hint,
                data=product,
            )
            for product in products
        ]
        return Page(refs=refs, has_more=len(products) >= PAGE_SIZE)

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        return normalize_product(dict(ref.data), ref.url)


def normalize_product(product: dict[str, Any], url: str) -> NormalizedOffer:
    """One Shopify product record -> `NormalizedOffer`; raises `SkipItem` for items we do not take."""
    vendor = clean_text(product.get("vendor"))
    if vendor.casefold() != "derimod":
        raise SkipItem("other-brand")

    title = clean_text(product.get("title"))
    tags = [clean_text(tag) for tag in product.get("tags", [])]
    body_text = html_to_text(product.get("body_html"))

    category = map_category(title, product.get("product_type"), source="derimod")
    if category is None:
        raise SkipItem("unknown-category")

    variants: list[dict[str, Any]] = product.get("variants", [])
    if not variants:
        raise SkipItem("no-variants")
    option_names = [clean_text(option.get("name")).casefold() for option in product.get("options", [])]
    size_key = f"option{option_names.index('beden') + 1}" if "beden" in option_names else None
    color_key = f"option{option_names.index('renk') + 1}" if "renk" in option_names else None

    available = [variant for variant in variants if variant.get("available")]
    reference = available[0] if available else variants[0]
    price = _decimal(reference.get("price"))
    if price is None or price <= 0:
        raise SkipItem("no-price")
    compare_at = _decimal(reference.get("compare_at_price"))
    original = compare_at if compare_at is not None and compare_at > price else None

    images = [clean_text(image.get("src")) for image in product.get("images", []) if image.get("src")]
    if not images:
        raise SkipItem("no-images")

    audience = map_audience(*tags, title) or Audience.UNISEX
    color = _color(reference.get(color_key) if color_key else None, tags, title)

    style = next((tag for tag in tags if _STYLE_TAG.match(tag)), None)
    color_code = _COLOR_CODE.search(str(product.get("handle", "")))
    model_code = f"{style}-{color_code.group(1)}" if style and color_code else style

    attributes = infer_attributes(category, title, body_text, " ".join(tags), material_texts=(title, " ".join(tags)))
    sizes = normalize_sizes(variant.get(size_key) for variant in available) if size_key else []

    return NormalizedOffer(
        external_id=str(product["id"]),
        url=url,
        title=strip_brand_prefix(title, "Derimod")[:300],
        price=price,
        original_price=original,
        stock_status=StockStatus.IN_STOCK if available else StockStatus.OUT_OF_STOCK,
        available_sizes=sizes,
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Derimod",
            name=strip_brand_prefix(title, "Derimod")[:200],
            model_code=model_code,
            category_slug=category,
            audience=audience,
            primary_color=color,
            pattern=attributes.pattern,
            fit=attributes.fit,
            formality=attributes.formality,
            seasons=attributes.seasons,
            material=attributes.material,
            description=body_text[:4000] or None,
        ),
    )


def _decimal(raw: object) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _color(option_value: object, tags: list[str], title: str) -> PrimaryColor:
    for candidate in (option_value, title, *tags):
        found = try_map_color(str(candidate)) if candidate else None
        if found is not None:
            return found
    return map_color(None)
