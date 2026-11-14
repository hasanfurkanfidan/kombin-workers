"""All known sources, by CLI slug."""

from __future__ import annotations

from kombin_workers.http import PoliteClient
from kombin_workers.sources.base import BaseSource
from kombin_workers.sources.colins import ColinsSource
from kombin_workers.sources.derimod import DerimodSource
from kombin_workers.sources.hotic import HoticSource
from kombin_workers.sources.jackjones import JackJonesSource
from kombin_workers.sources.koton import KotonSource
from kombin_workers.sources.network import NetworkSource
from kombin_workers.sources.twist import TwistSource

SOURCE_CLASSES: dict[str, type[BaseSource]] = {
    source.slug: source
    for source in (ColinsSource, DerimodSource, HoticSource, JackJonesSource, KotonSource, NetworkSource, TwistSource)
}


class UnknownSourceError(KeyError):
    pass


def build_source(slug: str, http: PoliteClient, max_pages: int = 5) -> BaseSource:
    source_class = SOURCE_CLASSES.get(slug)
    if source_class is None:
        known = ", ".join(sorted(SOURCE_CLASSES))
        raise UnknownSourceError(f"unknown source {slug!r}; known sources: {known}")
    return source_class(http, max_pages)


def runnable_slugs() -> list[str]:
    """Sources `run-all` takes: every one that is not marked blocked."""
    return sorted(slug for slug, source in SOURCE_CLASSES.items() if not source.blocked)
