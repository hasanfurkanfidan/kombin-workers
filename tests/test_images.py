"""Product galleries (task W3): `normalize.images` rules, and per source a real full gallery parsed and ordered.

`tests/fixtures/galleries.json` holds the real gallery records of a few product pages read on 2026-11-15 (Koton, Network,
Hotiç, Twist); Jack & Jones and Colin's use the product pages already saved under their own fixture folders.
Each test checks `gallery_urls` (up to 8 images, what the source collects) and that `normalize_product` sends the first 6
of that order (the backend's `imageUrls` limit).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from kombin_workers.models import MAX_IMAGES
from kombin_workers.normalize.images import GALLERY_CAP, first_is_person_free, order_gallery, person_free_hint
from kombin_workers.sources import colins, derimod, hotic, jackjones, koton, network, twist
from kombin_workers.sources.embedded import find_ld_type
from tests.conftest import fixture_json, fixture_text
from tests.test_sources import _jj_product, derimod_products
from tests.test_sources_w2 import hotic_records, network_records, twist_product


def galleries() -> dict[str, Any]:
    return copy.deepcopy(fixture_json("galleries.json"))


# ------------------------------------------------------------------------------------- the ordering rules


def test_without_hints_the_site_order_is_kept_and_duplicates_dropped() -> None:
    urls = [f"https://img.test/p/{n}.jpg" for n in (3, 1, 2, 1, 4)]
    assert order_gallery(urls) == [urls[0], urls[1], urls[2], urls[4]]


def test_the_gallery_is_capped_at_eight() -> None:
    urls = [f"https://img.test/p/{n}.jpg" for n in range(12)]
    assert GALLERY_CAP == 8
    assert order_gallery(urls) == urls[:8]


def test_person_free_hints_come_first_then_unknown_then_worn() -> None:
    worn = "https://img.test/p/model-front.jpg"
    unknown = "https://img.test/p/2.jpg"
    flat = "https://img.test/p/flat_view.jpg"
    ghost = "https://img.test/p/GHOST-1.jpg"
    detail = "https://img.test/p/detail.jpg"
    assert order_gallery([worn, unknown, flat, ghost, detail]) == [flat, ghost, detail, unknown, worn]


def test_ordering_happens_before_the_cut_so_a_late_packshot_survives() -> None:
    urls = [f"https://img.test/p/{n}.jpg" for n in range(9)] + ["https://img.test/p/packshot.jpg"]
    assert order_gallery(urls)[0] == "https://img.test/p/packshot.jpg"
    assert len(order_gallery(urls)) == 8


@pytest.mark.parametrize(
    ("url", "verdict"),
    [
        ("https://img.test/a/packshot_1.jpg", True),
        ("https://img.test/a/Flat-lay.jpg", True),
        ("https://img.test/a/hanger.png?v=2", True),
        ("https://img.test/a/model_3.jpg", False),
        ("https://img.test/a/LOOKBOOK-2.jpg", False),
        ("https://img.test/a/flatter-2.jpg", None),  # `flat` only counts as a whole word
        ("https://img.test/a/modeller/2.jpg", None),
        ("https://img.test/a/2.jpg?model=1", None),  # the query string is not looked at
        ("https://cdn.twist.com.tr/mnresize/2400/-/Twist/products/FW2627/Stillife/TW6260052109001.jpg", True),
        ("https://cdn.twist.com.tr/mnresize/2400/-/Twist/products/FW2627/Koleksiyon/TW6260025124003_0.jpg", None),
        ("https://images.jackjones.com.tr/12156101/5027764/001/x.png?v=1", True),
        ("https://images.jackjones.com.tr/12156101/5027764/003/x.jpg?v=1", None),
    ],
)
def test_person_free_hint(url: str, verdict: bool | None) -> None:
    assert person_free_hint(url) is verdict


def test_first_is_person_free() -> None:
    assert first_is_person_free(["https://img.test/packshot.jpg", "https://img.test/2.jpg"])
    assert not first_is_person_free(["https://img.test/1.jpg"])
    assert not first_is_person_free([])


def test_the_backend_limit_is_six() -> None:
    assert MAX_IMAGES == 6  # UpsertOffers/Validator.cs: MaxImages = 6


# ------------------------------------------------------------------------------------- per source


def test_derimod_gallery_follows_position_not_array_order() -> None:
    product = derimod_products()[0]
    images = product["images"]
    assert len(images) >= 3
    expected = [i["src"] for i in sorted(images, key=lambda i: i["position"])]
    product["images"] = list(reversed(images))
    assert derimod.gallery_urls(product) == expected
    offer = derimod.normalize_product(product, "https://derimod.com.tr/products/x")
    assert offer.image_urls == expected[:MAX_IMAGES]


def test_koton_gallery_is_the_whole_active_set_in_site_order() -> None:
    images = galleries()["koton"]
    data = fixture_json("koton/product_gomlek.json")
    data["product"]["productimage_set"] = images
    urls = koton.gallery_urls(data["product"])
    assert urls == [i["image"] for i in sorted(images, key=lambda i: i["order"])]
    assert len(urls) == 5  # Koton gives no hint in file names or data: the site's order stands
    images[1]["status"] = "passive"
    assert koton.gallery_urls(data["product"]) == [urls[0], *urls[2:]]
    assert koton.normalize_product(fixture_json("koton/product_gomlek.json"), "https://www.koton.com/x/").image_urls


def test_jack_jones_cutouts_come_first_and_the_gallery_is_capped_at_eight() -> None:
    product = _jj_product()
    media = product["media"]
    assert len(media) == 9
    urls = jackjones.gallery_urls(product)
    assert len(urls) == GALLERY_CAP
    png_count = sum(m["mimeType"] == "image/png" for m in media)
    assert png_count == 5
    assert all(u.split("?")[0].endswith(".png") for u in urls[:png_count])
    assert all(not u.split("?")[0].endswith(".png") for u in urls[png_count:])
    # within each group the site's order is kept: 001, 002, 006, 007, 015 then 003, 004, 005, 008
    assert [u.split("/")[-2] for u in urls] == ["001", "002", "006", "007", "015", "003", "004", "005"]
    offer = jackjones.normalize_product(product, "https://www.jackjones.com.tr/tr-tr/product/x")
    assert offer.image_urls == urls[:MAX_IMAGES]
    assert first_is_person_free(offer.image_urls)


def test_hotic_gallery_has_all_eight_pictures_in_display_order() -> None:
    pictures = galleries()["hotic"]
    assert len(pictures) == 8
    record = copy.deepcopy(next(iter(hotic_records("listing_erkek-loafer.json").values())))
    record["pictures"] = list(reversed(pictures))
    urls = hotic.gallery_urls(record)
    assert urls == [p["url"] for p in sorted(pictures, key=lambda p: p["displayOrder"])]
    assert len(urls) == 8
    assert hotic.normalize_product(record, "https://www.hotic.com.tr/x-p-1").image_urls == urls[:MAX_IMAGES]


def test_network_gallery_has_all_seven_images_at_1000px() -> None:
    media = galleries()["network"]
    assert len(media) == 7
    record = copy.deepcopy(next(iter(network_records("listing_kadin-bot.html").values())))
    record["MediaList"] = media
    urls = network.gallery_urls(record)
    assert len(urls) == 7
    assert all("/mnresize/1000/-/productimages/" in u for u in urls)
    assert [u.split("_")[-1].split("?")[0] for u in urls] == [f"0{n}.jpg" for n in range(1, 8)]
    assert network.normalize_product(record, "https://www.network.com.tr/x-p-1").image_urls == urls[:MAX_IMAGES]


def test_network_gallery_follows_display_order_not_file_numbers() -> None:
    record = copy.deepcopy(next(iter(network_records("listing_kadin-bot.html").values())))
    record["MediaList"] = galleries()["network_reordered"]  # real boots: DisplayOrder 2, 1, 3, 4, 5, 6 for _01 .. _06
    urls = network.gallery_urls(record)
    assert [u.split("_")[-1].split("?")[0] for u in urls] == ["02.jpg", "01.jpg", "03.jpg", "04.jpg", "05.jpg", "06.jpg"]


def test_twist_koleksiyon_gallery_keeps_its_order() -> None:
    product, _ld = twist_product()
    product["documents"] = galleries()["twist_koleksiyon"]
    urls = twist.gallery_urls(product)
    assert len(urls) == 7
    assert urls[0].endswith("/Koleksiyon/TW6260025124003_0.jpg")  # the default image stays first
    assert all("/Koleksiyon/" in u for u in urls)


def test_twist_stillife_gallery_is_person_free_first() -> None:
    product, _ld = twist_product()
    product["documents"] = galleries()["twist_stillife"]
    urls = twist.gallery_urls(product)
    assert len(urls) == 6
    assert all("/Stillife/" in u for u in urls)
    assert first_is_person_free(urls)


def test_twist_moves_a_stillife_shot_ahead_of_the_collection_shots() -> None:
    """Hypothetical mixed product: the real folders of two real products on one page."""
    product, _ld = twist_product()
    collection, stillife = galleries()["twist_koleksiyon"], galleries()["twist_stillife"]
    product["documents"] = [*collection, stillife[0]]
    urls = twist.gallery_urls(product)
    assert "/Stillife/" in urls[0] and len(urls) == GALLERY_CAP
    assert urls[1].endswith("/Koleksiyon/TW6260025124003_0.jpg")


def test_colins_gallery_is_the_ld_json_list_whole() -> None:
    html = fixture_text("colins/product_jean-sold-out.html")
    ld = find_ld_type(html, "Product")
    assert ld is not None and len(ld["image"]) == 8
    assert colins.gallery_urls(ld, {}) == ld["image"]  # no hint on this site: its order stands
    gomlek = find_ld_type(fixture_text("colins/product_gomlek.html"), "Product")
    assert gomlek is not None and colins.gallery_urls(gomlek, {}) == gomlek["image"] and len(gomlek["image"]) == 6


def test_colins_falls_back_to_the_card_images_and_accepts_a_single_image_string() -> None:
    assert colins.gallery_urls({}, {"imageUrls": ["https://img.test/a.jpg", "https://img.test/b.jpg"]}) == [
        "https://img.test/a.jpg",
        "https://img.test/b.jpg",
    ]
    assert colins.gallery_urls({"image": "https://img.test/a.jpg"}, {}) == ["https://img.test/a.jpg"]
