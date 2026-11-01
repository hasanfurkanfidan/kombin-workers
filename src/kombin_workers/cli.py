"""`kombin-workers` command line."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
import typer

from kombin_workers.backend import BackendClient, BackendError
from kombin_workers.http import PoliteClient
from kombin_workers.logging_setup import configure_logging
from kombin_workers.pipeline import RunSummary, run_source
from kombin_workers.scheduler import daemon_loop
from kombin_workers.settings import BOT_CONTACT_PLACEHOLDER, Settings, get_settings
from kombin_workers.sources.registry import SOURCE_CLASSES, UnknownSourceError, build_source, runnable_slugs

app = typer.Typer(help="Kombin ingestion workers.", no_args_is_help=True, add_completion=False)
log = structlog.get_logger(__name__)

LimitOption = Annotated[int | None, typer.Option("--limit", min=1, help="Stop after this many discovered products.")]
MaxPagesOption = Annotated[
    int | None, typer.Option("--max-pages", min=1, help="Listing pages per category (default: KOMBIN_MAX_PAGES, 5).")
]


def _bootstrap() -> Settings:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(json_logs=settings.log_json)
    if settings.bot_contact == BOT_CONTACT_PLACEHOLDER:
        log.warning("bot_contact_not_set", hint="set BOT_CONTACT before running against real sites")
    return settings


def _backend(settings: Settings) -> BackendClient:
    key = settings.internal_key.get_secret_value()
    if not key:
        typer.secho("KOMBIN_INTERNAL_KEY is not set (see .env.example).", err=True, fg=typer.colors.RED)
        raise typer.Exit(2)
    return BackendClient(settings.api_url, key, batch_size=settings.batch_size)


def _http(settings: Settings) -> PoliteClient:
    return PoliteClient(
        user_agent=settings.user_agent,
        requests_per_second=settings.requests_per_second,
        timeout=settings.http_timeout_seconds,
        max_consecutive_failures=settings.max_consecutive_failures,
    )


def _source_or_exit(slug: str, http: PoliteClient, max_pages: int):  # type: ignore[no-untyped-def]
    try:
        return build_source(slug, http, max_pages)
    except UnknownSourceError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(2) from exc


async def _run_one(
    settings: Settings, slug: str, *, backend: BackendClient | None, limit: int | None, max_pages: int, collect: bool
) -> RunSummary:
    async with _http(settings) as http:
        source = _source_or_exit(slug, http, max_pages)
        return await run_source(
            source, backend=backend, limit=limit, concurrency=settings.concurrency, collect=collect
        )


def _execute[T](coro: Awaitable[T]) -> T:
    try:
        return asyncio.run(coro)  # type: ignore[arg-type]
    except BackendError as exc:
        typer.secho(f"backend error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from exc


@app.command()
def sources() -> None:
    """List the known sources and whether they are blocked."""
    _bootstrap()
    for slug in sorted(SOURCE_CLASSES):
        cls = SOURCE_CLASSES[slug]
        state = "BLOCKED" if cls.blocked else "ok"
        typer.echo(f"{slug:<12} {state:<8} seller={slug}-store  {cls.brand_name}  - {cls.notes}")


@app.command()
def check(source: str, limit: LimitOption = 20, max_pages: MaxPagesOption = None) -> None:
    """Dry run: discover, fetch and normalize, print the items as JSON. Nothing is sent to the backend."""
    settings = _bootstrap()

    async def go() -> RunSummary:
        return await _run_one(settings, source, backend=None, limit=limit, max_pages=max_pages or settings.max_pages, collect=True)

    summary = _execute(go())
    typer.echo(json.dumps([item.to_wire() for item in summary.items], ensure_ascii=False, indent=2))
    typer.echo(summary.format(), err=True)


@app.command()
def run(source: str, limit: LimitOption = None, max_pages: MaxPagesOption = None) -> None:
    """Register the seller, run the full pipeline for one source and print a summary."""
    settings = _bootstrap()

    async def go() -> RunSummary:
        async with _backend(settings) as backend:
            return await _run_one(settings, source, backend=backend, limit=limit, max_pages=max_pages or settings.max_pages, collect=False)

    summary = _execute(go())
    typer.echo(summary.format())
    if summary.blocked or summary.aborted:
        raise typer.Exit(3)


@app.command("run-all")
def run_all(limit: LimitOption = None, max_pages: MaxPagesOption = None) -> None:
    """Run every non-blocked source, one after the other."""
    settings = _bootstrap()
    summaries = _execute(_run_all(settings, limit, max_pages or settings.max_pages))
    if any(s.aborted for s in summaries):
        raise typer.Exit(3)


async def _run_all(settings: Settings, limit: int | None, max_pages: int) -> list[RunSummary]:
    summaries: list[RunSummary] = []
    async with _backend(settings) as backend:
        for slug in runnable_slugs():
            try:
                summary = await _run_one(settings, slug, backend=backend, limit=limit, max_pages=max_pages, collect=False)
            except Exception:  # one broken source must not stop the others
                log.exception("source_failed", source=slug)
                continue
            typer.echo(summary.format())
            typer.echo("")
            summaries.append(summary)
    return summaries


@app.command()
def sweep(seller: Annotated[str | None, typer.Option("--seller", help="Seller slug; omit to sweep every seller.")] = None) -> None:
    """Deactivate listings the workers have not seen for 48 hours."""
    settings = _bootstrap()

    async def go() -> None:
        async with _backend(settings) as backend:
            result = await backend.sweep(seller)
            typer.echo(json.dumps(result.model_dump(by_alias=False), indent=2))

    _execute(go())


@app.command()
def daemon() -> None:
    """Run `run-all` every 6 hours and `sweep` once a day, with jitter. Logs are JSON when KOMBIN_LOG_JSON=1."""
    settings = _bootstrap()

    async def job_run_all() -> None:
        await _run_all(settings, None, settings.max_pages)

    async def job_sweep() -> None:
        async with _backend(settings) as backend:
            result = await backend.sweep(None)
            log.info("sweep_finished", **result.model_dump())

    hours: Callable[[float], float] = lambda h: h * 3600  # noqa: E731
    try:
        asyncio.run(
            daemon_loop(
                run_all=job_run_all,
                sweep=job_sweep,
                run_every=hours(settings.run_interval_hours),
                sweep_every=hours(settings.sweep_interval_hours),
                jitter=settings.jitter_seconds,
            )
        )
    except KeyboardInterrupt:
        log.info("daemon_stopped")


if __name__ == "__main__":  # pragma: no cover
    app()
