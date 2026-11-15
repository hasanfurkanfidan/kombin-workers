"""Koton (koton.com) - mass-market fashion.

Platform: Akinon ("Omnishop"). The storefront serves its own JSON when `?format=json` is appended to a listing or a
product URL, which is what its front end calls too. robots.txt disallows `/list/*`, `/shopping-page/*` and
`/*?category_ids=`; the category pages (`/erkek-gomlek/`) and product pages we use are allowed.

- Discovery: `/<gender>-<category>/?format=json&page=N`, 24 products a page, each one a colourway.
- Fetch: `<product url>?format=json` gives the per-size variants (with stock), all images, price and `retail_price`
  (the crossed-out price; equal to `price` when there is no discount) and the attribute set below.

Where each field comes from (see docs/sources.md):
- externalId / modelCode   `<base_code>-<colour code>`, e.g. 5SAK60396UW-031. `base_code` is Koton's style code (the
                           same one printed on the label); the colour code is the listing's `listing_code`.
- sizes                    the `integration_size_id` variant group, in-stock options only.
- audience                 attributes `filterable_gender` / `morhipo_cinsiyet`.
- fit/material/season      attributes `filterable_fit`, `filterable_kumas_tipi`, `integration_season_desc`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from kombin_workers.http import FetchError
from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.audience import map_audience
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color
from kombin_workers.normalize.images import order_gallery
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text, html_to_text, strip_brand_prefix
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem

BASE = "https://www.koton.com"
LOW_STOCK_UNITS = 3
_SIZE_SUFFIX = re.compile(r"(-\d{6,})-\d{1,2}/$")

LISTINGS = tuple(
    Listing(f"{gender}-{category}", slug)
    for gender in ("erkek", "kadin")
    for category, slug in (
        ("tisort", "ust-giyim/tisort"),
        ("gomlek", "ust-giyim/gomlek"),
        ("pantolon", "alt-giyim/pantolon"),
        ("mont", "dis-giyim/mont"),
        ("kaban", "dis-giyim/kaban"),
        ("blazer-ceket", "ust-giyim/blazer"),
        ("ceket", "dis-giyim/ceket"),
        ("etek", "alt-giyim/etek"),
        ("canta", "aksesuar/canta"),
        ("kemer", "aksesuar/kemer"),
    )
    # Only the combinations that exist on the site (probed): there is no men's skirt, no men's "ceket" listing.
    if (gender, category) not in {("erkek", "etek"), ("erkek", "ceket"), ("erkek", "canta")}
)


class KotonSource(BaseSource):
    slug = "koton"
    brand_name = "Koton"
    seller_name = "Koton"
    website_url = BASE
    notes = "Akinon storefront JSON (?format=json) for listings and products; robots.txt allows category and product pages."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        data = await self.http.get_json(f"{BASE}/{listing.path}/?format=json&page={page}")
        products: list[dict[str, Any]] = data.get("products", []) if isinstance(data, dict) else []
        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        refs = []
        for product in products:
            base_code, color = product.get("base_code"), product.get("listing_code")
            if not base_code or not color or not product.get("absolute_url"):
                continue
            refs.append(
                ListingRef(
                    url=canonical_url(product["absolute_url"]),
                    key=f"{base_code}-{color}",
                    category_hint=listing.hint,
                    data={"absolute_url": product["absolute_url"]},
                )
            )
        return Page(refs=refs, has_more=page < int(pagination.get("num_pages", 0)))

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        try:
            data = await self.http.get_json(f"{BASE}{ref.data['absolute_url']}?format=json")
        except FetchError as exc:
            if exc.status in (404, 410):
                raise SkipItem("gone") from exc
            raise
        return normalize_product(data, ref.url)


def canonical_url(absolute_url: str) -> str:
    """The product URL without the per-size suffix (`...-4085984-1/` -> `...-4085984/`)."""
    return BASE + _SIZE_SUFFIX.sub(r"\1/", absolute_url)


def normalize_product(data: dict[str, Any], url: str) -> NormalizedOffer:
    product: dict[str, Any] = data.get("product") or {}
    attributes: dict[str, Any] = product.get("attributes") or {}
    base_code = clean_text(product.get("base_code"))
    color_code = clean_text(attributes.get("integration_color_id"))
    if not base_code or not color_code:
        raise SkipItem("no-model-code")

    title = clean_text(product.get("name"))
    description = html_to_text(attributes.get("urun_aciklama"))
    category = map_category(
        title, attributes.get("filterable_category"), attributes.get("filterable_kategori"), source="koton"
    )
    if category is None:
        raise SkipItem("unknown-category")

    price = _decimal(product.get("price"))
    if price is None or price <= 0:
        raise SkipItem("no-price")
    retail = _decimal(product.get("retail_price"))
    original = retail if retail is not None and retail > price else None

    images = gallery_urls(product)
    if not images:
        raise SkipItem("no-images")

    in_stock_sizes, units = _sizes_in_stock(data.get("variants", []))
    if not in_stock_sizes:
        stock = StockStatus.OUT_OF_STOCK
    elif units is not None and units <= LOW_STOCK_UNITS:
        stock = StockStatus.LOW_STOCK
    else:
        stock = StockStatus.IN_STOCK

    audience = map_audience(attributes.get("filterable_gender"), attributes.get("morhipo_cinsiyet"))
    if audience is None:
        raise SkipItem("unknown-audience")

    name = strip_brand_prefix(title, "Koton")
    inferred = infer_attributes(
        category,
        name,
        description,
        attributes.get("filterable_fit"),
        attributes.get("filterable_kumas_tipi"),
        attributes.get("integration_season_desc"),
        material_texts=(attributes.get("filterable_kumas_tipi"), _composition_line(description)),
    )
    model_code = f"{base_code}-{color_code}".upper()

    return NormalizedOffer(
        external_id=model_code,
        url=url,
        title=name[:300],
        price=price,
        original_price=original,
        stock_status=stock,
        available_sizes=normalize_sizes(in_stock_sizes),
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Koton",
            name=name[:200],
            model_code=model_code,
            category_slug=category,
            audience=audience,
            primary_color=map_color(attributes.get("filterable_color") or attributes.get("variantable_color")),
            pattern=inferred.pattern,
            fit=inferred.fit,
            formality=inferred.formality,
            seasons=inferred.seasons,
            material=inferred.material,
            description=description[:4000] or None,
        ),
    )


def gallery_urls(product: dict[str, Any]) -> list[str]:
    """Every active `productimage_set` entry by `order`, packshot hints first, at most 8 (see `normalize.images`)."""
    return order_gallery(
        clean_text(image["image"])
        for image in sorted(product.get("productimage_set", []), key=lambda i: i.get("order", 0))
        if image.get("image") and image.get("status", "active") == "active"
    )


def _composition_line(description: str) -> str:
    """Lines of the description that spell out a fabric composition ("%98 Pamuk %2 Elastan")."""
    return " ".join(line for line in description.splitlines() if "%" in line)


def _sizes_in_stock(variant_groups: list[dict[str, Any]]) -> tuple[list[str], int | None]:
    """In-stock size labels of the size variant group, and their total unit count (None if unknown)."""
    for group in variant_groups:
        key = str(group.get("attribute_key", ""))
        if "size" not in key and "beden" not in key:
            continue
        labels: list[str] = []
        units = 0
        known = True
        for option in group.get("options", []):
            if not option.get("in_stock"):
                continue
            labels.append(str(option.get("label", "")))
            stock = (option.get("product") or {}).get("stock")
            if isinstance(stock, int):
                units += stock
            else:
                known = False
        return labels, (units if known else None)
    return [], None


def _decimal(raw: object) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
