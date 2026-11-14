"""Colin's (colins.com.tr) - Turkish jeans and casual wear, women and men.

Platform: server-rendered ASP.NET storefront. There is no JSON endpoint and no embedded state, but every listing card
carries its own data as attributes - `data-ga` (analytics record: colour variant, price) and `data-variants` (JSON: url,
images, size ids and names, price, old price) - and product pages have `application/ld+json` `Product` markup.
robots.txt disallows `/urunler/`, `/markalar/`, `/kampanyalar/`, `/cart`, `/checkout`, the account paths and any URL
containing `search`, `price`, `term` or `orderby`; category pages (`/c/...`) and product pages (`/p/...`) are allowed, and
we only add `?pagenumber=N`.

- Discovery: `/c/<category>-<id>?pagenumber=N`, 48 cards a page; the card is the colourway (its `id` is the product id).
- Fetch: the product page, for what a card does not have: `sku`, the in-stock sizes (`<option class="have-stock">`),
  availability and the breadcrumb.

Where each field comes from (see docs/sources.md):
- externalId     the card / product id (the URL ends in `-<id>`).
- modelCode      `<style>-<colour>` from the ld+json `sku` `CL1076502_Q1.V1_PGR`: the part before the first `_` and the
                 part after the last `_`.
- price          ld+json `offers.price`; crossed-out price = the card's `oldPrice` ("1.999,90 TL") when higher.
- sizes / stock  `<option class="have-stock">` of the size select; a page with none is sold out (the ld+json
                 `availability` is InStock either way, and the add-to-cart button is disabled until a size is chosen).
- colour         `data-ga.variant` ("Yeşil"); category: breadcrumb ("ERKEK UZUN KOL GÖMLEK") and the name.
- audience       the name and the breadcrumb ("Erkek ..." / "Kadın ...").
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from kombin_workers.http import FetchError
from kombin_workers.models import NormalizedOffer, NormalizedProduct, StockStatus
from kombin_workers.normalize.attributes import infer_attributes
from kombin_workers.normalize.categories import map_category
from kombin_workers.normalize.colors import map_color
from kombin_workers.normalize.sizes import normalize_sizes
from kombin_workers.normalize.text import clean_text
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience
from kombin_workers.sources.embedded import find_ld_type

BASE = "https://www.colins.com.tr"
BRAND = "Colin's"
LOW_STOCK_UNITS = 2  # sizes left; the page does not say how many pieces

# Women's and men's listings alternate so a capped run takes an even share of both.
LISTINGS = (
    Listing("c/kadin-gomlek-968", "ust-giyim/gomlek"),
    Listing("c/erkek-gomlek-1202", "ust-giyim/gomlek"),
    Listing("c/kadin-tisort-629", "ust-giyim/tisort"),
    Listing("c/erkek-tisort-630", "ust-giyim/tisort"),
    Listing("c/kadin-kazak-1211", "ust-giyim/triko"),
    Listing("c/erkek-kazak-1212", "ust-giyim/triko"),
    Listing("c/kadin-hirka-181", "ust-giyim/triko"),
    Listing("c/erkek-hirka-169", "ust-giyim/triko"),
    Listing("c/kadin-pantolon-209", "alt-giyim/pantolon"),
    Listing("c/erkek-pantolon-201", "alt-giyim/pantolon"),
    Listing("c/kadin-jean-pantolon-1094", "alt-giyim/jean"),
    Listing("c/erkek-jean-pantolon-1107", "alt-giyim/jean"),
    Listing("c/etek-239", "alt-giyim/etek"),
    Listing("c/kadin-ceket-211", "dis-giyim/ceket"),
    Listing("c/erkek-ceket-245", "dis-giyim/ceket"),
    Listing("c/kadin-mont-185", "dis-giyim/mont"),
    Listing("c/erkek-mont-243", "dis-giyim/mont"),
    Listing("c/kadin-kaban-259", "dis-giyim/kaban"),
    Listing("c/erkek-kaban-250", "dis-giyim/kaban"),
    Listing("c/kadin-canta-159", "aksesuar/canta"),
    Listing("c/erkek-canta-163", "aksesuar/canta"),
    Listing("c/kadin-kemer-157", "aksesuar/kemer"),
    Listing("c/erkek-kemer-168", "aksesuar/kemer"),
)


def extract_cards(html: str) -> list[dict[str, Any]]:
    """One dict per listing card: the `data-variants` entry plus the card's colour (`data-ga.variant`)."""
    cards: list[dict[str, Any]] = []
    for box in LexborHTMLParser(html).css("div.productbox"):
        try:
            variants = json.loads(box.attributes.get("data-variants") or "[]")
            analytics = json.loads(box.attributes.get("data-ga") or "{}")
        except ValueError:
            continue
        if not variants or not isinstance(variants[0], dict) or not variants[0].get("url"):
            continue
        card = dict(variants[0])
        card["colour"] = clean_text(analytics.get("variant")) if isinstance(analytics, dict) else ""
        cards.append(card)
    return cards


