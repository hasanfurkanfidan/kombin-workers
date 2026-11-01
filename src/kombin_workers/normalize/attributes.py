"""Heuristics for fit / pattern / formality / seasons / material from a title and description.

Everything here is keyword matching on folded text (`fold`: lower-case, ASCII). The tables are the documentation; change
them together with `tests/test_attributes.py`. When nothing matches the result is the "unknown" value: `Fit.UNKNOWN`,
`None` for pattern / seasons / material (the backend then keeps its own defaults), and a category-based formality.

Formality is 1 (very casual) .. 5 (very formal). It starts from `CATEGORY_FORMALITY` and moves one step per side:
a formal keyword raises it, a casual keyword lowers it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from kombin_workers.models import Fit, Pattern, Season
from kombin_workers.normalize.text import clean_text, fold

# --------------------------------------------------------------------------------------------------- fit
# Checked in this order: the first group with a hit wins.
FIT_KEYWORDS: tuple[tuple[Fit, tuple[str, ...]], ...] = (
    (Fit.OVERSIZED, ("oversize", "oversized", "over size", "ekstra bol", "bol kesim", "bol kalip", "boxy")),
    (Fit.SLIM, ("slim", "skinny", "dar kalip", "dar kesim", "body fit", "super slim", "fitted", "vucuda oturan")),
    (Fit.RELAXED, ("relaxed", "rahat kalip", "rahat kesim", "loose", "baggy", "wide leg", "genis paca", "bol paca", "genis kalip", "comfort fit")),
    (Fit.REGULAR, ("regular", "normal kalip", "standart kalip", "klasik kalip", "straight", "duz paca", "klasik kesim")),
)  # fmt: skip

# --------------------------------------------------------------------------------------------------- pattern
PATTERN_KEYWORDS: tuple[tuple[Pattern, tuple[str, ...]], ...] = (
    (Pattern.STRIPED, ("cizgili", "striped", "stripe", "raye", "pin stripe", "ince cizgi")),
    (Pattern.CHECKED, ("ekose", "kareli", "plaid", "checked", "check$", "potikare", "gingham", "kaz ayagi", "houndstooth")),
    (Pattern.PRINTED, ("baskili", "desenli", "print", "grafik", "leopar", "cicek", "floral", "camo", "kamuflaj", "puantiyeli", "logo", "yazili", "isleme")),
    (Pattern.TEXTURED, ("dokulu", "kabartmali", "jakarli", "kapitone", "pliseli", "bukleli", "texture", "ribana", "balik agi", "orgu desen", "nubuk", "suet", "suede")),
    (Pattern.SOLID, ("duz", "solid", "plain", "basic", "tek renk")),
)  # fmt: skip

# --------------------------------------------------------------------------------------------------- formality
CATEGORY_FORMALITY: dict[str, int] = {
    "ust-giyim/tisort": 1,
    "ust-giyim/gomlek": 3,
    "ust-giyim/triko": 2,
    "ust-giyim/blazer": 4,
    "alt-giyim/pantolon": 3,
    "alt-giyim/jean": 2,
    "alt-giyim/etek": 3,
    "dis-giyim/mont": 2,
    "dis-giyim/kaban": 4,
    "dis-giyim/ceket": 3,
    "ayakkabi/sneaker": 2,
    "ayakkabi/bot": 3,
    "ayakkabi/loafer": 4,
    "aksesuar/canta": 3,
    "aksesuar/kemer": 3,
}
FORMAL_KEYWORDS = ("klasik", "smokin", "davet", "resmi", "damat", "formal", "ofis", "is yeri", "saten", "takim", "kumas pantolon", "rugan", "oxford", "derby")
CASUAL_KEYWORDS = ("spor", "sporty", "casual", "gunluk", "rahat", "jogger", "esofman", "basic", "sokak", "streetwear", "oversize", "kapusonlu", "outdoor", "trekking", "kosu")

# --------------------------------------------------------------------------------------------------- seasons
WINTER_KEYWORDS = ("kislik", "kis ", "kalin", "polar", "kurklu", "astarli", "sisme", "puffer", "yunlu", "termal", "kaban", "kar botu", "boguzlu", "fitilli triko", "winter", "kapitone")
SUMMER_KEYWORDS = ("yazlik", "yaz ", "keten", "ince kumas", "ince dokulu", "sandalet", "kisa kollu", "kolsuz", "nefes alan", "viskon", "summer", "kisa paca", "polo yaka")
SPRING_AUTUMN_KEYWORDS = ("ilkbahar", "sonbahar", "mevsimlik", "ara sezon", "bahar", "spring", "autumn", "trenckot")
CATEGORY_SEASONS: dict[str, tuple[Season, ...]] = {
    "dis-giyim/mont": (Season.AUTUMN, Season.WINTER),
    "dis-giyim/kaban": (Season.AUTUMN, Season.WINTER),
}

# --------------------------------------------------------------------------------------------------- material
MATERIAL_WORDS: tuple[tuple[str, str], ...] = (
    ("deri", "Deri"), ("suet", "Süet"), ("nubuk", "Nubuk"), ("pamuk", "Pamuk"), ("cotton", "Pamuk"), ("keten", "Keten"),
    ("yun", "Yün"), ("wool", "Yün"), ("polyester", "Polyester"), ("viskon", "Viskon"), ("denim", "Denim"),
    ("kadife", "Kadife"), ("saten", "Saten"), ("naylon", "Naylon"), ("akrilik", "Akrilik"), ("elastan", "Elastan"),
    ("kasmir", "Kaşmir"), ("ipek", "İpek"), ("hasir", "Hasır"), ("kanvas", "Kanvas"), ("tekstil", "Tekstil"),
)  # fmt: skip
_COMPOSITION = re.compile(r"%\s*(\d{1,3})\s*([A-Za-zÇĞİIÖŞÜçğıöşü]+(?: [A-Za-zÇĞİIÖŞÜçğıöşü]+)?)|(\d{1,3})\s*%\s*([A-Za-zÇĞİIÖŞÜçğıöşü]+)")


def _has(folded: str, keywords: tuple[str, ...]) -> bool:
    for keyword in keywords:
        if keyword.endswith("$"):
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword[:-1])}(?![a-z0-9])", folded):
                return True
        elif keyword.endswith(" "):
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword.strip())}(?![a-z0-9])", folded):
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(keyword)}", folded):
            return True
    return False


def infer_fit(*texts: str | None) -> Fit:
    folded = fold(" ".join(t for t in texts if t))
    for fit, keywords in FIT_KEYWORDS:
        if _has(folded, keywords):
            return fit
    return Fit.UNKNOWN


def infer_pattern(*texts: str | None) -> Pattern | None:
    folded = fold(" ".join(t for t in texts if t))
    for pattern, keywords in PATTERN_KEYWORDS:
        if _has(folded, keywords):
            return pattern
    return None


def infer_formality(category_slug: str, *texts: str | None) -> int:
    folded = fold(" ".join(t for t in texts if t))
    score = CATEGORY_FORMALITY.get(category_slug, 3)
    formal, casual = _has(folded, FORMAL_KEYWORDS), _has(folded, CASUAL_KEYWORDS)
    if formal and not casual:
        score += 1
    elif casual and not formal:
        score -= 1
    return max(1, min(5, score))


def infer_seasons(category_slug: str, *texts: str | None) -> list[Season] | None:
    folded = " " + fold(" ".join(t for t in texts if t)) + " "
    winter, summer = _has(folded, WINTER_KEYWORDS), _has(folded, SUMMER_KEYWORDS)
    between = _has(folded, SPRING_AUTUMN_KEYWORDS)
    if winter and not summer:
        return [Season.AUTUMN, Season.WINTER]
    if summer and not winter:
        return [Season.SPRING, Season.SUMMER]
    if between and not (winter or summer):
        return [Season.SPRING, Season.AUTUMN]
    if not winter and not summer:
        return list(CATEGORY_SEASONS[category_slug]) if category_slug in CATEGORY_SEASONS else None
    return None


def infer_material(*texts: str | None) -> str | None:
    """A composition ("%98 Pamuk, %2 Elastan") if one is written out, else the first material word found."""
    joined = " ".join(clean_text(t) for t in texts if t)
    parts: list[tuple[int, str]] = []
    for match in _COMPOSITION.finditer(joined):
        pct = int(match.group(1) or match.group(3))
        name = (match.group(2) or match.group(4)).strip()
        if 0 < pct <= 100 and not any(name.lower() == existing.lower() for _, existing in parts):
            parts.append((pct, name))
    if parts:
        parts.sort(key=lambda p: -p[0])
        return ", ".join(f"%{pct} {name.capitalize()}" for pct, name in parts[:4])[:200]
    folded = fold(joined)
    for keyword, label in MATERIAL_WORDS:
        if _has(folded, (keyword,)):
            return label
    return None


class InferredAttributes(NamedTuple):
    pattern: Pattern | None
    fit: Fit
    formality: int
    seasons: list[Season] | None
    material: str | None


def infer_attributes(
    category_slug: str,
    title: str | None,
    *extra_texts: str | None,
    material_texts: tuple[str | None, ...] | None = None,
) -> InferredAttributes:
    """Run every heuristic over the title plus any extra descriptive text (description, tags, composition).

    Free-text descriptions often mention other fabrics in styling advice ("pair it with denim trousers"), so a source
    that has a dedicated fabric field passes just that as `material_texts`; otherwise the material is read from all texts.
    """
    texts = (title, *extra_texts)
    return InferredAttributes(
        pattern=infer_pattern(*texts),
        fit=infer_fit(*texts),
        formality=infer_formality(category_slug, *texts),
        seasons=infer_seasons(category_slug, *texts),
        material=infer_material(*(material_texts if material_texts is not None else texts)),
    )
