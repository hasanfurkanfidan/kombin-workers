"""Minimal sitemap reader: `<sitemapindex>` -> child sitemap URLs, `<urlset>` -> page URLs.

Sitemaps are machine-written XML; `<loc>` elements are found with a regular expression (no XML parser, so nothing
here can be fed an entity bomb). A sitemap index and a url set are told apart by their root element.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.DOTALL | re.IGNORECASE)
_INDEX = re.compile(r"<sitemapindex[\s>]", re.IGNORECASE)


@dataclass(frozen=True)
class Sitemap:
    urls: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


def parse_sitemap(xml: str) -> Sitemap:
    """The page URLs of a `<urlset>` or the child sitemap URLs of a `<sitemapindex>` (in document order, unique)."""
    locs = list(dict.fromkeys(html.unescape(m.group(1)).strip() for m in _LOC.finditer(xml)))
    locs = [loc for loc in locs if loc.startswith(("http://", "https://"))]
    if _INDEX.search(xml):
        return Sitemap(children=locs)
    return Sitemap(urls=locs)