class ColinsSource(BaseSource):
    slug = "colins"
    brand_name = BRAND
    seller_name = BRAND
    website_url = BASE
    notes = "Server-rendered: listing cards carry data attributes, product pages carry ld+json; robots.txt allows /c/ and /p/."

    async def discover(self) -> AsyncIterator[ListingRef]:
        async for ref in self.paged_discovery(LISTINGS, self._read_page):
            yield ref

    async def _read_page(self, listing: Listing, page: int) -> Page:
        suffix = f"?pagenumber={page}" if page > 1 else ""
        html = await self.http.get_text(f"{BASE}/{listing.path}{suffix}")
        refs = [
            ListingRef(url=BASE + card["url"], key=str(card["id"]), category_hint=listing.hint, data=card)
            for card in extract_cards(html)
            if card.get("id")
        ]
        return Page(refs=refs, has_more=f"pagenumber={page + 1}" in html)

    async def fetch(self, ref: ListingRef) -> NormalizedOffer | None:
        try:
            html = await self.http.get_text(ref.url)
        except FetchError as exc:
            if exc.status in (404, 410):
                raise SkipItem("gone") from exc
            raise
        return normalize_product(dict(ref.data), html, ref.url)


def normalize_product(card: dict[str, Any], html: str, url: str) -> NormalizedOffer:
    ld = find_ld_type(html, "Product")
    if ld is None:
        raise SkipItem("no-product-data")
    tree = LexborHTMLParser(html)

    title = clean_text(card.get("name") or ld.get("name"))
    crumbs = [clean_text(a.text()) for a in tree.css("ol.breadcrumb li a") if clean_text(a.text())]
    category = map_category(*crumbs[1:-1][::-1], title, source="colins")
    if category is None:
        raise SkipItem("unknown-category")
    audience = adult_audience(*crumbs[1:2], title)

    offer = ld.get("offers")
    if isinstance(offer, list):
        offer = offer[0] if offer else None
    price = _money((offer or {}).get("price"))
    if price is None:
        raise SkipItem("no-price")
    old = _tr_price(card.get("oldPrice"))
    original = old if old is not None and old > price else None

    images = [clean_text(i) for i in ld.get("image") or [] if isinstance(i, str)] or [
        clean_text(i) for i in card.get("imageUrls") or [] if isinstance(i, str)
    ]
    if not images:
        raise SkipItem("no-images")

    sizes = normalize_sizes(
        clean_text(option.text()) for option in tree.css("select.size-select-list-radio option.have-stock")
    )
    # The ld+json `availability` says InStock even on sold-out pages, and the add-to-cart button is disabled until a
    # size is picked; the size select is what tells: a page with no in-stock size option has nothing to sell.
    if not sizes:
        stock = StockStatus.OUT_OF_STOCK
    elif len(sizes) <= LOW_STOCK_UNITS and len(sizes) < len(card.get("sizes") or sizes):
        stock = StockStatus.LOW_STOCK
    else:
        stock = StockStatus.IN_STOCK

    sku = clean_text(ld.get("sku"))
    style, _, _ = sku.partition("_")
    colour_code = sku.rpartition("_")[2] if "_" in sku else ""
    model_code = f"{style}-{colour_code}" if style and colour_code else (sku or None)

    description = clean_text(ld.get("description"))
    inferred = infer_attributes(category, title, material_texts=(description,))

    return NormalizedOffer(
        external_id=str(card.get("id") or url.rsplit("-", 1)[-1]),
        url=url,
        title=title[:300],
        price=price,
        original_price=original,
        stock_status=stock,
        available_sizes=sizes,
        image_urls=images,
        product=NormalizedProduct(
            brand_name=BRAND,
            name=title[:200],
            model_code=model_code,
            category_slug=category,
            audience=audience,
            primary_color=map_color(card.get("colour")),
            pattern=inferred.pattern,
            fit=inferred.fit,
            formality=inferred.formality,
            seasons=inferred.seasons,
            material=inferred.material,
            description=description[:4000] or None,
        ),
    )


_TR_PRICE_JUNK = re.compile(r"[^\d,.]")


def _tr_price(raw: object) -> Decimal | None:
    """'1.999,90 TL' -> 1999.90 (None for empty / unreadable)."""
    if not raw:
        return None
    text = _TR_PRICE_JUNK.sub("", str(raw)).replace(".", "").replace(",", ".")
    return _money(text)


def _money(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None
