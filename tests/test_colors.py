import pytest

from kombin_workers.models import PrimaryColor as C
from kombin_workers.normalize.colors import map_color, try_map_color


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Siyah", C.BLACK),
        ("SİYAH", C.BLACK),
        ("Beyaz", C.WHITE),
        ("Kırık Beyaz", C.WHITE),
        ("Gri", C.GREY),
        ("Gri Melanj", C.GREY),
        ("Antrasit", C.GREY),
        ("Füme", C.GREY),
        ("Lacivert", C.NAVY),
        ("Koyu Lacivert", C.NAVY),
        ("Navy Blazer", C.NAVY),
        ("Koyu Mavi", C.NAVY),
        ("Mavi", C.BLUE),
        ("Açık Mavi", C.BLUE),
        ("Buz Mavisi", C.BLUE),
        ("Indigo Mavisi", C.BLUE),
        ("Turkuaz", C.BLUE),
        ("Yeşil", C.GREEN),
        ("Haki", C.GREEN),
        ("Koyu Haki", C.GREEN),
        ("Zeytin Yeşili", C.GREEN),
        ("Bej", C.BEIGE),
        ("Ekru", C.BEIGE),
        ("Krem", C.BEIGE),
        ("Vizon", C.BEIGE),
        ("Kahverengi", C.BROWN),
        ("Kahve", C.BROWN),
        ("Taba", C.BROWN),
        ("Kırmızı", C.RED),
        ("Bordo", C.RED),
        ("Pembe", C.PINK),
        ("Pudra", C.PINK),
        ("Gül Kurusu", C.PINK),
        ("Mor", C.PURPLE),
        ("Lila", C.PURPLE),
        ("Sarı", C.YELLOW),
        ("Hardal", C.YELLOW),
        ("Turuncu", C.ORANGE),
        ("Mercan", C.ORANGE),
        ("Çok Renkli", C.MULTI),
        ("Renkli", C.MULTI),
        ("Siyah/Beyaz", C.BLACK),
        ("black", C.BLACK),
        ("Navy", C.NAVY),
    ],
)
def test_turkish_color_words(text: str, expected: C) -> None:
    assert map_color(text) is expected


def test_modifiers_alone_are_not_a_colour() -> None:
    assert try_map_color("Koyu") is None
    assert try_map_color("Açık Ton") is None


def test_unknown_and_empty_become_other() -> None:
    assert map_color("Hologram") is C.OTHER
    assert map_color("") is C.OTHER
    assert map_color(None) is C.OTHER


def test_multi_wins_over_a_single_colour_word() -> None:
    assert map_color("Siyah Çok Renkli") is C.MULTI


def test_a_colour_inside_a_longer_string_is_found() -> None:
    assert map_color("Erkek Kahverengi Bağcıklı Sneaker") is C.BROWN


def test_words_that_merely_start_like_a_colour_do_not_match() -> None:
    # "kumas" (fabric) must not read as "kum" (sand), "narin" (thin) not as "nar" (pomegranate red)
    assert try_map_color("Kumaş") is None
    assert try_map_color("Narin") is None
