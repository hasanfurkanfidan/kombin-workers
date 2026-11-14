import pytest

from kombin_workers.normalize.sizes import normalize_size, normalize_sizes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("XS", ["XS"]),
        ("s", ["S"]),
        (" m ", ["M"]),
        ("L", ["L"]),
        ("xl", ["XL"]),
        ("XXL", ["XXL"]),
        ("2XL", ["XXL"]),
        ("3XL", ["XXXL"]),
        ("Small", ["S"]),
        ("Medium", ["M"]),
        ("X-Large", ["XL"]),
        ("S/M", ["S", "M"]),
        ("M/L", ["M", "L"]),
        ("S / M", ["S", "M"]),
        ("L-XL", ["L", "XL"]),
        ("42", ["42"]),
        ("42.5", ["42.5"]),
        ("42,5", ["42.5"]),
        ("42.0", ["42"]),
        ("42 1/2", ["42.5"]),
        ("42½", ["42.5"]),
        ("36", ["36"]),
        ("32/34", ["32/34"]),
        ("W32 L34", ["32/34"]),
        ("32X34", ["32/34"]),
        ("36-38", ["36-38"]),
        ("Standart", ["STD"]),
        ("TEK EBAT", ["STD"]),
        ("One Size", ["STD"]),
        ("STND", ["STD"]),
        ("6 Yaş", ["6Y"]),
        ("3-4 Yaş", ["3-4Y"]),
        ("9-12 Ay", ["9-12M"]),
        ("Beden 40", ["40"]),
        ("40 Numara", ["40"]),
    ],
)
def test_normalize_size(raw: str, expected: list[str]) -> None:
    assert normalize_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "-", "Beden Seçiniz", "Beden seçiniz", "N/A"])
def test_placeholders_produce_nothing(raw: str | None) -> None:
    assert normalize_size(raw) == []


def test_normalize_sizes_dedupes_and_sorts() -> None:
    assert normalize_sizes(["M", "s/m", "L", "m", None, "Beden Seçiniz", "S"]) == ["S", "M", "L"]


def test_sizes_come_out_in_reading_order() -> None:
    assert normalize_sizes(["XL", "M", "S", "L", "XXL", "XS"]) == ["XS", "S", "M", "L", "XL", "XXL"]
    assert normalize_sizes(["44", "40", "41.5", "41", "39"]) == ["39", "40", "41", "41.5", "44"]
    assert normalize_sizes(["STD", "L", "42", "32/34"]) == ["L", "42", "32/34", "STD"]


def test_a_size_never_exceeds_the_backend_limit() -> None:
    assert all(len(size) <= 20 for raw in ("W32 L34", "9-12 Ay", "XXXL", "42.5") for size in normalize_size(raw))
    assert normalize_size("Ç" * 25) == []
