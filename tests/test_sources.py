"""Each source's discovery and `fetch` against saved real responses (tests/fixtures/<source>/)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import respx

from kombin_workers.http import PoliteClient, RobotsBlocked
from kombin_workers.models import Audience, Fit, Pattern, PrimaryColor, Season, StockStatus
from kombin_workers.sources import derimod, jackjones, koton
from kombin_workers.sources.base import Listing, ListingRef, SkipItem
from kombin_workers.sources.registry import SOURCE_CLASSES, UnknownSourceError, build_source, runnable_slugs
from tests.conftest import allow_robots, fixture_json, fixture_text

# ================================================================================================= Derimod


def derimod_products() -> list[dict[str, Any]]:
    return copy.deepcopy(fixture_json("derimod/collection_erkek-deri-sneaker.json")["products"])


def test_derimod_normalizes_a_shopify_product() -> None:
    product = derimod_products()[0]
    offer = derimod.normalize_product(product, f"{derimod.BASE}/products/{product['handle']}")

    assert offer.external_id == "10266652148025"
    assert offer.title.startswith("Erkek Kahverengi")
    assert str(offer.price) == "5499.00"
    assert offer.original_price is None
    assert offer.stock_status is StockStatus.IN_STOCK
    assert offer.available_sizes == ["41", "42"]  # only the variants that are available
    assert 1 <= len(offer.image_urls) <= 6
    assert offer.url.endswith(product["handle"])

    info = offer.product
    assert info.brand_name == "Derimod"
    assert info.model_code == "WM790004DN-5637145360"  # style tag + colour code from the handle
    assert info.category_slug == "ayakkabi/sneaker"
    assert info.audience is Audience.MEN
    assert info.primary_color is PrimaryColor.BROWN
    assert info.material == "Deri"
    assert info.seasons == [Season.AUTUMN, Season.WINTER]
    assert info.description


def test_derimod_second_colourway_of_the_same_style_has_its_own_model_code() -> None:
    first, second = derimod_products()[0], derimod_products()[1]
    a = derimod.normalize_product(first, "https://derimod.com.tr/products/a")
    b = derimod.normalize_product(second, "https://derimod.com.tr/products/b")
    assert a.product.model_code != b.product.model_code
    assert b.product.primary_color is PrimaryColor.BLACK


def test_derimod_crossed_out_price_and_sold_out() -> None:
    product = derimod_products()[0]
    for variant in product["variants"]:
        variant["compare_at_price"] = "6999.00"
        variant["available"] = False
    offer = derimod.normalize_product(product, "https://derimod.com.tr/products/x")
    assert str(offer.original_price) == "6999.00"
    assert offer.stock_status is StockStatus.OUT_OF_STOCK
    assert offer.available_sizes == []


def test_derimod_zero_compare_at_price_means_not_crossed_out() -> None:
    product = derimod_products()[0]
    assert product["variants"][0]["compare_at_price"] == "0.00"
    assert derimod.normalize_product(product, "https://derimod.com.tr/products/x").original_price is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"vendor": "Geox"}, "other-brand"),
        ({"title": "Erkek Deri Cüzdan", "product_type": "Aksesuar > Cüzdan"}, "unknown-category"),
        ({"images": []}, "no-images"),
        ({"variants": []}, "no-variants"),
    ],
)
def test_derimod_skips_what_it_cannot_use(change: dict[str, Any], reason: str) -> None:
    product = derimod_products()[0]
    product.update(change)
    with pytest.raises(SkipItem) as caught:
        derimod.normalize_product(product, "https://derimod.com.tr/products/x")
    assert caught.value.reason == reason


async def test_derimod_discovery_reads_collection_json_and_dedupes(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, "https://derimod.com.tr", fixture_text("derimod/robots.txt"))
    route = router.get(url__regex=r"https://derimod\.com\.tr/collections/.*/products\.json.*").respond(
        200, json=fixture_json("derimod/collection_erkek-deri-sneaker.json")
    )
    source = derimod.DerimodSource(http, max_pages=3)
    refs = [ref async for ref in source.discover()]

    assert len(refs) == 5  # the same 5 products came back from all 13 collections: deduplicated
    assert route.call_count == len(derimod.LISTINGS)  # 5 < page size, so there is no page 2
    assert refs[0].url.startswith("https://derimod.com.tr/products/")
    assert refs[0].category_hint == "ayakkabi/sneaker"
    offer = await source.fetch(refs[0])
    assert offer is not None
    assert offer.product.model_code == "WM790004DN-5637145360"  # fetch needs no further request


async def test_derimod_discovery_pages_until_a_short_page(router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(derimod, "PAGE_SIZE", 2)
    monkeypatch.setattr(derimod, "LISTINGS", (Listing("erkek-bot", "ayakkabi/bot"),))
    allow_robots(router, "https://derimod.com.tr")
    products = derimod_products()
    pages = {"1": products[:2], "2": products[2:4], "3": products[4:5]}
    router.get(url__regex=r".*/products\.json.*").mock(
        side_effect=lambda request: __import__("httpx").Response(200, json={"products": pages[request.url.params["page"]]})
    )
    source = derimod.DerimodSource(http, max_pages=5)
    assert len([ref async for ref in source.discover()]) == 5


# ================================================================================================= Koton


def koton_product() -> dict[str, Any]:
    return copy.deepcopy(fixture_json("koton/product_gomlek.json"))


def test_koton_normalizes_the_storefront_json() -> None:
    offer = koton.normalize_product(koton_product(), "https://www.koton.com/kapakli-gomlek-gri-4085984/")

    assert offer.external_id == "5SAK60396UW-031"
    assert str(offer.price) == "899.99"
    assert offer.original_price is None
    assert offer.available_sizes == ["40"]  # the only size with stock
    assert offer.stock_status is StockStatus.LOW_STOCK  # one unit left
    assert len(offer.image_urls) == 5
    assert offer.title == "Kapaklı Cep Detaylı Klasik Yaka Kolsuz Saten Gömlek"

    info = offer.product
    assert info.brand_name == "Koton"
    assert info.model_code == "5SAK60396UW-031"
    assert info.category_slug == "ust-giyim/gomlek"
    assert info.audience is Audience.WOMEN
    assert info.primary_color is PrimaryColor.GREY
    assert info.fit is Fit.REGULAR
    assert info.material == "Saten"
    assert info.seasons == [Season.SPRING, Season.SUMMER]


def test_koton_crossed_out_price_is_retail_price() -> None:
    data = koton_product()
    data["product"]["price"] = "599.99"
    data["product"]["retail_price"] = "899.99"
    offer = koton.normalize_product(data, "https://www.koton.com/x/")
    assert (str(offer.price), str(offer.original_price)) == ("599.99", "899.99")


def test_koton_sold_out_and_plenty_in_stock() -> None:
    data = koton_product()
    for group in data["variants"]:
        for option in group["options"]:
            if group["attribute_key"] == "integration_size_id":
                option["in_stock"] = False
    assert koton.normalize_product(data, "https://www.koton.com/x/").stock_status is StockStatus.OUT_OF_STOCK

    data = koton_product()
    for group in data["variants"]:
        for option in group["options"]:
            if group["attribute_key"] == "integration_size_id":
                option["in_stock"], option["product"]["stock"] = True, 10
    offer = koton.normalize_product(data, "https://www.koton.com/x/")
    assert offer.stock_status is StockStatus.IN_STOCK
    assert offer.available_sizes == ["34", "36", "38", "40", "42", "44"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda d: d["product"].update(name="Kravat Minimal Desenli", attributes={**d["product"]["attributes"], "filterable_category": "Kravat"}), "unknown-category"),
        (lambda d: d["product"]["attributes"].pop("integration_color_id"), "no-model-code"),
        (lambda d: d["product"].update(productimage_set=[]), "no-images"),
        (lambda d: d["product"].update(price="0"), "no-price"),
        (lambda d: d["product"]["attributes"].update(filterable_gender="", morhipo_cinsiyet=""), "unknown-audience"),
    ],
)
def test_koton_skips_what_it_cannot_use(mutate: Any, reason: str) -> None:
    data = koton_product()
    mutate(data)
    with pytest.raises(SkipItem) as caught:
        koton.normalize_product(data, "https://www.koton.com/x/")
    assert caught.value.reason == reason


def test_koton_canonical_url_drops_the_size_suffix() -> None:
    assert koton.canonical_url("/kapakli-gomlek-gri-4085984-1/") == "https://www.koton.com/kapakli-gomlek-gri-4085984/"
    assert koton.canonical_url("/kapakli-gomlek-gri-4085984/") == "https://www.koton.com/kapakli-gomlek-gri-4085984/"
    assert koton.canonical_url("/pamuklu-3-lu-corap-4183278-12/") == "https://www.koton.com/pamuklu-3-lu-corap-4183278/"


async def test_koton_discovery_goes_page_major_and_stops_at_max_pages(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(koton, "LISTINGS", (Listing("erkek-gomlek", "ust-giyim/gomlek"), Listing("kadin-gomlek", "ust-giyim/gomlek")))
    allow_robots(router, "https://www.koton.com", fixture_text("koton/robots.txt"))
    listing = fixture_json("koton/listing_erkek-gomlek.json")
    seen_urls: list[str] = []

    def answer(request: Any) -> Any:
        import httpx

        seen_urls.append(str(request.url))
        page = int(request.url.params["page"])
        shifted = copy.deepcopy(listing)
        for product in shifted["products"]:  # make every page and every category show different products
            product["listing_code"] = f"{request.url.path.strip('/')}-{page}-{product['pk']}"
        return httpx.Response(200, json=shifted)

    router.get(url__regex=r"https://www\.koton\.com/(erkek|kadin)-gomlek/.*").mock(side_effect=answer)
    source = koton.KotonSource(http, max_pages=2)
    refs = [ref async for ref in source.discover()]

    assert [u.split("com")[1] for u in seen_urls] == [
        "/erkek-gomlek/?format=json&page=1",
        "/kadin-gomlek/?format=json&page=1",
        "/erkek-gomlek/?format=json&page=2",
        "/kadin-gomlek/?format=json&page=2",
    ]
    assert len(refs) == 12
    assert refs[0].url.startswith("https://www.koton.com/")
    assert refs[0].key.startswith("7WAM60003NW-")


async def test_koton_fetch_reads_the_product_json(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, "https://www.koton.com", fixture_text("koton/robots.txt"))
    route = router.get("https://www.koton.com/kapakli-gomlek-gri-4085984-1/?format=json").respond(200, json=koton_product())
    source = koton.KotonSource(http)
    ref = ListingRef(
        url="https://www.koton.com/kapakli-gomlek-gri-4085984/",
        key="5SAK60396UW-031",
        data={"absolute_url": "/kapakli-gomlek-gri-4085984-1/"},
    )
    offer = await source.fetch(ref)
    assert offer is not None
    assert offer.url == "https://www.koton.com/kapakli-gomlek-gri-4085984/"
    assert route.call_count == 1


async def test_koton_fetch_of_a_removed_product_is_a_skip(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, "https://www.koton.com")
    router.get(url__startswith="https://www.koton.com/gone").respond(404)
    ref = ListingRef(url="https://www.koton.com/gone/", key="k", data={"absolute_url": "/gone/"})
    with pytest.raises(SkipItem) as caught:
        await koton.KotonSource(http).fetch(ref)
    assert caught.value.reason == "gone"


# ================================================================================================= Jack & Jones

JJ_PRODUCT_URL = "https://www.jackjones.com.tr/tr-tr/product/12298420_2161/rahat-kalip-bisiklet-yaka-tisort"


def test_jack_jones_normalizes_the_embedded_state() -> None:
    offer = jackjones.normalize_page(fixture_text("jack-jones/product_tisort.html"), JJ_PRODUCT_URL)

    assert offer.external_id == "12298420_2161"
    assert str(offer.price) == "890"
    assert offer.original_price is None
    assert offer.available_sizes == ["L"]  # M, XL, S, XXL are sold out
    assert offer.stock_status is StockStatus.IN_STOCK
    assert 1 <= len(offer.image_urls) <= 6
    assert offer.title == "Erkek Logo Baskılı Tişört - Kendrıck"

    info = offer.product
    assert info.brand_name == "Jack & Jones"
    assert info.model_code == "12298420-2161"
    assert info.category_slug == "ust-giyim/tisort"
    assert info.audience is Audience.MEN
    assert info.primary_color is PrimaryColor.BLACK
    assert info.fit is Fit.RELAXED
    assert info.pattern is Pattern.PRINTED
    assert info.material == "%100 Pamuk"


def _jj_product() -> dict[str, Any]:
    state = jackjones.extract_initial_state(fixture_text("jack-jones/product_tisort.html"))
    key = next(k for k in state if k.startswith("$initialContentResponse-"))
    return copy.deepcopy(state[key]["product"])


def test_jack_jones_sold_out_and_sale_price() -> None:
    product = _jj_product()
    for variant in product["variants"]:
        variant["inStockEcom"] = False
    product["price"]["listPrice"] = {"amount": 1190, "currency": "TRY"}
    offer = jackjones.normalize_product(product, JJ_PRODUCT_URL)
    assert offer.stock_status is StockStatus.OUT_OF_STOCK
    assert offer.available_sizes == []
    assert str(offer.original_price) == "1190"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p.update(name="Erkek Sweat Şort", ediCategory="Şort", ediSubCategory="Şort", englishCategory="Shorts"), "unknown-category"),
        (lambda p: p.update(media=[]), "no-images"),
        (lambda p: p["price"].update(salesPrice={"amount": 0}), "no-price"),
        (lambda p: p.update(englishGender="", targetGroup="", tags=[]), "unknown-audience"),
    ],
)
def test_jack_jones_skips_what_it_cannot_use(mutate: Any, reason: str) -> None:
    product = _jj_product()
    mutate(product)
    with pytest.raises(SkipItem) as caught:
        jackjones.normalize_product(product, JJ_PRODUCT_URL)
    assert caught.value.reason == reason


def test_jack_jones_page_without_state_is_a_skip() -> None:
    with pytest.raises(SkipItem) as caught:
        jackjones.normalize_page("<html><body>Bakımdayız</body></html>", JJ_PRODUCT_URL)
    assert caught.value.reason == "no-product-data"


def test_extract_initial_state_is_forgiving() -> None:
    assert jackjones.extract_initial_state("<html></html>") == {}
    assert jackjones.extract_initial_state("window.__INITIAL_STATE__ = {broken") == {}
    assert jackjones.extract_initial_state('x window.__INITIAL_STATE__ = {"a": {"b": 1}}; y') == {"a": {"b": 1}}


async def test_jack_jones_discovery_reads_the_listing_state(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jackjones, "LISTINGS", (Listing("erkek-giyim/tisort", "ust-giyim/tisort"),))
    allow_robots(router, "https://www.jackjones.com.tr", fixture_text("jack-jones/robots.txt"))
    route = router.get(url__regex=r"https://www\.jackjones\.com\.tr/tr-tr/erkek-giyim/tisort.*").respond(
        200, text=fixture_text("jack-jones/listing_tisort.html")
    )
    refs = [ref async for ref in jackjones.JackJonesSource(http, max_pages=3).discover()]

    # 3 results per fixture page of a 156-product category: the same 3 on every page, deduplicated
    assert len(refs) == 3
    assert route.call_count == 3
    assert all(ref.url.startswith("https://www.jackjones.com.tr/tr-tr/product/") for ref in refs)
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2", "3"]


async def test_jack_jones_fetch_parses_the_product_page(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, "https://www.jackjones.com.tr", fixture_text("jack-jones/robots.txt"))
    router.get(JJ_PRODUCT_URL).respond(200, text=fixture_text("jack-jones/product_tisort.html"))
    offer = await jackjones.JackJonesSource(http).fetch(ListingRef(url=JJ_PRODUCT_URL, key="12298420_2161"))
    assert offer is not None
    assert offer.external_id == "12298420_2161"


# ================================================================================================= robots + registry


async def test_a_source_whose_paths_robots_disallows_is_reported_blocked(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, "https://derimod.com.tr", "User-agent: *\nDisallow: /collections/\n")
    page = router.get(url__startswith="https://derimod.com.tr/collections/").respond(200, json={"products": []})
    with pytest.raises(RobotsBlocked):
        _ = [ref async for ref in derimod.DerimodSource(http).discover()]
    assert page.call_count == 0


ALL_SOURCES = ["colins", "derimod", "hotic", "jack-jones", "koton", "network", "twist"]


def test_registry_lists_every_source_with_conventional_seller_slugs() -> None:
    assert sorted(SOURCE_CLASSES) == ALL_SOURCES
    assert runnable_slugs() == ALL_SOURCES
    for slug, cls in SOURCE_CLASSES.items():
        assert cls.slug == slug
        assert not cls.blocked
        assert cls.notes


async def test_sellers_are_brand_stores_with_backend_slugs(http: PoliteClient) -> None:
    expected = {slug: f"{slug}-store" for slug in ALL_SOURCES}
    for slug, seller_slug in expected.items():
        source = build_source(slug, http)
        assert source.seller_slug == seller_slug
        wire = source.seller.to_wire()
        assert wire["kind"] == "BrandStore"
        assert wire["websiteUrl"].startswith("https://")
        assert wire["isActive"] is True


def test_unknown_source_and_blocked_flag(http: PoliteClient, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnknownSourceError, match="known sources: colins, derimod, hotic, jack-jones, koton, network, twist"):
        build_source("zara", http)
    monkeypatch.setattr(SOURCE_CLASSES["koton"], "blocked", True)
    assert "koton" not in runnable_slugs()


def test_fixtures_are_valid_json_documents() -> None:
    json.loads(fixture_text("koton/product_gomlek.json"))
    json.loads(fixture_text("derimod/collection_erkek-deri-sneaker.json"))
