"""Size strings -> the compact forms the backend stores (max 20 characters each).

`normalize_sizes` also puts a list in reading order: XS S M L XL XXL, then 38 39 40 41.5 ..., then anything else.

- Letter sizes are upper-case: XXS XS S M L XL XXL XXXL ("xl" -> "XL", "2XL" -> "XXL", "Medium" -> "M").
- A double size splits into its two sizes: "S/M" -> ["S", "M"], "M-L" -> ["M", "L"].
- Numeric sizes keep one decimal at most, with a dot: "42,5" -> "42.5", "42 1/2" -> "42.5", "42.0" -> "42".
- Trouser sizes stay as one size: "32/34", "W32 L34" -> "32/34". Ranges stay too: "36-38".
- One-size labels ("Standart", "Tek Ebat", "One Size") become "STD".
- Placeholders ("Beden Seçiniz", "-") produce nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from kombin_workers.normalize.text import clean_text, fold

LETTER_SIZES = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL")
ONE_SIZE = "STD"

_WORDS = {
    "xxsmall": "XXS", "xsmall": "XS", "small": "S", "medium": "M", "large": "L", "xlarge": "XL", "xxlarge": "XXL",
    "2xl": "XXL", "3xl": "XXXL", "2xs": "XXS", "xxxl": "XXXL", "xxxs": "XXXS",
}  # fmt: skip
_ONE_SIZE_WORDS = frozenset({"std", "standart", "standard", "tek ebat", "tekebat", "one size", "onesize", "os", "u", "tek beden"})
_PLACEHOLDERS = ("beden sec", "secin", "seciniz", "size select", "select size")
_HALF = {"½": ".5", "1/2": ".5"}

_LETTER = re.compile(r"^(?:XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|\d?XL|\d?XS)$")
_NUMBER = re.compile(r"^\d{1,3}(?:[.,]\d)?$")
_TROUSER = re.compile(r"^W?(\d{2})\s*[/xX\s]\s*L?(\d{2})$")
_RANGE = re.compile(r"^(\d{1,3})\s*-\s*(\d{1,3})$")
_AGE = re.compile(r"^(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*(YAS|AY|Y|M)$")
_SPLIT_DOUBLE = re.compile(r"\s*[/-]\s*")


def _number(raw: str) -> str:
    value = raw.replace(",", ".")
    if "." in value:
        whole, frac = value.split(".", 1)
        return whole if set(frac) <= {"0"} else f"{whole}.{frac}"
    return value


def _letter(token: str) -> str:
    return _WORDS.get(token.lower(), token).upper()


def normalize_size(raw: str | None) -> list[str]:
    """Zero, one or two normalized sizes for one source size label."""
    text = clean_text(raw)
    if not text:
        return []
    folded = fold(text)
    if any(marker in folded for marker in _PLACEHOLDERS) or folded in {"-", "--", "yok", "n/a"}:
        return []
    if folded in _ONE_SIZE_WORDS:
        return [ONE_SIZE]

    text = re.sub(r"(?i)\b(beden|size|numara|no)\b[:.]?", "", text).strip()
    for half, replacement in _HALF.items():
        text = re.sub(rf"(?<=\d)\s*{re.escape(half)}", replacement, text)
    text = text.strip()
    upper = text.upper()
    compact = re.sub(r"\s+", "", upper)

    word = _WORDS.get(re.sub(r"[^a-z0-9]", "", compact.lower()))
    if word:
        return [word]
    if _LETTER.match(compact):
        return [_letter(compact)]

    trouser = _TROUSER.match(compact) or _TROUSER.match(upper)
    if trouser:
        return [f"{trouser.group(1)}/{trouser.group(2)}"]

    if _NUMBER.match(compact):
        return [_number(compact)]

    age = _AGE.match(fold(compact).upper())
    if age:
        unit = "Y" if age.group(3) in {"YAS", "Y"} else "M"
        span = f"{age.group(1)}-{age.group(2)}" if age.group(2) else age.group(1)
        return [f"{span}{unit}"]

    range_match = _RANGE.match(compact)
    if range_match and not _LETTER.match(range_match.group(1)):
        return [f"{range_match.group(1)}-{range_match.group(2)}"]

    parts = [part for part in _SPLIT_DOUBLE.split(compact) if part]
    if len(parts) == 2 and all(_LETTER.match(part) or part.lower() in _WORDS for part in parts):
        return [_letter(part) for part in parts]

    return [compact[:20]] if compact.isascii() and len(compact) <= 20 else []


def _sort_key(size: str) -> tuple[int, float, str]:
    if size in LETTER_SIZES:
        return (0, float(LETTER_SIZES.index(size)), size)
    try:
        return (1, float(size), size)
    except ValueError:
        return (2, 0.0, size)


def normalize_sizes(raw_sizes: Iterable[str | None]) -> list[str]:
    """Normalize many size labels: duplicates dropped, letter sizes first (XS..XXL), then numbers ascending, then the rest."""
    seen: dict[str, None] = {}
    for raw in raw_sizes:
        for size in normalize_size(raw):
            seen.setdefault(size)
    return sorted(seen, key=_sort_key)
