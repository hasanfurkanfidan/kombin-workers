from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from kombin_workers.cli import app
from kombin_workers.scheduler import daemon_loop
from kombin_workers.settings import get_settings
from tests.conftest import allow_robots, fixture_json, fixture_text

BASE = "http://backend.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOMBIN_API_URL", BASE)
    monkeypatch.setenv("KOMBIN_INTERNAL_KEY", "test-key")
    monkeypatch.setenv("KOMBIN_REQUESTS_PER_SECOND", "1000")
    monkeypatch.setenv("BOT_CONTACT", "tests@example.org")
    get_settings.cache_clear()


def mock_derimod(router: respx.MockRouter) -> None:
    allow_robots(router, "https://derimod.com.tr", fixture_text("derimod/robots.txt"))
    router.get(url__regex=r"https://derimod\.com\.tr/collections/.*").respond(
        200, json=fixture_json("derimod/collection_erkek-deri-sneaker.json")
    )


def test_sources_lists_every_source() -> None:
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if "seller=" in line]
    assert [line.split()[0] for line in lines] == ["colins", "derimod", "hotic", "jack-jones", "koton", "network", "twist"]
    assert all("BLOCKED" not in line for line in lines)


def test_unknown_source_is_a_usage_error() -> None:
    result = runner.invoke(app, ["check", "zara"])
    assert result.exit_code == 2
    assert "unknown source 'zara'" in result.output


def test_check_prints_normalized_items_as_json_and_calls_no_backend(router: respx.MockRouter) -> None:
    mock_derimod(router)
    result = runner.invoke(app, ["check", "derimod", "--limit", "2", "--max-pages", "1"])
    assert result.exit_code == 0, result.output
    items = json.loads(result.stdout)
    assert len(items) == 2
    assert items[0]["product"]["brandName"] == "Derimod"
    assert items[0]["imageUrls"]
    assert not any(str(call.request.url).startswith(BASE) for call in router.calls)


def test_the_per_source_cap_comes_from_settings(router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_derimod(router)
    monkeypatch.setenv("KOMBIN_MAX_ITEMS_PER_SOURCE", "2")
    get_settings.cache_clear()
    result = runner.invoke(app, ["check", "derimod", "--limit", "10", "--max-pages", "1"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 2


def test_run_registers_the_seller_then_upserts(router: respx.MockRouter) -> None:
    mock_derimod(router)
    seller = router.put(f"{BASE}/api/v1/internal/catalog/sellers/derimod-store").respond(
        200, json={"id": "s", "slug": "derimod-store", "name": "Derimod", "kind": "BrandStore", "created": True}
    )
    upsert = router.post(f"{BASE}/api/v1/internal/catalog/offers/upsert").respond(
        200, json={"created": 3, "updated": 0, "snapshotsWritten": 3, "results": [], "errors": []}
    )
    result = runner.invoke(app, ["run", "derimod", "--limit", "3", "--max-pages", "1"])
    assert result.exit_code == 0, result.output
    assert "discovered:      3" in result.stdout
    assert "created=3" in result.stdout
    assert seller.calls.last.request.headers["X-Internal-Key"] == "test-key"
    assert len(json.loads(upsert.calls.last.request.content)["items"]) == 3


def test_run_without_a_key_explains_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOMBIN_INTERNAL_KEY", "")
    get_settings.cache_clear()
    result = runner.invoke(app, ["run", "derimod"])
    assert result.exit_code == 2
    assert "KOMBIN_INTERNAL_KEY is not set" in result.output


def test_run_exits_3_when_robots_blocks_the_source(router: respx.MockRouter) -> None:
    allow_robots(router, "https://derimod.com.tr", "User-agent: *\nDisallow: /collections/\n")
    router.put(url__regex=rf"{BASE}/api/v1/internal/catalog/sellers/.*").respond(
        200, json={"id": "s", "slug": "derimod-store", "name": "Derimod", "kind": "BrandStore", "created": True}
    )
    result = runner.invoke(app, ["run", "derimod", "--max-pages", "1"])
    assert result.exit_code == 3
    assert "BLOCKED" in result.stdout


def test_sweep_all_and_one_seller(router: respx.MockRouter) -> None:
    route = router.post(f"{BASE}/api/v1/internal/catalog/offers/sweep").respond(
        200, json={"offersDeactivated": 4, "productsDeactivated": 2, "productsReactivated": 0}
    )
    result = runner.invoke(app, ["sweep"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["offers_deactivated"] == 4
    assert json.loads(route.calls.last.request.content) == {"sellerSlug": None}
    runner.invoke(app, ["sweep", "--seller", "koton-store"])
    assert json.loads(route.calls.last.request.content) == {"sellerSlug": "koton-store"}


def test_backend_errors_exit_1(router: respx.MockRouter) -> None:
    router.post(f"{BASE}/api/v1/internal/catalog/offers/sweep").respond(401, json={"detail": "bad key"})
    result = runner.invoke(app, ["sweep"])
    assert result.exit_code == 1
    assert "401" in result.output


# ------------------------------------------------------------------------------------------ daemon schedule


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_daemon_runs_every_cycle_and_sweeps_on_its_own_schedule() -> None:
    clock, jobs = Clock(), []

    async def run_all() -> None:
        jobs.append(("run", clock.now))

    async def sweep() -> None:
        jobs.append(("sweep", clock.now))

    await daemon_loop(
        run_all=run_all, sweep=sweep, run_every=100, sweep_every=250, jitter=0, clock=clock, sleep=clock.sleep, max_cycles=5
    )
    assert jobs == [("run", 0), ("run", 100), ("run", 200), ("sweep", 250), ("run", 300)]


async def test_daemon_applies_jitter_and_survives_a_failing_job() -> None:
    clock, calls = Clock(), []

    async def run_all() -> None:
        calls.append(clock.now)
        raise RuntimeError("site down")

    async def sweep() -> None:
        return None

    await daemon_loop(
        run_all=run_all, sweep=sweep, run_every=100, sweep_every=10_000, jitter=30, rand=lambda: 0.5,
        clock=clock, sleep=clock.sleep, max_cycles=3,
    )
    assert calls == [0, 115, 230]  # every 100 s plus a jitter of 0.5 * 30 s, even though the job keeps failing


def test_daemon_command_is_registered() -> None:
    result = runner.invoke(app, ["daemon", "--help"])
    assert result.exit_code == 0
    assert "every 6 hours" in result.stdout
    assert asyncio.iscoroutinefunction(daemon_loop)
    assert httpx  # imported for parity with the other CLI tests
