"""Polite HTTP: per-host rate limit, retries with backoff, robots.txt, a hard stop after repeated failures.

Every request to a site goes through `PoliteClient.get`. It (1) asks the host's cached robots.txt whether the URL may
be fetched, (2) waits for the host's next request slot (default 1 request/second, never faster), (3) retries 429/5xx
and transport errors with exponential backoff (honouring `Retry-After`), and (4) raises `SourceAborted` once
`max_consecutive_failures` requests in a row have failed, so a broken or hostile site is left alone.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog

log = structlog.get_logger(__name__)

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

ROBOTS_TOKEN = "KombinBot"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
HEALTHY_STATUSES = frozenset({404, 410})  # the server answered normally; the page just does not exist
MAX_BACKOFF_SECONDS = 60.0
MAX_RETRY_AFTER_SECONDS = 120.0


class FetchError(Exception):
    """A request ended without a usable response (non-retryable status, or retries exhausted)."""

    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status = status


class RobotsBlocked(FetchError):
    """robots.txt disallows the URL for us; it is never fetched."""

    def __init__(self, url: str) -> None:
        super().__init__(url, None, "disallowed by robots.txt")


class SourceAborted(Exception):
    """Too many consecutive failures; the source is abandoned for this run."""


class RateLimiter:
    """Hands out request slots at least `interval` seconds apart (per instance, i.e. per host)."""

    def __init__(self, interval: float, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next_slot = 0.0

    async def wait(self) -> None:
        now = self._clock()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self.interval
        if slot > now:
            await self._sleep(slot - now)

    def push_back(self, seconds: float) -> None:
        """Delay the next slot (used for Retry-After and Crawl-delay)."""
        self._next_slot = max(self._next_slot, self._clock() + seconds)


class RobotsRules:
    """robots.txt rules for one host.

    Parsing uses `urllib.robotparser`. Its matcher only does prefix matching, but real files (all three of ours) use the
    `*` and `$` wildcards, so matching is redone here with Google's rules: the longest matching pattern wins, and
    `Allow` wins a tie. A missing file (4xx other than 401/403) allows everything; 401/403 and an unreachable file
    disallow everything.
    """

    def __init__(self, parser: RobotFileParser, *, allow_all: bool = False, disallow_all: bool = False) -> None:
        self._parser = parser
        self._allow_all = allow_all
        self._disallow_all = disallow_all
        self._patterns: dict[str, re.Pattern[str]] = {}

    @classmethod
    def from_text(cls, text: str) -> RobotsRules:
        parser = RobotFileParser()
        # urllib.robotparser ends a group at every blank line, so "User-agent: *" followed by an empty line loses its
        # rules (two of our three sites write robots.txt that way). Google and every other crawler ignore blank lines.
        parser.parse([line for line in text.splitlines() if line.strip()])
        return cls(parser)

    @classmethod
    def from_status(cls, status: int | None) -> RobotsRules:
        if status is not None and 400 <= status < 500 and status not in (401, 403):
            return cls(RobotFileParser(), allow_all=True)
        return cls(RobotFileParser(), disallow_all=True)

    @property
    def crawl_delay(self) -> float | None:
        if self._allow_all or self._disallow_all:
            return None
        delay = self._parser.crawl_delay(ROBOTS_TOKEN)
        return float(delay) if delay is not None else None

    @property
    def sitemaps(self) -> list[str]:
        return list(self._parser.site_maps() or [])

    def allowed(self, url: str) -> bool:
        if self._disallow_all:
            return False
        if self._allow_all:
            return True
        parts = urlsplit(url)
        target = unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")
        best_len = -1
        allowed = True
        for rule in self._rules():
            rule_path = unquote(rule.path)
            if not rule_path or not self._pattern(rule_path).match(target):
                continue
            specificity = len(rule_path.replace("*", ""))
            if specificity > best_len or (specificity == best_len and rule.allowance):
                best_len, allowed = specificity, rule.allowance
        return allowed

    def _rules(self) -> list[Any]:
        for entry in self._parser.entries:  # type: ignore[attr-defined]
            if entry.applies_to(ROBOTS_TOKEN):
                return list(entry.rulelines)
        default = self._parser.default_entry  # type: ignore[attr-defined]
        return list(default.rulelines) if default is not None else []

    def _pattern(self, rule_path: str) -> re.Pattern[str]:
        cached = self._patterns.get(rule_path)
        if cached is None:
            anchored = rule_path.endswith("$")
            body = rule_path[:-1] if anchored else rule_path
            regex = ".*".join(re.escape(piece) for piece in body.split("*")) + ("$" if anchored else "")
            cached = self._patterns[rule_path] = re.compile(regex)
        return cached


@dataclass
class PoliteClient:
    """Rate-limited, robots-aware async HTTP client. One instance per source run."""

    user_agent: str
    requests_per_second: float = 1.0
    timeout: float = 30.0
    max_consecutive_failures: int = 20
    max_retries: int = 4
    backoff_base: float = 1.0
    respect_robots: bool = True
    transport: httpx.AsyncBaseTransport | None = None
    clock: Clock = time.monotonic
    sleep: Sleep = asyncio.sleep
    consecutive_failures: int = field(default=0, init=False)
    requests_made: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Language": "tr-TR,tr;q=0.9"},
            transport=self.transport,
        )
        self._limiters: dict[str, RateLimiter] = {}
        self._robots: dict[str, RobotsRules] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ public API

    async def get(self, url: str, *, accept: str | None = None) -> httpx.Response:
        if self.respect_robots and not await self.is_allowed(url):
            raise RobotsBlocked(url)
        return await self._request(url, accept=accept)

    async def get_text(self, url: str) -> str:
        return (await self.get(url)).text

    async def get_json(self, url: str) -> Any:
        response = await self.get(url, accept="application/json")
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(url, response.status_code, "response is not JSON") from exc

    async def is_allowed(self, url: str) -> bool:
        return (await self.robots_for(url)).allowed(url)

    async def robots_for(self, url: str) -> RobotsRules:
        origin = _origin(url)
        rules = self._robots.get(origin)
        if rules is not None:
            return rules
        async with self._robots_locks.setdefault(origin, asyncio.Lock()):
            rules = self._robots.get(origin)
            if rules is None:
                rules = await self._fetch_robots(origin)
                self._robots[origin] = rules
                if rules.crawl_delay:
                    self._limiter(origin).interval = max(self._limiter(origin).interval, rules.crawl_delay)
        return rules

    # ------------------------------------------------------------------ internals

    def _limiter(self, origin: str) -> RateLimiter:
        limiter = self._limiters.get(origin)
        if limiter is None:
            limiter = self._limiters[origin] = RateLimiter(
                1.0 / self.requests_per_second, clock=self.clock, sleep=self.sleep
            )
        return limiter

    async def _fetch_robots(self, origin: str) -> RobotsRules:
        url = f"{origin}/robots.txt"
        try:
            response = await self._request(url, retry=True)
        except FetchError as exc:
            log.warning("robots_unavailable", url=url, status=exc.status)
            return RobotsRules.from_status(exc.status)
        return RobotsRules.from_text(response.text)

    async def _request(self, url: str, *, accept: str | None = None, retry: bool = True) -> httpx.Response:
        limiter = self._limiter(_origin(url))
        headers = {"Accept": accept} if accept else None
        attempts = self.max_retries + 1 if retry else 1
        last_status: int | None = None
        last_error = "no attempt made"

        for attempt in range(attempts):
            await limiter.wait()
            self.requests_made += 1
            retry_after: float | None = None
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_status, last_error = None, f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if status < 400:
                    self.consecutive_failures = 0
                    return response
                last_status, last_error = status, f"HTTP {status}"
                if status in HEALTHY_STATUSES:
                    self.consecutive_failures = 0
                    raise FetchError(url, status, last_error)
                retry_after = _retry_after_seconds(response)
                if status not in RETRY_STATUSES:
                    self._note_failure(url, status)
                    raise FetchError(url, status, last_error)

            self._note_failure(url, last_status)
            if attempt + 1 < attempts:
                delay = retry_after if retry_after is not None else self._backoff(attempt)
                log.info("retrying", url=url, attempt=attempt + 1, status=last_status, delay=round(delay, 2))
                limiter.push_back(delay)

        raise FetchError(url, last_status, f"gave up after {attempts} attempts ({last_error})")

    def _backoff(self, attempt: int) -> float:
        return float(min(MAX_BACKOFF_SECONDS, self.backoff_base * 2**attempt) * (1 + random.random() * 0.1))

    def _note_failure(self, url: str, status: int | None) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            log.error("source_aborted", url=url, status=status, consecutive_failures=self.consecutive_failures)
            raise SourceAborted(f"{self.consecutive_failures} consecutive failures (last: {url}, status {status})")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(raw)))
    except ValueError:
        return None
