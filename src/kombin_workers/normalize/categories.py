"""Source category / breadcrumb / title -> one of our category slugs (or None, which means: skip the item).

Our taxonomy is the backend's (`GET /api/v1/catalog/categories`):

    ust-giyim/{tisort,gomlek,triko,blazer}   alt-giyim/{pantolon,jean,etek}   dis-giyim/{mont,kaban,ceket}
    ayakkabi/{sneaker,bot,loafer}            aksesuar/{canta,kemer}

Two layers decide, in this order:

1. `SOURCE_OVERRIDES[source][label]`: an exact (folded) source label mapped to a slug, or to `None` to force a skip.
   Use it when a source's own wording is ambiguous or misleading.
2. `RULES`: ordered keyword rules over the (folded) labels. The first rule that matches wins; labels are tried in the
   order the caller passes them, so put the most specific one (a category field) before the vaguer ones (the title).

A keyword matches at the start of a word ("tisort" matches "tisortler"); a keyword ending in `$` must be a whole word
(plus an optional plural: "kemer$" matches "kemer"/"kemerler" but not "kemerli", which is an adjective on a coat).
Words in `SKIP_WORDS` mean the item belongs to a category we do not have (dress, shorts, sweatshirt, sandals, ...);
they are checked first so that "gomlek elbise" is not filed as a shirt.

Judgement calls (documented because they stretch the taxonomy):
- `ayakkabi/loafer` is the bucket for every non-sport, non-boot shoe (loafer, moccasin, oxford, derby, dress shoes).
- Sweatshirts have no slug, so they are skipped rather than forced into `triko`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from kombin_workers.normalize.text import fold

CATEGORY_SLUGS = frozenset(
    {
        "ust-giyim/tisort",
        "ust-giyim/gomlek",
        "ust-giyim/triko",
        "ust-giyim/blazer",
        "alt-giyim/pantolon",
        "alt-giyim/jean",
        "alt-giyim/etek",
        "dis-giyim/mont",
        "dis-giyim/kaban",
        "dis-giyim/ceket",
        "ayakkabi/sneaker",
        "ayakkabi/bot",
        "ayakkabi/loafer",
        "aksesuar/canta",
        "aksesuar/kemer",
    }
)

SKIP_WORDS: tuple[str, ...] = (
    "elbise", "bluz", "sort$", "bermuda", "sweatshirt", "sweat shirt", "hoodie", "kapusonlu sweat", "sandalet",
    "terlik", "corap", "takim elbise", "pijama", "mayo", "bikini", "boxer", "ic camasir", "atki", "sapka", "bere",
    "eldiven", "cuzdan", "kartlik", "gozluk", "sal$", "bandana", "kravat", "papyon", "tulum", "body", "atlet",
    "tayt", "esofman takim", "yelek", "babet", "topuklu", "stiletto", "espadril", "parfum", "valiz", "bavul",
    "bileklik", "kolye", "kupe", "yuzuk", "saat", "ayakkabi bakim", "bakim urun", "cuzdan",
)  # fmt: skip

# (keywords, slug) - first match wins, so order is specificity. Keywords are folded.
RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("blazer",), "ust-giyim/blazer"),
    (("jean ceket", "kot ceket", "denim ceket"), "dis-giyim/ceket"),
    (("jean", "kot pantolon", "denim pantolon", "kot$"), "alt-giyim/jean"),
    (("mont$", "montlar", "sisme", "puffer", "parka", "anorak", "kaban mont"), "dis-giyim/mont"),
    (("kaban", "palto", "manto", "trenckot", "trench", "pardesu", "pardosu", "overcoat"), "dis-giyim/kaban"),
    (("ceket", "bomber", "biker", "harrington", "jacket"), "dis-giyim/ceket"),
    (("kazak", "triko", "hirka", "pullover", "sweater", "cardigan", "boguzlu", "yaka kazak"), "ust-giyim/triko"),
    (("tisort", "t-shirt", "tshirt", "t shirt", "polo$", "polo yaka", "tee$"), "ust-giyim/tisort"),
    (("gomlek", "shirt$"), "ust-giyim/gomlek"),
    (("pantolon", "jogger", "chino", "esofman alti", "kargo", "pantalon", "trouser", "keten pantolon"), "alt-giyim/pantolon"),
    (("etek", "skirt"), "alt-giyim/etek"),
    (("sneaker", "spor ayakkabi", "kosu ayakkabi", "basketbol ayakkabi", "trainer"), "ayakkabi/sneaker"),
    (("bot$", "botlar", "cizme", "boot", "chelsea", "postal"), "ayakkabi/bot"),
    (
        ("loafer", "mokasen", "makosen", "oxford", "derby", "klasik ayakkabi", "casual ayakkabi", "gunluk ayakkabi",
         "dress shoe", "monk"),
        "ayakkabi/loafer",
    ),
    (("canta", "bag$", "backpack", "postaci", "clutch"), "aksesuar/canta"),
    (("kemer$", "belt$"), "aksesuar/kemer"),
)  # fmt: skip

# Per-source exact-label overrides (folded labels). `None` forces a skip. Add an entry only when a source's own wording
# defeats the rules above, and cover it in tests/test_categories.py.
SOURCE_OVERRIDES: dict[str, dict[str, str | None]] = {
    "koton": {
        "kot pantolon": "alt-giyim/jean",
        "jean pantolon": "alt-giyim/jean",
    },
    "derimod": {},
    "jack-jones": {
        "jeans": "alt-giyim/jean",
        "t-shirts & tops": "ust-giyim/tisort",
        "sweats": None,
        "knitwear": "ust-giyim/triko",
    },
}

_compiled: dict[str, re.Pattern[str]] = {}


def _pattern(keyword: str) -> re.Pattern[str]:
    cached = _compiled.get(keyword)
    if cached is None:
        if keyword.endswith("$"):
            body = re.escape(keyword[:-1]) + r"(?:ler|lar)?(?![a-z0-9])"
        else:
            body = re.escape(keyword)
        cached = _compiled[keyword] = re.compile(rf"(?<![a-z0-9]){body}")
    return cached


def _matches(folded: str, keywords: Iterable[str]) -> bool:
    return any(_pattern(keyword).search(folded) for keyword in keywords)


def map_category(*labels: str | None, source: str | None = None) -> str | None:
    """Our category slug for the given source labels (most specific first), or None if there is none."""
    folded_labels = [folded for label in labels if (folded := fold(label))]
    if not folded_labels:
        return None

    overrides = SOURCE_OVERRIDES.get(source or "", {})
    for folded in folded_labels:
        if folded in overrides:
            return overrides[folded]

    for folded in folded_labels:
        if _matches(folded, SKIP_WORDS):
            return None
        for keywords, slug in RULES:
            if _matches(folded, keywords):
                return slug
    return None
