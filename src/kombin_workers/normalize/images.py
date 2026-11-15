"""Product galleries: dedupe, order packshots first, cap.

The try-on feature needs photos that show the garment **without a person** (flat / packshot, ghost mannequin, hanger).
A source collects every gallery image of a product, in the site's own order, and passes the URLs to `order_gallery`.
We never download an image to classify it (the vision service does that later); the only evidence used is what the
URL itself and the site's data say:

1. A hint in the file name / path (`packshot`, `flat`, `ghost`, `hanger`, `stillife`, `cutout` ...) or one of
   the per-host rules in `HOST_RULES` (verified per brand, see docs/sources.md) marks an image *likely person-free*.
   (`detail` / `detay` is deliberately *not* a hint: detail shots are often close-ups on a model.)
2. A hint of the opposite kind (`model`, `lookbook`, `lifestyle`, ...) marks it *likely worn*.
3. Everything else is *unknown* and keeps the site's order.

The order is: likely person-free, then unknown, then likely worn - each group in the site's original order (stable).
A site that gives no hint therefore keeps its own order. The result is capped at `GALLERY_CAP` (8) images.

The backend accepts at most 6 `imageUrls` per upsert item (`UpsertOffers/Validator.cs`), so `NormalizedOffer` keeps the
first 6 of that order: ordering happens *before* the cut, so a packshot that sits 7th on the site still goes out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

GALLERY_CAP = 8

_PERSON_FREE_WORDS = re.compile(
    r"(?<![a-z])(packshot|pack-shot|flat-?lay|flat|ghost|hanger|still-?life|stillife|cut-?out)(?![a-z])"
)
_WORN_WORDS = re.compile(r"(?<![a-z])(on-?model|model|manken|lookbook|lifestyle|editorial|campaign)(?![a-z])")


def _twist_folder(path: str) -> bool | None:
    """Twist stores a product's pictures in `/Twist/products/<season>/<folder>/`; `Stillife` holds the still-life shots."""
    return True if "/stillife/" in path else None


def _png_cutout(path: str) -> bool | None:
    """Jack & Jones serves its transparent-background cut-outs as `.png`; the on-figure shots are `.jpg`."""
    return True if path.endswith(".png") else None


# host -> rule(lower-cased URL path) -> True (likely person-free) / False (likely worn) / None (no opinion)
HOST_RULES = {
    "cdn.twist.com.tr": _twist_folder,
    "images.jackjones.com.tr": _png_cutout,
}


def person_free_hint(url: str) -> bool | None:
    """True = likely person-free, False = likely worn, None = no hint. Looks at the URL only (never at the image)."""
    parts = urlsplit(url)
    path = parts.path.lower()
    rule = HOST_RULES.get((parts.hostname or "").lower())
    if rule is not None:
        verdict = rule(path)
        if verdict is not None:
            return verdict
    if _PERSON_FREE_WORDS.search(path):
        return True
    if _WORN_WORDS.search(path):
        return False
    return None


def order_gallery(urls: Iterable[str], *, cap: int = GALLERY_CAP) -> list[str]:
    """Unique URLs, likely person-free first, then unknown, then likely worn (stable), at most `cap`."""
    unique = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))
    rank = {True: 0, None: 1, False: 2}
    ordered = sorted(unique, key=lambda u: rank[person_free_hint(u)])  # sorted() is stable
    return ordered[:cap]


def first_is_person_free(urls: list[str]) -> bool:
    """Whether the first image of a gallery is likely person-free (used for the run statistics)."""
    return bool(urls) and person_free_hint(urls[0]) is True
