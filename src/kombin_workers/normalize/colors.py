"""Turkish (and the odd English) colour words -> `PrimaryColor`.

How a colour name is read:

1. The text is folded (`fold`): lower-case, ASCII, so "Kırmızı" == "kirmizi".
2. Multi-word names ("cok renkli", "buz mavisi") are looked up as phrases first, longest first.
3. Otherwise the text is split into words. Shade modifiers ("koyu", "acik", "melanj", ...) are ignored, with one
   exception: "koyu" before a blue word means Navy ("koyu mavi" == dark blue).
4. The first word that names a colour wins ("siyah beyaz" -> Black). A word that names several colours at once
   ("renkli", "multi") wins over everything.
5. A word matches a table entry exactly or with a Turkish suffix ("mavisi", "yesili", "grimsi").

Anything unknown is `PrimaryColor.OTHER`.
"""

from __future__ import annotations

import re

from kombin_workers.models import PrimaryColor as C
from kombin_workers.normalize.text import fold

# Whole phrases, checked before single words (keys are folded).
PHRASES: dict[str, C] = {
    "cok renkli": C.MULTI,
    "cok renk": C.MULTI,
    "multi color": C.MULTI,
    "multi colour": C.MULTI,
    "multi renk": C.MULTI,
    "buz mavisi": C.BLUE,
    "bebe mavisi": C.BLUE,
    "petrol mavisi": C.BLUE,
    "saks mavisi": C.BLUE,
    "gok mavisi": C.BLUE,
    "kot mavisi": C.BLUE,
    "indigo mavisi": C.BLUE,
    "royal blue": C.BLUE,
    "light blue": C.BLUE,
    "dark blue": C.NAVY,
    "navy blue": C.NAVY,
    "gul kurusu": C.PINK,
    "yag yesili": C.GREEN,
    "nane yesili": C.GREEN,
    "zeytin yesili": C.GREEN,
    "off white": C.WHITE,
    "kirik beyaz": C.WHITE,
    "kum beji": C.BEIGE,
    "toprak rengi": C.BROWN,
}

