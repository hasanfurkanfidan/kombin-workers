"""Pydantic models mirroring the backend's upsert body (`docs/openapi.json`).

Enum members are copied from the OpenAPI schema (`ProductColor`, `ProductFit`, `ProductPattern`, `StockStatus`,
`Audience`, `SellerKind`); seasons come from the backend validator (Spring, Summer, Autumn, Winter). The wire format
is camelCase; use `model_dump(by_alias=True, mode="json")` (see `to_wire`).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


class StockStatus(StrEnum):
    IN_STOCK = "InStock"
    LOW_STOCK = "LowStock"
    OUT_OF_STOCK = "OutOfStock"
    UNKNOWN = "Unknown"


class Audience(StrEnum):
    WOMEN = "Women"
    MEN = "Men"
    UNISEX = "Unisex"
    KIDS = "Kids"


class PrimaryColor(StrEnum):
    BLACK = "Black"
    WHITE = "White"
    GREY = "Grey"
    NAVY = "Navy"
    BLUE = "Blue"
    GREEN = "Green"
    BEIGE = "Beige"
    BROWN = "Brown"
    RED = "Red"
    PINK = "Pink"
    PURPLE = "Purple"
    YELLOW = "Yellow"
    ORANGE = "Orange"
    MULTI = "Multi"
    OTHER = "Other"


class Fit(StrEnum):
    SLIM = "Slim"
    REGULAR = "Regular"
    RELAXED = "Relaxed"
    OVERSIZED = "Oversized"
    UNKNOWN = "Unknown"


class Pattern(StrEnum):
    SOLID = "Solid"
    STRIPED = "Striped"
    CHECKED = "Checked"
    PRINTED = "Printed"
    TEXTURED = "Textured"
    OTHER = "Other"


class Season(StrEnum):
    SPRING = "Spring"
    SUMMER = "Summer"
    AUTUMN = "Autumn"
    WINTER = "Winter"


class SellerKind(StrEnum):
    MARKETPLACE = "Marketplace"
    BRAND_STORE = "BrandStore"


MAX_IMAGES = 6  # the backend rejects more than 6 `imageUrls` per item (UpsertOffers/Validator.cs); sources collect up to 8


def _strip(value: str) -> str:
    return value.strip()


def _http_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")) or " " in value:
        raise ValueError(f"not an absolute http(s) URL: {value!r}")
    return value


Text = Annotated[str, AfterValidator(_strip)]
HttpUrl = Annotated[str, AfterValidator(_http_url), Field(max_length=2048)]


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, use_enum_values=False)

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


class SellerRegistration(WireModel):
    """Body of `PUT /internal/catalog/sellers/{slug}`. PUT replaces the whole seller, so a source owns every field."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    kind: SellerKind
    website_url: HttpUrl
    affiliate_network: str | None = None
    affiliate_url_template: str | None = None
    affiliate_params: dict[str, str] | None = None
    is_active: bool = True


class NormalizedProduct(WireModel):
    """The `product` block of an upsert item. Identity fields (brand, name, modelCode, category) never change later."""

    brand_name: Annotated[str, Field(min_length=1, max_length=100)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    model_code: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    category_slug: Annotated[str, Field(min_length=1, max_length=100)]
    audience: Audience
    primary_color: PrimaryColor
    pattern: Pattern | None = None
    fit: Fit | None = None
    formality: Annotated[int, Field(ge=1, le=5)] = 3
    seasons: list[Season] | None = None
    material: Annotated[str, Field(max_length=200)] | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None
    canonical_product_id: str | None = None
    brand_website_url: HttpUrl | None = None
    brand_logo_url: HttpUrl | None = None

    @field_validator("name", "brand_name", mode="after")
    @classmethod
    def _needs_alphanumeric(cls, value: str) -> str:
        if not any(ch.isalnum() for ch in value):
            raise ValueError("must contain at least one letter or digit")
        return value

    @field_validator("model_code", mode="before")
    @classmethod
    def _blank_model_code(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class NormalizedOffer(WireModel):
    """One upsert item: the seller's listing plus the product it describes."""

    external_id: Annotated[str, Field(min_length=1, max_length=200)]
    url: HttpUrl
    title: Annotated[str, Field(min_length=1, max_length=300)]
    price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    original_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)] | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "TRY"
    shipping_cost: Annotated[Decimal, Field(ge=0)] | None = None
    free_shipping_threshold: Annotated[Decimal, Field(ge=0)] | None = None
    stock_status: StockStatus = StockStatus.UNKNOWN
    available_sizes: list[Annotated[str, Field(min_length=1, max_length=20)]] = Field(
        default_factory=list, max_length=50
    )
    image_urls: list[HttpUrl] = Field(min_length=1, max_length=6)
    product: NormalizedProduct

    @model_validator(mode="after")
    def _crossed_out_price_must_be_higher(self) -> NormalizedOffer:
        if self.original_price is not None and self.original_price <= self.price:
            self.original_price = None
        return self

    @field_validator("available_sizes", mode="after")
    @classmethod
    def _unique_sizes(cls, sizes: list[str]) -> list[str]:
        return list(dict.fromkeys(sizes))

    @field_validator("image_urls", mode="before")
    @classmethod
    def _unique_capped_images(cls, urls: object) -> object:
        """Drop duplicates and blanks, keep the first six (the backend limit)."""
        if isinstance(urls, list):
            return list(dict.fromkeys(u.strip() for u in urls if isinstance(u, str) and u.strip()))[:MAX_IMAGES]
        return urls

    @field_serializer("price", "original_price", "shipping_cost", "free_shipping_threshold")
    def _serialize_money(self, value: Decimal | None) -> float | None:
        return None if value is None else _money(value)


NormalizedItem = NormalizedOffer
