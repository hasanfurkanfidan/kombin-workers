from __future__ import annotations

import time

import httpx
import pytest
import respx

from kombin_workers.http import (
    FetchError,
    PoliteClient,
    RateLimiter,
    RobotsBlocked,
    RobotsRules,
    SourceAborted,
)
from kombin_workers.settings import Settings
from tests.conftest import allow_robots, fixture_text


class FakeTime:
    """A clock that only moves when somebody sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client_with(fake: FakeTime, *, backoff_base: float = 1.0) -> PoliteClient:
    return PoliteClient(user_agent="KombinBot/test", clock=fake.clock, sleep=fake.sleep, backoff_base=backoff_base)


# --------------------------------------------------------------------------------------------- rate limiting


async def test_limiter_spaces_slots_one_interval_apart() -> None:
    fake = FakeTime()
    limiter = RateLimiter(1.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(4):
        await limiter.wait()
    assert fake.sleeps == [1.0, 1.0, 1.0]  # the first slot is free


async def test_limiter_does_not_delay_a_request_that_is_already_late() -> None:
    fake = FakeTime()
    limiter = RateLimiter(1.0, clock=fake.clock, sleep=fake.sleep)
    await limiter.wait()
    fake.now += 5
    await limiter.wait()
    assert fake.sleeps == []


async def test_default_rate_is_one_request_per_second_per_host(router: respx.MockRouter) -> None:
    fake = FakeTime()
    allow_robots(router, "https://a.test")
    allow_robots(router, "https://b.test")
    router.get(url__regex=r"https://[ab]\.test/p.*").respond(200, json={})
    async with client_with(fake) as client:
        for n in range(3):
            await client.get(f"https://a.test/p{n}")
        # robots.txt + three pages = four requests, three waits of 1s; the other host starts its own clock
        assert fake.sleeps == [1.0, 1.0, 1.0]
        await client.get("https://b.test/p0")
        assert fake.sleeps == [1.0, 1.0, 1.0, 1.0]  # b.test: its robots.txt was free, then its page waited 1s
    assert client.requests_made == 6


async def test_wall_clock_rate(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="KombinBot/test", requests_per_second=20) as client:  # 50 ms apart
        started = time.monotonic()
        for _ in range(5):
            await client.get("https://a.test/p")
        elapsed = time.monotonic() - started
    assert elapsed >= 0.19  # 6 requests including robots.txt: at least 5 gaps of 50 ms


async def test_crawl_delay_slows_the_host_down(router: respx.MockRouter) -> None:
    fake = FakeTime()
    allow_robots(router, "https://a.test", "User-agent: *\nCrawl-delay: 5\n")
    router.get("https://a.test/p").respond(200, json={})
    async with client_with(fake) as client:
        await client.get("https://a.test/p")
        await client.get("https://a.test/p")
    assert fake.sleeps[-1] == 5.0


# --------------------------------------------------------------------------------------------- retries


async def test_server_errors_are_retried_with_exponential_backoff(router: respx.MockRouter) -> None:
    fake = FakeTime()
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").mock(
        side_effect=[httpx.Response(503), httpx.Response(500), httpx.Response(502), httpx.Response(200, json={"ok": 1})]
    )
    async with client_with(fake, backoff_base=2.0) as client:
        assert await client.get_json("https://a.test/p") == {"ok": 1}
    assert route.call_count == 4
    waits = [s for s in fake.sleeps if s >= 2.0]
    assert len(waits) == 3
    for wait, expected in zip(waits, (2.0, 4.0, 8.0), strict=True):  # base * 2**attempt, plus up to 10% jitter
        assert expected <= wait <= expected * 1.1 + 1e-9


async def test_retry_after_is_honoured_on_429(router: respx.MockRouter) -> None:
    fake = FakeTime()
    allow_robots(router, "https://a.test")
    router.get("https://a.test/p").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "17"}), httpx.Response(200, json={})]
    )
    async with client_with(fake) as client:
        await client.get("https://a.test/p")
    assert 17.0 in fake.sleeps


async def test_transport_errors_are_retried(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").mock(side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={})])
    async with PoliteClient(user_agent="t", requests_per_second=1000, backoff_base=0) as client:
        await client.get("https://a.test/p")
    assert route.call_count == 2


async def test_gives_up_after_the_retry_budget(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").respond(503)
    async with PoliteClient(user_agent="t", requests_per_second=1000, backoff_base=0, max_retries=2) as client:
        with pytest.raises(FetchError) as caught:
            await client.get("https://a.test/p")
    assert caught.value.status == 503
    assert route.call_count == 3


async def test_not_found_is_final_and_does_not_count_as_a_failure(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/gone").respond(404)
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        with pytest.raises(FetchError) as caught:
            await client.get("https://a.test/gone")
        assert caught.value.status == 404
        assert client.consecutive_failures == 0
    assert route.call_count == 1


async def test_forbidden_is_not_retried_but_counts_as_a_failure(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").respond(403)
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        with pytest.raises(FetchError):
            await client.get("https://a.test/p")
        assert client.consecutive_failures == 1
    assert route.call_count == 1


async def test_twenty_consecutive_failures_abort_the_source(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").respond(500)
    async with PoliteClient(user_agent="t", requests_per_second=1000, backoff_base=0, max_retries=4) as client:
        with pytest.raises(FetchError):
            await client.get("https://a.test/p")  # 5 attempts
        with pytest.raises(FetchError):
            await client.get("https://a.test/p")  # 10
        with pytest.raises(FetchError):
            await client.get("https://a.test/p")  # 15
        with pytest.raises(SourceAborted):
            await client.get("https://a.test/p")  # the 20th failure stops everything
        assert client.consecutive_failures == 20
    assert route.call_count == 20


async def test_a_success_resets_the_failure_streak(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    router.get("https://a.test/p").mock(side_effect=[httpx.Response(500)] * 3 + [httpx.Response(200, json={})])
    async with PoliteClient(user_agent="t", requests_per_second=1000, backoff_base=0) as client:
        await client.get("https://a.test/p")
        assert client.consecutive_failures == 0


async def test_user_agent_and_accept_headers(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    route = router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="KombinBot/0.1 (+https://kombin.app/bot; contact: [YOUR EMAIL])", requests_per_second=1000) as client:
        await client.get_json("https://a.test/p")
    request = route.calls.last.request
    assert request.headers["User-Agent"] == "KombinBot/0.1 (+https://kombin.app/bot; contact: [YOUR EMAIL])"
    assert request.headers["Accept"] == "application/json"


async def test_non_json_body_is_a_fetch_error(router: respx.MockRouter) -> None:
    allow_robots(router, "https://a.test")
    router.get("https://a.test/p").respond(200, text="<html>")
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        with pytest.raises(FetchError):
            await client.get_json("https://a.test/p")


BARE_UA = "KombinBot/0.1 (+https://kombin.app/bot)"


@pytest.mark.parametrize("value", [None, "", "   ", "[YOUR EMAIL]", "[your email]", "[anything in brackets]"])
def test_user_agent_omits_the_contact_part_when_unset_or_still_the_placeholder(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.delenv("KOMBIN_BOT_CONTACT", raising=False)
    if value is None:
        monkeypatch.delenv("BOT_CONTACT", raising=False)
    else:
        monkeypatch.setenv("BOT_CONTACT", value)
    settings = Settings(_env_file=None)
    assert settings.user_agent == BARE_UA
    assert "YOUR EMAIL" not in settings.user_agent.upper()
    assert not settings.has_bot_contact


def test_user_agent_carries_a_real_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KOMBIN_BOT_CONTACT", raising=False)
    monkeypatch.setenv("BOT_CONTACT", " ops@example.org ")
    settings = Settings(_env_file=None)
    assert settings.user_agent == "KombinBot/0.1 (+https://kombin.app/bot; contact: ops@example.org)"
    assert settings.has_bot_contact


# --------------------------------------------------------------------------------------------- robots.txt


async def test_robots_disallowed_url_is_never_requested(router: respx.MockRouter) -> None:
    allow_robots(router, "https://www.koton.com", fixture_text("koton/robots.txt"))
    blocked = router.get(url__startswith="https://www.koton.com/list/").respond(200, text="x")
    allowed = router.get("https://www.koton.com/erkek-gomlek/?format=json&page=1").respond(200, json={"products": []})
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        with pytest.raises(RobotsBlocked):
            await client.get("https://www.koton.com/list/?search_text=gomlek")
        await client.get("https://www.koton.com/erkek-gomlek/?format=json&page=1")
    assert blocked.call_count == 0
    assert allowed.call_count == 1


async def test_robots_is_requested_like_a_browser_would_with_the_default_accept_header(router: respx.MockRouter) -> None:
    # Koton answers 500 to `Accept: text/plain` on /robots.txt, so the request must not set one
    robots = allow_robots(router, "https://a.test")
    router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        await client.get_json("https://a.test/p")
    assert robots.calls.last.request.headers["Accept"] == "*/*"


async def test_robots_cache_fetches_robots_once_per_host(router: respx.MockRouter) -> None:
    robots = allow_robots(router, "https://a.test")
    router.get(url__regex=r"https://a\.test/.*").respond(200, json={})
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        for n in range(3):
            await client.get(f"https://a.test/p{n}")
    assert robots.call_count == 1


@pytest.mark.parametrize(
    ("site", "url", "allowed"),
    [
        ("koton", "https://www.koton.com/erkek-gomlek/?format=json&page=2", True),
        ("koton", "https://www.koton.com/kapakli-gomlek-gri-4085984/?format=json", True),
        ("koton", "https://www.koton.com/list/?search_text=x", False),
        ("koton", "https://www.koton.com/shopping-page/abc", False),
        ("koton", "https://www.koton.com/erkek-gomlek/?category_ids=12", False),  # `/*?category_ids=` wildcard
        ("koton", "https://www.koton.com/baskets/basket/", False),
        ("derimod", "https://derimod.com.tr/collections/erkek-bot/products.json?limit=100&page=1", True),
        ("derimod", "https://derimod.com.tr/products/erkek-siyah-bot", True),
        ("derimod", "https://derimod.com.tr/search?q=bot", False),
        ("derimod", "https://derimod.com.tr/collections/erkek-bot?sort_by=price-ascending", False),
        ("derimod", "https://derimod.com.tr/cart", False),
        ("jack-jones", "https://www.jackjones.com.tr/tr-tr/erkek-giyim/tisort?page=2", True),
        ("jack-jones", "https://www.jackjones.com.tr/tr-tr/product/12298420_2161/rahat-kalip-bisiklet-yaka-tisort", True),
        ("jack-jones", "https://www.jackjones.com.tr/tr-tr/arama?q=tisort", False),
        ("jack-jones", "https://www.jackjones.com.tr/tr-tr/checkout", False),
    ],
)
def test_the_real_robots_files_of_our_sources(site: str, url: str, allowed: bool) -> None:
    assert RobotsRules.from_text(fixture_text(f"{site}/robots.txt")).allowed(url) is allowed


def test_longest_match_wins_and_allow_wins_ties() -> None:
    rules = RobotsRules.from_text("User-agent: *\nDisallow: /a/\nAllow: /a/b\nDisallow: /p\nAllow: /p")
    assert rules.allowed("https://s.test/a/c") is False
    assert rules.allowed("https://s.test/a/b/c") is True
    assert rules.allowed("https://s.test/p/1") is True  # same specificity: Allow wins


def test_blank_lines_do_not_end_a_group() -> None:
    # Koton and Jack & Jones write robots.txt like this; urllib.robotparser alone would read it as "allow everything"
    rules = RobotsRules.from_text("User-agent: *\n\nDisallow: /secret\n\n\nDisallow: /other\n")
    assert rules.allowed("https://s.test/secret") is False
    assert rules.allowed("https://s.test/other/x") is False
    assert rules.allowed("https://s.test/open") is True


def test_the_group_for_our_own_name_beats_the_wildcard_group() -> None:
    text = "User-agent: *\nDisallow: /\n\nUser-agent: KombinBot\nDisallow: /private\n"
    rules = RobotsRules.from_text(text)
    assert rules.allowed("https://s.test/products") is True
    assert rules.allowed("https://s.test/private/x") is False


def test_dollar_anchors_a_pattern() -> None:
    rules = RobotsRules.from_text("User-agent: *\nDisallow: /*.json$\n")
    assert rules.allowed("https://s.test/a.json") is False
    assert rules.allowed("https://s.test/a.json?x=1") is True


@pytest.mark.parametrize(("status", "allowed"), [(404, True), (410, True), (403, False), (401, False), (500, False), (None, False)])
def test_missing_or_unreadable_robots(status: int | None, allowed: bool) -> None:
    assert RobotsRules.from_status(status).allowed("https://s.test/anything") is allowed


async def test_robots_404_allows_everything(router: respx.MockRouter) -> None:
    router.get("https://a.test/robots.txt").respond(404)
    router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        await client.get("https://a.test/p")


async def test_robots_403_blocks_everything(router: respx.MockRouter) -> None:
    router.get("https://a.test/robots.txt").respond(403)
    page = router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="t", requests_per_second=1000) as client:
        with pytest.raises(RobotsBlocked):
            await client.get("https://a.test/p")
    assert page.call_count == 0


async def test_robots_check_can_be_switched_off_for_our_own_hosts(router: respx.MockRouter) -> None:
    page = router.get("https://a.test/p").respond(200, json={})
    async with PoliteClient(user_agent="t", requests_per_second=1000, respect_robots=False) as client:
        await client.get("https://a.test/p")
    assert page.call_count == 1


def test_per_source_cap_defaults_to_400_and_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).max_items_per_source == 400
    monkeypatch.setenv("KOMBIN_MAX_ITEMS_PER_SOURCE", "25")
    assert Settings(_env_file=None).max_items_per_source == 25
