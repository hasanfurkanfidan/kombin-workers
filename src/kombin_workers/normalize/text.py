"""Text helpers shared by every normalizer: whitespace/entity cleanup, Turkish folding, model-code extraction."""

from __future__ import annotations

import html
import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser

_WS = re.compile(r"\s+")
_TR_FOLD = str.maketrans(
    {"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"}
)
_BLOCK_TAGS = ("br", "p", "li", "div", "tr", "h1", "h2", "h3", "h4", "ul")

# A model code is a run of letters/digits that mixes both and is at least five characters long: WM790004DN, 5SAK60396UW,
# 12298420, 00202A3997. Plain words ("Sneaker") and short tokens ("S", "42") are not codes.
_MODEL_CODE = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{3,}[A-Za-z0-9](?![A-Za-z0-9])|(?<!\d)\d{7,}(?!\d)")


def clean_text(value: str | None) -> str:
    """Decode HTML entities, turn NBSP/zero-width characters into plain whitespace, collapse runs of it."""
    if not value:
        return ""
    text = html.unescape(value)
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    return _WS.sub(" ", text).strip()


def html_to_text(markup: str | None) -> str:
    """Visible text of an HTML fragment, one line per block element, entities decoded."""
    if not markup:
        return ""
    tree = LexborHTMLParser(f"<div>{markup}</div>")
    for node in tree.css("script,style"):
        node.decompose()
    for tag in _BLOCK_TAGS:
        for node in tree.css(tag):
            node.insert_after("\n")
    root = tree.css_first("div")
    raw = root.text(deep=True, separator="") if root is not None else ""
    lines = (clean_text(line) for line in raw.splitlines())
    return "\n".join(line for line in lines if line)


def fold(value: str | None) -> str:
    """Lower-case, Turkish-aware, accent-free ASCII-ish form used for every keyword match."""
    if not value:
        return ""
    text = clean_text(value).translate(_TR_FOLD).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def strip_brand_prefix(title: str, brand: str) -> str:
    """Remove a leading brand name (and the separator after it): 'KOTON Kravat' -> 'Kravat'."""
    cleaned = clean_text(title)
    pattern = re.compile(rf"^\s*{re.escape(brand)}\b[\s:\-–—|]*", re.IGNORECASE)
    stripped = pattern.sub("", cleaned, count=1).strip()
    return stripped or cleaned


def title_case_tr(value: str) -> str:
    """Capitalize each word the Turkish way ('ŞORT' -> 'Şort', 'IŞIK' -> 'Işık')."""
    words = []
    for word in clean_text(value).split(" "):
        lowered = word.replace("I", "ı").replace("İ", "i").lower()
        if not lowered:
            continue
        first = "İ" if lowered[0] == "i" else lowered[0].upper()
        words.append(first + lowered[1:])
    return " ".join(words)


def extract_model_code(text: str | None) -> str | None:
    """First model-code-looking token in `text`, upper-cased, or None."""
    if not text:
        return None
    match = _MODEL_CODE.search(clean_text(text))
    return match.group(0).upper() if match else None


def find_model_code(candidates: list[str]) -> str | None:
    """First candidate (a tag, an SKU, ...) that looks like a model code."""
    for candidate in candidates:
        code = extract_model_code(candidate)
        if code and code == candidate.strip().upper():
            return code
    return None
