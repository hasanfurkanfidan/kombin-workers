"""Network (network.com.tr) - Turkish premium-casual fashion, women and men (Ay Marka Mağazacılık).

Platform: a server-rendered storefront. Every listing page embeds the *complete* record of each of its 24 products as
JSON (`window.defaultProductList[...] = {...{"ID":223002,...}}`): price, strike-through price, per-size stock, images,
colour, gender, category path. So `fetch` needs no request - a listing page is also the product page, like Derimod.

robots.txt disallows `/product/`, `/search`, `/api/`, `/cart/` and filter / sort query strings (`*filter=*`, `*orderOption=*`,
...). Product pages are `/<slug>-p-<id>` (not under `/product/`) and category pages are `/<slug>-<id>`; neither is
disallowed, and we only add `?page=N`.

Where each field comes from (see docs/sources.md):
- externalId     the record `ID` (one colourway; the URL ends in `-p-<ID>`).
- modelCode      `ExternalSystemCode`, e.g. `1099332-536` (style code - colour code).
- price          `ActualPriceToShowOnScreen`; crossed-out price = `StrikeThroughPriceToShowOnScreen` when higher.
                 (`ProductPromotion.PromotedPrice` is a basket-only campaign price and is ignored.)
- sizes / stock  `VariantWithStockList[].StockExists`.
- audience       `GenderAttribute`; category `good-type-description` / the last part of `CategoryLevelDisplayName`.
- images         `MediaList[].ProductMediaFileDOList[].ImageResizePath` with the size placeholders filled in (a few file
                 names contain a space, which is percent-encoded).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color, try_map_color
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience

BASE = "https://www.network.com.tr"
IMAGE_WIDTH = "1000"

# Women's and men's listings alternate so a capped run takes an even share of both. The hint is only a hint.
LISTINGS = (
    Listing("kadin-gomlek-cesitleri-698", "ust-giyim/gomlek"),
    Listing("erkek-gomlek-cesitleri-710", "ust-giyim/gomlek"),
    Listing("network-kadin-t-shirt-1343", "ust-giyim/tisort"),
    Listing("erkek-t-shirt-cesitleri-711", "ust-giyim/tisort"),
    Listing("kadin-kazak-cesitleri-1325", "ust-giyim/triko"),
    Listing("network-erkek-kazak-1326", "ust-giyim/triko"),
    Listing("kadin-pantolon-modelleri-703", "alt-giyim/pantolon"),
    Listing("pantolon-718", "alt-giyim/pantolon"),
    Listing("kadin-jean-pantolon-704", "alt-giyim/jean"),
    Listing("erkek-jean-pantolon-719", "alt-giyim/jean"),
    Listing("kadin-etek-cesitleri-706", "alt-giyim/etek"),
    Listing("kadin-ceket-modelleri-700", "dis-giyim/ceket"),
    Listing("erkek-ceket-cesitleri-713", "dis-giyim/ceket"),
    Listing("kadin-mont-modelleri-712", "dis-giyim/mont"),
    Listing("erkek-mont-modelleri-724", "dis-giyim/mont"),
    Listing("kadin-kaban-modelleri-715", "dis-giyim/kaban"),
    Listing("erkek-kaban-cesitleri-726", "dis-giyim/kaban"),
    Listing("kadin-bot-2218", "ayakkabi/bot"),
    Listing("erkek-bot-modelleri-1339", "ayakkabi/bot"),
    Listing("kadin-loafer-1953", "ayakkabi/loafer"),
    Listing("network-erkek-loafer-1487", "ayakkabi/loafer"),
    Listing("sneaker-1349", "ayakkabi/sneaker"),
    Listing("erkek-sneaker-modelleri-1350", "ayakkabi/sneaker"),
)

_RECORD_START = re.compile(r'\.\.\.\{"ID":')
_NEXT_LINK = re.compile(r'<link rel="next"')


def extract_records(html: str) -> list[dict[str, Any]]:
    """Every product record embedded in a listing page, once each (the page repeats them in two script blocks)."""
    decoder = json.JSONDecoder()
    records: dict[str, dict[str, Any]] = {}
    for match in _RECORD_START.finditer(html):
        try:
            record, _ = decoder.raw_decode(html, match.end() - len('{"ID":'))
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("ID") is not None:
            records.setdefault(str(record["ID"]), record)
    return list(records.values())


class NetworkSource(BaseSource):
    slug = "network"
    brand_name = "Network"
    seller_name = "Network"
    website_url = BASE
    notes = "Listing pages embed full product JSON (24 a page); robots.txt only disallows /product/, search and filters."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        html = await self.http.get_text(f"{BASE}/{listing.path}?page={page}")
        refs = []
        for record in extract_records(html):
            uri = clean_text(record.get("SeoFriendlyProductUri"))
            if not uri:
                continue
            refs.append(ListingRef(url=f"{BASE}/{uri}", key=str(record["ID"]), category_hint=listing.hint, data=record))
        return Page(refs=refs, has_more=bool(_NEXT_LINK.search(html)))

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        return normalize_product(dict(ref.data), ref.url)


def normalize_product(record: dict[str, Any], url: str) -> NormalizedOffer:
    attributes = _attributes(record)
    title = clean_text(record.get("DisplayName"))
    description = clean_text(record.get("Description"))

    category = map_category(
        attributes.get("good-type-description"),
        _last_segment(record.get("CategoryLevelDisplayName")),
        title,
        source="network",
    )
    if category is None:
        raise SkipItem("unknown-category")

    if _truthy(record.get("IsSaleForbidden")) or _truthy(record.get("IsWebSaleForbidden")):
        raise SkipItem("not-for-sale")
    price = _money(record.get("ActualPriceToShowOnScreen")) or _money(record.get("ActualPrice"))
    if price is None:
        raise SkipItem("no-price")
    strike = _money(record.get("StrikeThroughPriceToShowOnScreen"))
    original = strike if strike is not None and strike > price else None

    images = _images(record)
    if not images:
        raise SkipItem("no-images")

    audience = adult_audience(record.get("GenderAttribute"), attributes.get("gender"))

    sizes_in_stock = [
        v.get("ValueText") for v in record.get("VariantWithStockList") or [] if isinstance(v, dict) and _truthy(v.get("StockExists"))
    ]
    sizes = normalize_sizes(sizes_in_stock)
    if not sizes:
        stock = StockStatus.OUT_OF_STOCK
    elif _truthy(record.get("HasLowStock")):
        stock = StockStatus.LOW_STOCK
    else:
        stock = StockStatus.IN_STOCK

    colour_name = clean_text(record.get("ColorAttribute") or attributes.get("color"))
    color = try_map_color(attributes.get("color-group")) or map_color(colour_name)

    model_code = clean_text(record.get("ExternalSystemCode")) or None
    inferred = infer_attributes(
        category,
        title,
        description,
        attributes.get("fit"),
        attributes.get("pattern-name"),
        record.get("SeasonAttribute"),
        material_texts=(),
    )

    return NormalizedOffer(
        external_id=str(record["ID"]),
        url=url,
        title=title[:300],
        price=price,
        original_price=original,
        stock_status=stock,
        available_sizes=sizes,
        image_urls=images,
        product=NormalizedProduct(
            brand_name="Network",
            name=title[:200],
            model_code=model_code,
            category_slug=category,
            audience=audience,
            primary_color=color,
            pattern=inferred.pattern,
            fit=inferred.fit,
            formality=inferred.formality,
            seasons=inferred.seasons,
            material=None,
            description=description[:4000] or None,
        ),
    )


def _attributes(record: dict[str, Any]) -> dict[str, str]:
    """`CodeName -> ValueText` of the record's attribute list (first value wins)."""
    result: dict[str, str] = {}
    for attribute in record.get("NonVariantAttributeList") or record.get("AttributeList") or []:
        if isinstance(attribute, dict) and attribute.get("CodeName"):
            result.setdefault(str(attribute["CodeName"]), clean_text(attribute.get("ValueText")))
    return result


def _images(record: dict[str, Any]) -> list[str]:
    media = [m for m in record.get("MediaList") or [] if isinstance(m, dict)]
    urls: list[str] = []
    for item in sorted(media, key=lambda m: int(m.get("DisplayOrder") or 0)):
        for file in item.get("ProductMediaFileDOList") or []:
            template = file.get("ImageResizePath") if isinstance(file, dict) else None
            if template:
                urls.append(str(template).replace("{width}", IMAGE_WIDTH).replace("{height}", "-").replace(" ", "%20"))
                break
    return urls


def _last_segment(path: object) -> str:
    parts = [part for part in str(path or "").split("/") if part.strip()]
    return parts[-1] if parts else ""


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _money(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None
