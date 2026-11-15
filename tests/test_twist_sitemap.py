"""Twist discovery through the sitemaps listed in robots.txt (task W3), against the real sitemap files saved on 2026-11-15
(`tests/fixtures/twist/sitemap.xml`, the real index; `sitemap_products.xml`, 44 of the ~1260 real product URLs)."""

from __future__ import annotations

import collections

import respx

from kombin_workers.http import PoliteClient
from kombin_workers.sources import twist
from kombin_workers.sources.sitemap import parse_sitemap
from tests.conftest import allow_robots, fixture_text

TWIST_URL = "https://www.twist.com.tr"
PRODUCTS_SITEMAP = f"{TWIST_URL}/export/Twist/sitemap/products-1.xml"


def mock_sitemaps(router: respx.MockRouter, robots: str | None = None) -> dict[str, respx.Route]:
    allow_robots(router, TWIST_URL, robots or fixture_text("twist/robots.txt"))
    return {
        "index": router.get(f"{TWIST_URL}/sitemap.xml").respond(200, text=fixture_text("twist/sitemap.xml")),
        "products": router.get(PRODUCTS_SITEMAP).respond(200, text=fixture_text("twist/sitemap_products.xml")),
        "categories": router.get(f"{TWIST_URL}/sitemap-categories-tr.xml").respond(200, text="<urlset></urlset>"),
        "blogs": router.get(f"{TWIST_URL}/sitemap-blogs-tr.xml").respond(200, text="<urlset></urlset>"),
    }


def test_parse_sitemap_index_and_urlset() -> None:
    index = parse_sitemap(fixture_text("twist/sitemap.xml"))
    assert index.urls == []
    assert index.children == [
        PRODUCTS_SITEMAP,
        f"{TWIST_URL}/sitemap-categories-tr.xml",
        f"{TWIST_URL}/sitemap-blogs-tr.xml",
    ]
    products = parse_sitemap(fixture_text("twist/sitemap_products.xml"))
    assert products.children == []
    assert len(products.urls) == 45
    assert products.urls[0] == TWIST_URL
    assert all(u.startswith(f"{TWIST_URL}/urun/") for u in products.urls[1:])


def test_parse_sitemap_unescapes_entities_and_ignores_junk() -> None:
    xml = "<urlset><url><loc> https://a.test/x?a=1&amp;b=2 </loc></url><url><loc>javascript:alert(1)</loc></url></urlset>"
    assert parse_sitemap(xml).urls == ["https://a.test/x?a=1&b=2"]
    assert parse_sitemap("not xml at all").urls == []


async def test_twist_discovery_reads_the_product_sitemap_only(router: respx.MockRouter, http: PoliteClient) -> None:
    routes = mock_sitemaps(router)
    refs = [r async for r in twist.TwistSource(http).discover()]
    assert routes["index"].call_count == 1 and routes["products"].call_count == 1
    assert routes["categories"].call_count == 0 and routes["blogs"].call_count == 0  # only product sitemaps are followed
    assert refs and all(r.url.startswith(f"{TWIST_URL}/urun/") for r in refs)
    assert all("page=" not in str(call.request.url) for call in router.calls)  # still no `?page=`
    assert len({r.key for r in refs}) == len(refs)


async def test_twist_sitemap_drops_categories_we_do_not_have_before_any_product_request(
    router: respx.MockRouter, http: PoliteClient
) -> None:
    mock_sitemaps(router)
    refs = [r async for r in twist.TwistSource(http).discover()]
    slugs = {r.key for r in refs}
    assert not any(s.endswith(("elbise", "bluz", "yelek", "sweatshirt", "bermuda")) for s in slugs)
    assert all(r.category_hint for r in refs)
    assert len(refs) < 44  # dresses, blouses, ... were left out of the 44 product URLs


async def test_twist_sitemap_refs_are_dealt_out_one_category_at_a_time(router: respx.MockRouter, http: PoliteClient) -> None:
    """A capped run takes an even share: the first refs cover several categories instead of exhausting one."""
    mock_sitemaps(router)
    refs = [r async for r in twist.TwistSource(http).discover()]
    categories = {r.category_hint for r in refs}
    assert len(categories) >= 8
    first_round = [r.category_hint for r in refs[: len(categories)]]
    assert len(set(first_round)) == len(categories)
    assert max(collections.Counter(r.category_hint for r in refs[:20]).values()) <= 2


async def test_twist_sitemap_urls_that_robots_disallows_are_not_fetched(router: respx.MockRouter, http: PoliteClient) -> None:
    robots = fixture_text("twist/robots.txt").replace("User-agent: *\n", "User-agent: *\nDisallow: /urun/*mont\n", 1)
    mock_sitemaps(router, robots)
    refs = [r async for r in twist.TwistSource(http).discover()]
    assert refs and not any(r.key.endswith("mont") for r in refs)


async def test_twist_falls_back_to_category_pages_when_the_sitemap_has_no_products(
    router: respx.MockRouter, http: PoliteClient
) -> None:
    allow_robots(router, TWIST_URL, fixture_text("twist/robots.txt"))
    router.get(f"{TWIST_URL}/sitemap.xml").respond(200, text="<urlset></urlset>")
    router.get(url__regex=rf"{TWIST_URL}/[a-z/-]+").respond(404)
    assert [r async for r in twist.TwistSource(http).discover()] == []  # fallback pages 404 here; nothing invented
