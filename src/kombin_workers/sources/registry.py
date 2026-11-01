"""All known sources, by CLI slug."""

from __future__ import annotations

from kombin_workers.http import PoliteClient
from kombin_workers.sources.base import BaseSource
from kombin_workers.sources.derimod import DerimodSource
from kombin_workers.sources.jackjones import JackJonesSource
from kombin_workers.sources.koton import KotonSource

SOURCE_CLASSES: dict[str, type[BaseSource]] = {
    source.slug: source for source in (DerimodSource, JackJonesSource, KotonSource)
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