# Single words (keys are folded stems; suffixes are handled by `_SUFFIXES`).
WORDS: dict[str, C] = {
    # Black
    "siyah": C.BLACK, "kara": C.BLACK, "black": C.BLACK, "jet": C.BLACK,
    # White
    "beyaz": C.WHITE, "white": C.WHITE, "optik": C.WHITE, "kar": C.WHITE,
    # Grey (antrasit/fume/gumus are greys)
    "gri": C.GREY, "grey": C.GREY, "gray": C.GREY, "antrasit": C.GREY, "fume": C.GREY, "gumus": C.GREY,
    "silver": C.GREY, "charcoal": C.GREY, "duman": C.GREY,
    # Navy
    "lacivert": C.NAVY, "navy": C.NAVY, "marine": C.NAVY, "lacivertli": C.NAVY,
    # Blue
    "mavi": C.BLUE, "blue": C.BLUE, "indigo": C.BLUE, "turkuaz": C.BLUE, "petrol": C.BLUE, "saks": C.BLUE,
    "denim": C.BLUE, "turquoise": C.BLUE, "buz": C.BLUE,
    # Green (haki/zeytin/nane/fistik are greens)
    "yesil": C.GREEN, "green": C.GREEN, "haki": C.GREEN, "khaki": C.GREEN, "zumrut": C.GREEN, "zeytin": C.GREEN,
    "nane": C.GREEN, "mint": C.GREEN, "fistik": C.GREEN, "olive": C.GREEN, "cimen": C.GREEN,
    # Beige (ekru/krem/kum/vizon/taş are beiges)
    "bej": C.BEIGE, "beige": C.BEIGE, "ekru": C.BEIGE, "krem": C.BEIGE, "cream": C.BEIGE, "kum": C.BEIGE,
    "fildisi": C.BEIGE, "ivory": C.BEIGE, "vizon": C.BEIGE, "nude": C.BEIGE, "camel": C.BEIGE, "tas": C.BEIGE,
    "biskuvi": C.BEIGE, "sand": C.BEIGE, "stone": C.BEIGE, "naturel": C.BEIGE, "natural": C.BEIGE,
    # Brown
    "kahverengi": C.BROWN, "kahve": C.BROWN, "brown": C.BROWN, "taba": C.BROWN, "cikolata": C.BROWN,
    "kestane": C.BROWN, "tarcin": C.BROWN, "chocolate": C.BROWN, "tan": C.BROWN, "bronz": C.BROWN, "toprak": C.BROWN,
    # Red (bordo/visne are reds)
    "kirmizi": C.RED, "red": C.RED, "bordo": C.RED, "burgundy": C.RED, "visne": C.RED, "bordeaux": C.RED,
    "nar": C.RED, "ates": C.RED,
    # Pink
    "pembe": C.PINK, "pink": C.PINK, "pudra": C.PINK, "fusya": C.PINK, "fuchsia": C.PINK, "gul": C.PINK,
    "powder": C.PINK, "roze": C.PINK, "rose": C.PINK,
    # Purple
    "mor": C.PURPLE, "purple": C.PURPLE, "lila": C.PURPLE, "eflatun": C.PURPLE, "murdum": C.PURPLE,
    "lavanta": C.PURPLE, "lavender": C.PURPLE, "violet": C.PURPLE, "murdumu": C.PURPLE,
    # Yellow
    "sari": C.YELLOW, "yellow": C.YELLOW, "hardal": C.YELLOW, "limon": C.YELLOW, "altin": C.YELLOW,
    "gold": C.YELLOW, "mustard": C.YELLOW,
    # Orange (mercan/kiremit/somon/seftali are oranges)
    "turuncu": C.ORANGE, "orange": C.ORANGE, "mercan": C.ORANGE, "kiremit": C.ORANGE, "somon": C.ORANGE,
    "seftali": C.ORANGE, "coral": C.ORANGE, "salmon": C.ORANGE, "bakir": C.ORANGE,
    # Several colours at once
    "renkli": C.MULTI, "multi": C.MULTI, "multicolor": C.MULTI, "multicolour": C.MULTI, "karisik": C.MULTI,
    "rengarenk": C.MULTI, "colorful": C.MULTI,
}  # fmt: skip

# Shade/finish words that say nothing about the hue. ("koyu" is handled separately.)
MODIFIERS = frozenset(
    {
        "acik", "canli", "soluk", "pastel", "parlak", "mat", "melanj", "melange", "light", "dark", "koyu",
        "orta", "yarim", "ton", "tonu", "renk", "rengi", "color", "colour", "metalik", "neon", "washed", "yikama",
        "ve", "and", "ile", "kirik",
    }
)  # fmt: skip

_SUFFIXES = ("", "si", "msi", "imsi", "i", "li", "lu", "ler", "lar", "leri", "ligi")
_SPLIT = re.compile(r"[^a-z0-9]+")
_BLUES = frozenset({C.BLUE})


def _lookup(token: str) -> C | None:
    direct = WORDS.get(token)
    if direct is not None:
        return direct
    for stem, color in WORDS.items():
        if len(stem) >= 3 and token.startswith(stem) and token[len(stem) :] in _SUFFIXES:
            return color
    return None


def try_map_color(text: str | None) -> C | None:
    """The `PrimaryColor` a colour name denotes, or None if no colour word is recognised."""
    folded = fold(text)
    if not folded:
        return None

    for phrase in sorted(PHRASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            return PHRASES[phrase]

    tokens = [token for token in _SPLIT.split(folded) if token]
    found: list[C] = []
    dark = False
    for token in tokens:
        if token == "koyu":
            dark = True
            continue
        if token in MODIFIERS:
            continue
        color = _lookup(token)
        if color is None:
            continue
        if color is C.MULTI:
            return C.MULTI
        found.append(color)
        if len(found) == 1 and dark and color in _BLUES:
            return C.NAVY
    return found[0] if found else None


def map_color(text: str | None) -> C:
    """Like `try_map_color`, but unknown names become `PrimaryColor.OTHER`."""
    return try_map_color(text) or C.OTHER
