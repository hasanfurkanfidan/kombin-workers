"""Network, Hotiç, Twist and Colin's: parsers against saved real pages, discovery with respx, and the robots gate.

Fixtures are trimmed copies of real responses (2026-11-14), see tests/fixtures/<slug>/. Nothing here touches the network.
Where a test edits a fixture ("crossed-out price", "sold out") the real pages had no such product on sale that day.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from kombin_workers.http import PoliteClient, RobotsBlocked, RobotsRules
from kombin_workers.models import Audience, Fit, NormalizedOffer, Pattern, PrimaryColor, StockStatus
from kombin_workers.normalize.categories import CATEGORY_SLUGS
from kombin_workers.sources import colins, hotic, network, twist
from kombin_workers.sources.base import BaseSource, Listing, ListingRef, Page, SkipItem, adult_audience
from kombin_workers.sources.embedded import dig, extract_ld_json, extract_next_data, find_ld_type
from tests.conftest import allow_robots, fixture_json, fixture_text

# ================================================================================================= helpers


def next_data_page(data: dict[str, Any]) -> str:
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def prices(offer: NormalizedOffer) -> tuple[float, float | None]:
    return float(offer.price), None if offer.original_price is None else float(offer.original_price)


def reason_of(call: Callable[[], object]) -> str:
    with pytest.raises(SkipItem) as caught:
        call()
    return caught.value.reason


# ================================================================================================= Network

NETWORK_URL = "https://www.network.com.tr"


def network_records(name: str) -> dict[int, dict[str, Any]]:
    return {r["ID"]: r for r in network.extract_records(fixture_text(f"network/{name}"))}


def test_network_extract_records_reads_each_embedded_product_once() -> None:
    html = fixture_text("network/listing_erkek-gomlek.html")
    assert html.count('...{"ID":') == 4  # the page repeats every record in a second script block
    records = network.extract_records(html)
    assert [r["ID"] for r in records] == [223002, 223066]
    assert network.extract_records("<html>no state</html>") == []
    assert network.extract_records('<script>...{"ID":3, broken</script>') == []


def test_network_normalizes_an_embedded_product() -> None:
    record = network_records("listing_erkek-gomlek.html")[223002]
    offer = network.normalize_product(record, f"{NETWORK_URL}/tutun-keten-dis-gomlek-p-223002")
    product = offer.product
    assert offer.external_id == "223002"
    assert offer.title == "Tütün Keten Dış Gömlek"
    assert prices(offer) == (12999, None)
    assert offer.stock_status is StockStatus.IN_STOCK
    assert offer.available_sizes == ["XS", "S", "M", "L", "XL", "XXL"]
    assert offer.image_urls[0] == "https://img-network.mncdn.com/mnresize/1000/-/productimages/8684782259850_01.jpg"
    assert len(offer.image_urls) == 3
    assert product.brand_name == "Network"
    assert product.model_code == "1099332-536"
    assert product.category_slug == "ust-giyim/gomlek"  # good type "Dış Gömlek", although the site files it under Mont
    assert product.audience is Audience.MEN
    assert product.primary_color is PrimaryColor.BROWN  # colour "Tütün", colour group "Kahverengi"
    assert product.fit is Fit.RELAXED  # "COMFORT FİT"
    assert product.pattern is Pattern.SOLID


def test_network_image_file_names_with_a_space_are_percent_encoded() -> None:
    record = copy.deepcopy(network_records("listing_erkek-gomlek.html")[223002])  # real case: ".../8684782298279_04 .jpg"
    file = record["MediaList"][1]["ProductMediaFileDOList"][0]
    file["ImageResizePath"] = "https://img-network.mncdn.com/mnresize/{width}/{height}/productimages/8684782298279_04 .jpg"
    offer = network.normalize_product(record, f"{NETWORK_URL}/x-p-223002")
    assert offer.image_urls[1] == "https://img-network.mncdn.com/mnresize/1000/-/productimages/8684782298279_04%20.jpg"


def test_network_second_product_is_a_striped_pink_shirt() -> None:
    record = network_records("listing_erkek-gomlek.html")[223066]
    offer = network.normalize_product(record, f"{NETWORK_URL}/x-p-223066")
    assert offer.product.primary_color is PrimaryColor.PINK
    assert offer.product.pattern is Pattern.STRIPED
    assert offer.product.model_code == "1099359-618"


def test_network_crossed_out_price_and_basket_campaign_price() -> None:
    record = network_records("listing_kadin-bot.html")[194891]
    assert record["ProductPromotion"]["PromotedPrice"]  # a basket-only price exists and must not be used
    offer = network.normalize_product(record, f"{NETWORK_URL}/x-p-194891")
    assert prices(offer) == (4569, 15249)
    assert offer.available_sizes == ["36", "37"]
    assert offer.product.category_slug == "ayakkabi/bot"
    assert offer.product.audience is Audience.WOMEN


def test_network_low_stock_flag_and_a_full_price_product() -> None:
    records = network_records("listing_kadin-bot.html")
    low = network.normalize_product(records[194896], f"{NETWORK_URL}/x-p-194896")
    assert low.stock_status is StockStatus.LOW_STOCK
    assert low.original_price == 17449
    full = network.normalize_product(records[194892], f"{NETWORK_URL}/x-p-194892")
    assert prices(full) == (14999, None)
    assert full.stock_status is StockStatus.IN_STOCK


def test_network_sold_out_when_no_size_has_stock() -> None:
    record = copy.deepcopy(network_records("listing_kadin-bot.html")[194891])
    for variant in record["VariantWithStockList"]:
        variant["StockExists"] = False
    offer = network.normalize_product(record, f"{NETWORK_URL}/x-p-194891")
    assert offer.stock_status is StockStatus.OUT_OF_STOCK
    assert offer.available_sizes == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda r: r.update(DisplayName="Çizgili Elbise", CategoryLevelDisplayName="Kadın/Giyim/Elbise") or r["NonVariantAttributeList"].clear(), "unknown-category"),
        (lambda r: r.update(ActualPrice="0.0", ActualPriceToShowOnScreen="0.0"), "no-price"),
        (lambda r: r.update(MediaList=[]), "no-images"),
        (lambda r: r.update(GenderAttribute="Kız Çocuk"), "kids"),
        (lambda r: r.update(GenderAttribute="", NonVariantAttributeList=[]), "unknown-audience"),
        (lambda r: r.update(IsWebSaleForbidden=True), "not-for-sale"),
    ],
)
def test_network_skips_what_it_cannot_use(mutate: Callable[[dict[str, Any]], object], reason: str) -> None:
    record = copy.deepcopy(network_records("listing_erkek-gomlek.html")[223002])
    if reason == "unknown-audience":  # keep the category readable so the audience check is what fails
        record["NonVariantAttributeList"] = [{"CodeName": "good-type-description", "ValueText": "Gömlek"}]
    mutate(record)
    assert reason_of(lambda: network.normalize_product(record, f"{NETWORK_URL}/x-p-1")) == reason


async def test_network_discovery_reads_embedded_records_and_follows_the_next_link(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(network, "LISTINGS", (Listing("erkek-gomlek-cesitleri-710", "ust-giyim/gomlek"),))
    allow_robots(router, NETWORK_URL, fixture_text("network/robots.txt"))
    pages = {
        "1": fixture_text("network/listing_erkek-gomlek.html"),
        "2": fixture_text("network/listing_kadin-bot.html"),  # no <link rel="next">: the last page
    }
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(200, text=pages[request.url.params["page"]])

    router.get(f"{NETWORK_URL}/erkek-gomlek-cesitleri-710").mock(side_effect=answer)
    refs = [ref async for ref in network.NetworkSource(http, max_pages=5).discover()]
    assert seen == ["page=1", "page=2"]
    assert [r.key for r in refs] == ["223002", "223066", "194891", "194896", "194892"]
    assert refs[0].url == f"{NETWORK_URL}/tutun-keten-dis-gomlek-p-223002"


async def test_network_fetch_needs_no_request(router: respx.MockRouter, http: PoliteClient) -> None:
    record = network_records("listing_erkek-gomlek.html")[223002]
    ref = ListingRef(url=f"{NETWORK_URL}/tutun-keten-dis-gomlek-p-223002", key="223002", data=record)
    offer = await network.NetworkSource(http).fetch(ref)
    assert offer is not None and offer.external_id == "223002"
    assert not router.calls



# ================================================================================================= Hotiç

HOTIC_URL = "https://www.hotic.com.tr"


def hotic_records(name: str) -> dict[str, dict[str, Any]]:
    data = fixture_json(f"hotic/{name}")["props"]["pageProps"]["productList"]["products"]["data"]
    return {r["erpCode"]: r for r in data}


def test_hotic_normalizes_a_listing_record() -> None:
    record = hotic_records("listing_erkek-loafer.json")["02AYH333890A100"]
    offer = hotic.normalize_product(record, f"{HOTIC_URL}/{record['slug']}")
    product = offer.product
    assert offer.external_id == "02AYH333890A100"
    assert prices(offer) == (6999, None)
    assert offer.available_sizes == ["40", "41", "42", "43", "44"]  # size 45 has quantity 0
    assert offer.stock_status is StockStatus.IN_STOCK
    assert len(offer.image_urls) == 3 and offer.image_urls[0].startswith("https://img-phantomhotic.sm.mncdn.com/")
    assert product.brand_name == "Hotiç"
    assert product.model_code == "02AYH33389-0A100"  # style + colour code
    assert product.category_slug == "ayakkabi/loafer"
    assert product.audience is Audience.MEN
    assert product.primary_color is PrimaryColor.BLACK


def test_hotic_skips_sizes_without_stock_and_reads_the_selected_colour() -> None:
    record = hotic_records("listing_erkek-loafer.json")["02AYH303060A620"]
    offer = hotic.normalize_product(record, f"{HOTIC_URL}/{record['slug']}")
    assert offer.available_sizes == ["40", "41", "43"]
    assert offer.product.primary_color is PrimaryColor.BLUE
    assert offer.product.model_code == "02AYH30306-0A620"


def test_hotic_bag_is_one_size_for_women() -> None:
    record = hotic_records("listing_kadin-sirt-cantasi.json")["03CAH316410A100"]
    offer = hotic.normalize_product(record, f"{HOTIC_URL}/{record['slug']}")
    assert offer.available_sizes == ["STD"]
    assert offer.product.category_slug == "aksesuar/canta"
    assert offer.product.audience is Audience.WOMEN


def test_hotic_crossed_out_price_low_stock_and_sold_out() -> None:
    record = copy.deepcopy(hotic_records("listing_erkek-loafer.json")["02AYH333890A100"])  # edited: nothing was on sale
    record["price"]["oldPrice"] = 9999
    assert hotic.normalize_product(record, f"{HOTIC_URL}/x").original_price == 9999

    for variant in record["variants"]:
        variant["quantity"] = 1 if variant["specValueName"] in ("40", "41") else 0
    assert hotic.normalize_product(record, f"{HOTIC_URL}/x").stock_status is StockStatus.LOW_STOCK

    for variant in record["variants"]:
        variant["quantity"] = 0
    sold_out = hotic.normalize_product(record, f"{HOTIC_URL}/x")
    assert (sold_out.stock_status, sold_out.available_sizes) == (StockStatus.OUT_OF_STOCK, [])


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda r: r.update(brandSlug="geox"), "other-brand"),
        (lambda r: r.update(erpCode=""), "no-model-code"),
        (lambda r: r.update(productName="Erkek Deri Sandalet", categories=[{"name": "Erkek Sandalet"}]), "unknown-category"),
        (lambda r: r["price"].update(newPrice=0), "no-price"),
        (lambda r: r.update(pictures=[]), "no-images"),
        (lambda r: r.update(productName="Kız Çocuk Loafer", categories=[{"name": "Kız Çocuk Ayakkabı"}]), "kids"),
        (lambda r: r.update(productName="Deri Loafer", categories=[{"name": "Loafer"}]), "unknown-audience"),
    ],
)
def test_hotic_skips_what_it_cannot_use(mutate: Callable[[dict[str, Any]], object], reason: str) -> None:
    record = copy.deepcopy(hotic_records("listing_erkek-loafer.json")["02AYH333890A100"])
    mutate(record)
    assert reason_of(lambda: hotic.normalize_product(record, f"{HOTIC_URL}/x")) == reason


async def test_hotic_discovery_pages_with_a_zero_based_page_index(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hotic, "LISTINGS", (Listing("erkek-loafer", "ayakkabi/loafer"),))
    allow_robots(router, HOTIC_URL, fixture_text("hotic/robots.txt"))
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        data = fixture_json("hotic/listing_erkek-loafer.json")
        products = data["props"]["pageProps"]["productList"]["products"]
        for record in products["data"]:
            record["erpCode"] += request.url.params["pageIndex"]
        products["hasNextPage"] = request.url.params["pageIndex"] == "0"
        return httpx.Response(200, text=next_data_page(data))

    router.get(f"{HOTIC_URL}/erkek-loafer").mock(side_effect=answer)
    refs = [ref async for ref in hotic.HoticSource(http, max_pages=5).discover()]
    assert seen == ["pageIndex=0", "pageIndex=1"]
    assert len(refs) == 4
    assert refs[0].url.startswith(f"{HOTIC_URL}/siyah-erkek-rugan-deri")
    assert refs[0].category_hint == "ayakkabi/loafer"


async def test_hotic_fetch_needs_no_request(router: respx.MockRouter, http: PoliteClient) -> None:
    record = hotic_records("listing_erkek-loafer.json")["02AYH333890A100"]
    ref = ListingRef(url=f"{HOTIC_URL}/{record['slug']}", key=record["erpCode"], data=record)
    assert await hotic.HoticSource(http).fetch(ref) is not None
    assert not router.calls


# ================================================================================================= Twist

TWIST_URL = "https://www.twist.com.tr"


def twist_product() -> tuple[dict[str, Any], dict[str, Any]]:
    data = fixture_json("twist/product_gomlek.json")
    return data["product"], data["ld"]


def test_twist_normalizes_a_product_page() -> None:
    product, ld = twist_product()
    offer = twist.normalize_product(product, f"{TWIST_URL}/urun/x", ld)
    assert offer.external_id == "TW6260025124003"
    assert offer.title == "Ekose Desen Yakası Bağlamalı Gömlek"  # the site writes it in capitals
    assert prices(offer) == (4999, None)
    assert offer.available_sizes == ["34", "36", "38", "40"]  # 42 and 44 have no stock
    assert offer.stock_status is StockStatus.IN_STOCK
    assert offer.image_urls[0] == "https://cdn.twist.com.tr/mnresize/2400/-/Twist/products/FW2627/Koleksiyon/TW6260025124003_0.jpg"
    assert len(offer.image_urls) == 6
    assert offer.product.brand_name == "Twist"
    assert offer.product.model_code == "TW6260025124-003"  # style, colour code
    assert offer.product.category_slug == "ust-giyim/gomlek"
    assert offer.product.audience is Audience.WOMEN
    assert offer.product.primary_color is PrimaryColor.BEIGE  # "Natürel"
    assert offer.product.pattern is Pattern.CHECKED
    assert offer.product.material == "%100 Pamuk"


def test_twist_crossed_out_price_low_stock_and_sold_out() -> None:
    product, ld = twist_product()
    product = copy.deepcopy(product)  # edited: nothing on the day's pages was reduced
    product["basePrice"], product["salesPrice"] = 7999, 4999
    assert twist.normalize_product(product, f"{TWIST_URL}/urun/x", ld).original_price == 7999

    product["stocksByBarcode"] = {key: (1 if key == "80263" else 0) for key in product["stocksByBarcode"]}
    low = twist.normalize_product(product, f"{TWIST_URL}/urun/x", ld)
    assert (low.stock_status, low.available_sizes) == (StockStatus.LOW_STOCK, ["36"])

    product["stocksByBarcode"] = {key: 0 for key in product["stocksByBarcode"]}
    gone = twist.normalize_product(product, f"{TWIST_URL}/urun/x", ld)
    assert (gone.stock_status, gone.available_sizes) == (StockStatus.OUT_OF_STOCK, [])


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p.update(name="Çiçek Desen Elbise", categories=[{"id": 1, "name": "Elbise"}]) or p["attributes"].clear(), "unknown-category"),
        (lambda p: p.update(salesPrice=0), "no-price"),
        (lambda p: p.update(documents=[]), "no-images"),
        (lambda p: [a["attributeValues"].__setitem__(0, {"value": "Çocuk"}) for a in p["attributes"] if a["attribute"]["integrationCode"] == "gender"], "kids"),
    ],
)
def test_twist_skips_what_it_cannot_use(mutate: Callable[[dict[str, Any]], object], reason: str) -> None:
    product, ld = twist_product()
    product = copy.deepcopy(product)
    mutate(product)
    assert reason_of(lambda: twist.normalize_product(product, f"{TWIST_URL}/urun/x", ld)) == reason


def test_twist_page_without_state_is_a_skip() -> None:
    assert reason_of(lambda: twist.normalize_page("<html>nothing</html>", f"{TWIST_URL}/urun/x")) == "no-product-data"


def test_twist_normalize_page_reads_next_data_and_ld_json() -> None:
    product, ld = twist_product()
    wrapped = {"props": {"pageProps": {"data": {"response": {"product": product}}}}}
    html = (
        f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(wrapped)}</script></body></html>'
    )
    offer = twist.normalize_page(html, f"{TWIST_URL}/urun/x")
    assert offer.product.material == "%100 Pamuk"
    assert offer.available_sizes == ["34", "36", "38", "40"]


async def test_twist_without_a_sitemap_falls_back_to_the_first_page_of_a_category(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """robots.txt disallows `?page=`: even with max_pages=3 the category page is read once, and never with a page parameter."""
    monkeypatch.setattr(twist, "LISTINGS", (Listing("kadin-gomlek-modelleri", "ust-giyim/gomlek"),))
    allow_robots(router, TWIST_URL, fixture_text("twist/robots.txt"))
    router.get(f"{TWIST_URL}/sitemap.xml").respond(404)
    route = router.get(f"{TWIST_URL}/kadin-gomlek-modelleri").respond(200, text=next_data_page(fixture_json("twist/listing_kadin-gomlek.json")))
    refs = [ref async for ref in twist.TwistSource(http, max_pages=3).discover()]
    assert route.call_count == 1
    assert all("page=" not in str(call.request.url) for call in router.calls)
    assert [r.key for r in refs] == [p["sku"] for p in fixture_json("twist/listing_kadin-gomlek.json")["props"]["pageProps"]["data"]["response"]["products"]]
    assert refs[0].url.startswith(f"{TWIST_URL}/urun/")


async def test_twist_fetch_reads_the_product_page_and_a_missing_one_is_a_skip(
    router: respx.MockRouter, http: PoliteClient
) -> None:
    product, ld = twist_product()
    wrapped = {"props": {"pageProps": {"data": {"response": {"product": product}}}}}
    allow_robots(router, TWIST_URL, fixture_text("twist/robots.txt"))
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>' + next_data_page(wrapped)
    router.get(f"{TWIST_URL}/urun/x").respond(200, text=html)
    router.get(f"{TWIST_URL}/urun/gone").respond(404)
    source = twist.TwistSource(http)
    offer = await source.fetch(ListingRef(url=f"{TWIST_URL}/urun/x", key="k"))
    assert offer is not None and offer.external_id == "TW6260025124003"
    with pytest.raises(SkipItem) as caught:
        await source.fetch(ListingRef(url=f"{TWIST_URL}/urun/gone", key="g"))
    assert caught.value.reason == "gone"


# ================================================================================================= Colin's

COLINS_URL = "https://www.colins.com.tr"


def colins_cards(name: str) -> list[dict[str, Any]]:
    return colins.extract_cards(fixture_text(f"colins/{name}"))


def test_colins_extract_cards_reads_the_data_attributes() -> None:
    cards = colins_cards("listing_erkek-gomlek.html")
    assert [c["id"] for c in cards] == [47298, 50752]
    first = cards[0]
    assert first["colour"] == "Yeşil"
    assert first["url"] == "/p/comfort-fit-shirt-neck-ekoseli-erkek-yesil-uzun-kol-gomlek-47298"
    assert first["price"] == "1499,90 TL" and first["oldPrice"] is None
    assert [s["name"] for s in first["sizes"]] == ["S", "M", "L", "XL"]
    assert colins.extract_cards("<html><body>no cards</body></html>") == []


def test_colins_normalizes_a_product() -> None:
    card = colins_cards("listing_erkek-gomlek.html")[0]
    offer = colins.normalize_product(card, fixture_text("colins/product_gomlek.html"), COLINS_URL + card["url"])
    assert offer.external_id == "47298"
    assert offer.title == "Comfort Fit Shirt Neck Ekoseli Erkek Yeşil Uzun Kol Gömlek"
    assert prices(offer) == (1499.9, None)
    assert offer.available_sizes == ["S", "M", "L", "XL"]
    assert offer.stock_status is StockStatus.IN_STOCK
    assert len(offer.image_urls) == 6 and offer.image_urls[0].endswith("294544_0.jpeg?v=1787562941")
    product = offer.product
    assert product.brand_name == "Colin's"
    assert product.model_code == "CL1076502-PGR"  # style, colour abbreviation
    assert product.category_slug == "ust-giyim/gomlek"
    assert product.audience is Audience.MEN
    assert product.primary_color is PrimaryColor.GREEN
    assert product.fit is Fit.RELAXED  # "Comfort Fit"
    assert product.pattern is Pattern.CHECKED
    assert product.material == "%100 Pamuk"


def test_colins_sold_out_pages_say_so_even_though_ld_json_claims_in_stock() -> None:
    bag = colins_cards("listing_kadin-canta.html")[0]
    offer = colins.normalize_product(bag, fixture_text("colins/product_canta-sold-out.html"), COLINS_URL + bag["url"])
    assert 'availability":"https://schema.org/InStock' in fixture_text("colins/product_canta-sold-out.html")
    assert (offer.stock_status, offer.available_sizes) == (StockStatus.OUT_OF_STOCK, [])
    assert prices(offer) == (1199.95, 1499.9)  # card oldPrice "1.499,90 TL"
    assert offer.product.category_slug == "aksesuar/canta"
    assert offer.product.audience is Audience.WOMEN
    assert offer.product.model_code == "CL1082205-BUR"

    jean_html = fixture_text("colins/product_jean-sold-out.html")
    jean_card = {**bag, "id": 50440, "name": "Flare Fit Kadın Green Cast Denim Jean Pantolon", "colour": "Yeşil"}
    jean = colins.normalize_product(jean_card, jean_html, COLINS_URL + "/p/flare-fit-kadin-green-cast-denim-jean-pantolon-50440")
    assert jean.stock_status is StockStatus.OUT_OF_STOCK  # the page has no size select at all
    assert jean.product.category_slug == "alt-giyim/jean"  # its breadcrumb has no sub-category: the name decides
    assert jean.product.model_code == "CL1082719-DN45196"


def test_colins_few_sizes_left_is_low_stock() -> None:
    card = colins_cards("listing_erkek-gomlek.html")[0]
    html = fixture_text("colins/product_gomlek.html")
    for size in ("M", "L", "XL"):
        html = html.replace('class="have-stock">\n\t\t\t\t\t\t\t' + size, 'class="no-stock">\n\t\t\t\t\t\t\t' + size)
    html = html.replace('class="have-stock"', 'class="no-stock"').replace('value="353830" class="no-stock"', 'value="353830" class="have-stock"')
    offer = colins.normalize_product(card, html, COLINS_URL + card["url"])
    assert offer.available_sizes == ["S"]
    assert offer.stock_status is StockStatus.LOW_STOCK


@pytest.mark.parametrize(
    ("replace", "reason"),
    [
        (("ERKEK UZUN KOL G&#214;MLEK", "ERKEK ELBİSE"), "unknown-category"),
        (('"price":"1499.90"', '"price":"0"'), "no-price"),
    ],
)
def test_colins_skips_what_it_cannot_use(replace: tuple[str, str], reason: str) -> None:
    card = copy.deepcopy(colins_cards("listing_erkek-gomlek.html")[0])
    html = fixture_text("colins/product_gomlek.html").replace(*replace)
    if reason == "unknown-category":
        card["name"] = "Erkek Yeşil Elbise"
    assert reason_of(lambda: colins.normalize_product(card, html, COLINS_URL + card["url"])) == reason


def test_colins_page_without_product_markup_is_a_skip() -> None:
    card = colins_cards("listing_erkek-gomlek.html")[0]
    assert reason_of(lambda: colins.normalize_product(card, "<html>nothing</html>", COLINS_URL + card["url"])) == "no-product-data"


def test_colins_prices_are_read_the_turkish_way() -> None:
    assert colins._tr_price("1.499,90 TL") == Decimal("1499.90")
    assert colins._tr_price("799,95 TL") == Decimal("799.95")
    assert colins._tr_price(None) is None
    assert colins._tr_price("") is None


async def test_colins_discovery_pages_with_pagenumber_and_stops_when_there_is_no_next_link(
    router: respx.MockRouter, http: PoliteClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(colins, "LISTINGS", (Listing("c/erkek-gomlek-1202", "ust-giyim/gomlek"),))
    allow_robots(router, COLINS_URL, fixture_text("colins/robots.txt"))
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        page = request.url.params.get("pagenumber", "1")
        text = fixture_text("colins/listing_erkek-gomlek.html" if page == "1" else "colins/listing_kadin-canta.html")
        return httpx.Response(200, text=text)

    router.get(f"{COLINS_URL}/c/erkek-gomlek-1202").mock(side_effect=answer)
    refs = [ref async for ref in colins.ColinsSource(http, max_pages=5).discover()]
    assert seen == ["", "pagenumber=2"]
    assert [r.key for r in refs] == ["47298", "50752", "50471", "50082"]
    assert refs[0].url == COLINS_URL + "/p/comfort-fit-shirt-neck-ekoseli-erkek-yesil-uzun-kol-gomlek-47298"


async def test_colins_fetch_reads_the_product_page(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, COLINS_URL, fixture_text("colins/robots.txt"))
    card = colins_cards("listing_erkek-gomlek.html")[0]
    route = router.get(COLINS_URL + card["url"]).respond(200, text=fixture_text("colins/product_gomlek.html"))
    router.get(COLINS_URL + "/p/gone-1").respond(404)
    source = colins.ColinsSource(http)
    offer = await source.fetch(ListingRef(url=COLINS_URL + card["url"], key="47298", data=card))
    assert offer is not None and offer.product.model_code == "CL1076502-PGR"
    assert route.call_count == 1
    with pytest.raises(SkipItem) as caught:
        await source.fetch(ListingRef(url=COLINS_URL + "/p/gone-1", key="1", data=card))
    assert caught.value.reason == "gone"


# ================================================================================================= robots gate

REAL_ROBOTS = {
    "network": NETWORK_URL,
    "hotic": HOTIC_URL,
    "twist": TWIST_URL,
    "colins": COLINS_URL,
}


def rules(slug: str) -> RobotsRules:
    return RobotsRules.from_text(fixture_text(f"{slug}/robots.txt"))


def test_network_robots_disallows_product_search_and_filters_but_not_what_we_read() -> None:
    r = rules("network")
    assert not r.allowed(f"{NETWORK_URL}/product/anything")
    assert not r.allowed(f"{NETWORK_URL}/search?searchKey=gomlek")
    assert not r.allowed(f"{NETWORK_URL}/erkek-gomlek-cesitleri-710?filterIDList=3")
    assert not r.allowed(f"{NETWORK_URL}/erkek-gomlek-cesitleri-710?orderOption=2")
    assert r.allowed(f"{NETWORK_URL}/erkek-gomlek-cesitleri-710?page=2")
    assert r.allowed(f"{NETWORK_URL}/tutun-keten-dis-gomlek-p-223002")


def test_hotic_robots_disallows_ordering_and_search_but_not_paging() -> None:
    r = rules("hotic")
    assert not r.allowed(f"{HOTIC_URL}/erkek-ayakkabi?ordering=price")
    assert not r.allowed(f"{HOTIC_URL}/erkek-ayakkabi?searchValue=bot")
    assert not r.allowed(f"{HOTIC_URL}/sepetim")
    assert r.allowed(f"{HOTIC_URL}/erkek-ayakkabi-erkek-tum-ayakkabilar-erkek-loafer-ayakkabi?pageIndex=1")
    assert r.allowed(f"{HOTIC_URL}/siyah-erkek-rugan-deri-siyah-kalin-tabanli-klasik-loafer-02ayh333890a100-p-38273")


def test_twist_robots_disallows_paging_and_filters_but_not_category_or_product_pages() -> None:
    r = rules("twist")
    assert not r.allowed(f"{TWIST_URL}/kadin-gomlek-modelleri?page=2")
    assert not r.allowed(f"{TWIST_URL}/kadin-gomlek-modelleri?beden=36")
    assert not r.allowed(f"{TWIST_URL}/kadin-gomlek-modelleri?sort=price")
    assert r.allowed(f"{TWIST_URL}/kadin-gomlek-modelleri")
    assert r.allowed(f"{TWIST_URL}/urun/naturel-ekose-desen-yakasi-baglamali-gomlek")


def test_colins_robots_disallows_urunler_search_and_sorting_but_not_c_and_p_pages() -> None:
    r = rules("colins")
    assert not r.allowed(f"{COLINS_URL}/urunler/gomlek")
    assert not r.allowed(f"{COLINS_URL}/c/erkek-gomlek-1202?orderby=5")
    assert not r.allowed(f"{COLINS_URL}/search?q=gomlek")
    assert r.allowed(f"{COLINS_URL}/c/erkek-gomlek-1202?pagenumber=2")
    assert r.allowed(f"{COLINS_URL}/p/comfort-fit-shirt-neck-ekoseli-erkek-yesil-uzun-kol-gomlek-47298")


@pytest.mark.parametrize("slug", sorted(REAL_ROBOTS))
async def test_a_new_source_is_blocked_without_a_single_page_request_when_robots_disallows_its_paths(
    slug: str, router: respx.MockRouter, http: PoliteClient
) -> None:
    from kombin_workers.sources.registry import build_source

    origin = REAL_ROBOTS[slug]
    allow_robots(router, origin, "User-agent: *\nDisallow: /\n")
    page = router.get(url__startswith=origin).respond(200, text="<html></html>")
    source = build_source(slug, http)
    with pytest.raises(RobotsBlocked):
        _ = [ref async for ref in source.discover()]
    assert page.call_count == 0  # only robots.txt was requested


async def test_the_robots_gate_refuses_a_disallowed_url_even_for_a_direct_get(router: respx.MockRouter, http: PoliteClient) -> None:
    allow_robots(router, TWIST_URL, fixture_text("twist/robots.txt"))
    page = router.get(url__startswith=f"{TWIST_URL}/kadin-gomlek-modelleri").respond(200, text="x")
    with pytest.raises(RobotsBlocked):
        await http.get(f"{TWIST_URL}/kadin-gomlek-modelleri?page=2")
    assert page.call_count == 0


# ================================================================================================= shared helpers


def test_adult_audience_skips_kids_and_unknowns() -> None:
    assert adult_audience("Kadın") is Audience.WOMEN
    assert adult_audience(None, "Erkek Giyim") is Audience.MEN
    assert adult_audience("Unisex") is Audience.UNISEX
    assert reason_of(lambda: adult_audience("Erkek Çocuk")) == "kids"
    assert reason_of(lambda: adult_audience("Mont", None)) == "unknown-audience"
    assert reason_of(lambda: adult_audience("Kadın Erkek")) == "unknown-audience"


@pytest.mark.parametrize("module", [network, hotic, twist, colins])
def test_every_listing_is_unique_and_hints_a_real_category(module: Any) -> None:
    paths = [listing.path for listing in module.LISTINGS]
    assert len(set(paths)) == len(paths)
    assert {listing.hint for listing in module.LISTINGS} <= CATEGORY_SLUGS


async def test_discovery_deals_products_out_one_listing_at_a_time_so_a_cap_stays_balanced(http: PoliteClient) -> None:
    pages = {"women": ["w1", "w2", "w3"], "men": ["m1", "m2", "m3"], "bags": ["b1"]}

    class Fake(BaseSource):
        slug = "fake"
        brand_name = seller_name = "Fake"
        website_url = "https://fake.test"

    async def read_page(listing: Listing, page: int) -> Page:
        keys = pages[listing.path] if page == 1 else []
        return Page(refs=[ListingRef(url=f"https://fake.test/{k}", key=k) for k in keys], has_more=False)

    source = Fake(http, max_pages=2)
    listings = [Listing("women", "x"), Listing("men", "x"), Listing("bags", "x")]
    keys = [ref.key async for ref in source.paged_discovery(listings, read_page)]
    assert keys == ["w1", "m1", "b1", "w2", "m2", "w3", "m3"]
    assert keys[:4] == ["w1", "m1", "b1", "w2"]  # a cap of four takes from every listing, not just the first


# ================================================================================================= embedded data readers


def test_extract_next_data_is_forgiving() -> None:
    assert extract_next_data(next_data_page({"props": {"a": 1}})) == {"props": {"a": 1}}
    assert extract_next_data("<html>none</html>") == {}
    assert extract_next_data('<script id="__NEXT_DATA__" type="application/json">{broken</script>') == {}
    assert extract_next_data('<script id="__NEXT_DATA__" type="application/json">[1, 2]</script>') == {}


def test_extract_ld_json_flattens_arrays_and_graphs_and_skips_invalid_blocks() -> None:
    html = (
        '<script type="application/ld+json">{"@type": "Product", "name": "a"}</script>'
        '<script type="application/ld+json">[{"@type": "BreadcrumbList"}, {"@type": ["Thing", "Product"], "name": "b"}]</script>'
        '<script type="application/ld+json">{"@graph": [{"@type": "Offer"}]}</script>'
        '<script type="application/ld+json">{nope</script>'
    )
    types = [node.get("@type") for node in extract_ld_json(html)]
    assert types == ["Product", "BreadcrumbList", ["Thing", "Product"], "Offer", None]
    found = find_ld_type(html, "Product")
    assert found is not None and found["name"] == "a"
    assert find_ld_type(html, "Event") is None
    assert find_ld_type('<script type="application/ld+json">[{"@type": ["Thing", "Product"], "name": "b"}]</script>', "Product") == {
        "@type": ["Thing", "Product"],
        "name": "b",
    }


def test_dig_returns_none_instead_of_raising() -> None:
    data = {"a": {"b": [10, {"c": 3}]}}
    assert dig(data, "a", "b", 1, "c") == 3
    assert dig(data, "a", "b", -1, "c") == 3
    assert dig(data, "a", "x", "c") is None
    assert dig(data, "a", "b", 5) is None
    assert dig(data, "a", "b", "c") is None
    assert dig(None, "a") is None
