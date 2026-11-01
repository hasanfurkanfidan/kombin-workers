"""A site's gender / section wording -> `Audience`.

Words are matched as whole tokens of the folded text, so "men" never matches inside "women". Kids win over everything
("erkek cocuk" is Kids, not Men); "unisex" wins over the adult genders; if both a women's and a men's word appear the
answer is unknown (None) rather than a guess.
"""

from __future__ import annotations

import re

from kombin_workers.models import Audience
from kombin_workers.normalize.text import fold

KIDS_WORDS = frozenset({"cocuk", "cocuklar", "kiz", "bebek", "baby", "kids", "kid", "junior", "boys", "girls", "genc", "child"})
UNISEX_WORDS = frozenset({"unisex", "uniseks"})
WOMEN_WORDS = frozenset({"kadin", "kadinlar", "bayan", "women", "woman", "womens", "female", "ladies", "lady", "girl"})
MEN_WORDS = frozenset({"erkek", "erkekler", "bay", "men", "man", "mens", "male", "gentleman"})

_TOKEN = re.compile(r"[a-z0-9]+")


def map_audience(*texts: str | None) -> Audience | None:
    """The audience the given labels (a gender field, a section, tags, a title) point to, or None."""
    tokens: set[str] = set()
    for text in texts:
        tokens.update(_TOKEN.findall(fold(text)))
    if tokens & KIDS_WORDS:
        return Audience.KIDS
    if tokens & UNISEX_WORDS:
        return Audience.UNISEX
    women, men = bool(tokens & WOMEN_WORDS), bool(tokens & MEN_WORDS)
    if women and men:
        return None
    if women:
        return Audience.WOMEN
    if men:
        return Audience.MEN
    return None
