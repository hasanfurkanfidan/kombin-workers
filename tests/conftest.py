from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import respx

from kombin_workers.http import PoliteClient
from kombin_workers.logging_setup import configure_logging
from kombin_workers.models import Audience, NormalizedOffer, NormalizedProduct, PrimaryColor, StockStatus

FIXTURES = Path(__file__).parent / "fixtures"
ALLOW_ALL = "User-agent: *\nDisallow:\n"


def fixture_text(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def fixture_json(relative: str) -> Any:
    return json.loads(fixture_text(relative))


@pytest.fixture(autouse=True)
def _quiet_logs() -> None:
    configure_logging(json_logs=True, level=logging.DEBUG)


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def allow_robots(router: respx.MockRouter, origin: str, text: str = ALLOW_ALL) -> respx.Route:
    return router.get(f"{origin}/robots.txt").respond(200, text=text)


@pytest.fixture
async def http() -> AsyncIterator[PoliteClient]:
    """A client that is fast enough for tests (1000 requests/second, no backoff wait)."""
    client = PoliteClient(user_agent="KombinBot/test", requests_per_second=1000, backoff_base=0.0)
    try:
        yield client
    finally:
        await client.aclose()


def make_offer(n: int = 1, **overrides: Any) -> NormalizedOffer:
    """A valid `NormalizedOffer`; `n` makes externalId, url and modelCode unique."""
    product = NormalizedProduct(
        brand_name="Koton",
        name=f"Basic Tişört {n}",
        model_code=f"MC-{n}",
        category_slug="ust-giyim/tisort",
        audience=Audience.MEN,
        primary_color=PrimaryColor.BLACK,
        formality=2,
    )
    fields: dict[str, Any] = {
        "external_id": f"ext-{n}",
        "url": f"https://www.koton.com/p-{n}/",
        "title": f"Basic Tişört {n}",
        "price": "199.90",
        "stock_status": StockStatus.IN_STOCK,
        "available_sizes": ["S", "M"],
        "image_urls": [f"https://img.test/{n}.jpg"],
        "product": product,
    }
    fields.update(overrides)
    return NormalizedOffer(**fields)
