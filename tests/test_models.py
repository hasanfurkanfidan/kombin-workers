from decimal import Decimal

import pytest
from pydantic import ValidationError

from kombin_workers.models import MAX_IMAGES, NormalizedProduct, SellerKind, SellerRegistration
from tests.conftest import make_offer


def test_wire_format_is_camel_case_with_numeric_money() -> None:
    wire = make_offer(7, price="1299.9", original_price="1599.90").to_wire()
    assert wire["externalId"] == "ext-7"
    assert wire["price"] == 1299.9
    assert wire["originalPrice"] == 1599.9
    assert wire["stockStatus"] == "InStock"
    assert wire["availableSizes"] == ["S", "M"]
    assert wire["imageUrls"] == ["https://img.test/7.jpg"]
    assert wire["currency"] == "TRY"
    assert wire["shippingCost"] is None
    product = wire["product"]
    assert product["brandName"] == "Koton"
    assert product["categorySlug"] == "ust-giyim/tisort"
    assert product["audience"] == "Men"
    assert product["primaryColor"] == "Black"
    assert product["formality"] == 2
    assert product["canonicalProductId"] is None


def test_a_crossed_out_price_must_be_higher_than_the_price() -> None:
    assert make_offer(price="100", original_price="80").original_price is None
    assert make_offer(price="100", original_price="100").original_price is None
    assert make_offer(price="100", original_price="120").original_price == Decimal("120")


def test_images_are_deduplicated_and_capped_at_six() -> None:
    urls = [f"https://img.test/{i}.jpg" for i in range(10)]
    offer = make_offer(image_urls=[urls[0], urls[0], *urls[1:]])
    assert offer.image_urls == urls[:MAX_IMAGES]


def test_an_item_needs_at_least_one_image() -> None:
    with pytest.raises(ValidationError):
        make_offer(image_urls=[])


@pytest.mark.parametrize("price", ["0", "-5", "abc"])
def test_price_must_be_positive(price: str) -> None:
    with pytest.raises(ValidationError):
        make_offer(price=price)


def test_urls_must_be_absolute_http() -> None:
    with pytest.raises(ValidationError):
        make_offer(url="/relative/path")
    with pytest.raises(ValidationError):
        make_offer(image_urls=["ftp://x/y.jpg"])


def test_sizes_are_deduplicated() -> None:
    assert make_offer(available_sizes=["M", "M", "L"]).available_sizes == ["M", "L"]


def test_product_name_needs_a_letter_or_digit_and_formality_is_bounded() -> None:
    base = {"brand_name": "Koton", "category_slug": "x", "audience": "Men", "primary_color": "Black"}
    with pytest.raises(ValidationError):
        NormalizedProduct.model_validate({"name": "---", **base})
    with pytest.raises(ValidationError):
        NormalizedProduct.model_validate({"name": "Tee", "formality": 6, **base})


def test_blank_model_code_becomes_none() -> None:
    product = NormalizedProduct.model_validate(
        {"brand_name": "Koton", "name": "Tee", "model_code": "  ", "category_slug": "x", "audience": "Men", "primary_color": "Black"}
    )
    assert product.model_code is None


def test_seller_registration_wire_format() -> None:
    wire = SellerRegistration(name="Koton", kind=SellerKind.BRAND_STORE, website_url="https://www.koton.com").to_wire()
    assert wire == {
        "name": "Koton",
        "kind": "BrandStore",
        "websiteUrl": "https://www.koton.com",
        "affiliateNetwork": None,
        "affiliateUrlTemplate": None,
        "affiliateParams": None,
        "isActive": True,
    }
