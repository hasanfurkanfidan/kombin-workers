import pytest

from kombin_workers.models import Audience, Fit, Pattern, Season
from kombin_workers.normalize.attributes import (
    infer_attributes,
    infer_fit,
    infer_formality,
    infer_material,
    infer_pattern,
    infer_seasons,
)
from kombin_workers.normalize.audience import map_audience


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Slim Fit Jean Pantolon", Fit.SLIM),
        ("Skinny jean", Fit.SLIM),
        ("Dar kalıp tişört", Fit.SLIM),
        ("Oversize Tişört", Fit.OVERSIZED),
        ("Bol kalıp sweat", Fit.OVERSIZED),
        ("Rahat Kalıp Bisiklet Yaka Tişört", Fit.RELAXED),
        ("Relaxed fit gömlek", Fit.RELAXED),
        ("Wide leg pantolon", Fit.RELAXED),
        ("Baggy jean", Fit.RELAXED),
        ("Regular fit gömlek", Fit.REGULAR),
        ("Normal kalıp polo", Fit.REGULAR),
        ("Straight jean", Fit.REGULAR),
        ("slimfit", Fit.SLIM),
        ("relaxedfit", Fit.RELAXED),
        ("Düz Renk Gömlek", Fit.UNKNOWN),
        ("", Fit.UNKNOWN),
    ],
)
def test_fit(text: str, expected: Fit) -> None:
    assert infer_fit(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Çizgili Gömlek", Pattern.STRIPED),
        ("Striped tee", Pattern.STRIPED),
        ("Ekose Gömlek", Pattern.CHECKED),
        ("Kareli Etek", Pattern.CHECKED),
        ("Pötikare Gömlek", Pattern.CHECKED),
        ("Logo Baskılı Tişört", Pattern.PRINTED),
        ("Çiçek Desenli Gömlek", Pattern.PRINTED),
        ("Leopar Desenli Çanta", Pattern.PRINTED),
        ("Kabartmalı Kazak", Pattern.TEXTURED),
        ("Kapitone Mont", Pattern.TEXTURED),
        ("Nubuk Deri Sneaker", Pattern.TEXTURED),
        ("Düz Renk Tişört", Pattern.SOLID),
        ("Basic Tişört", Pattern.SOLID),
        ("Çizgili baskılı tişört", Pattern.STRIPED),
        ("Gömlek", None),
    ],
)
def test_pattern(text: str, expected: Pattern | None) -> None:
    assert infer_pattern(text) is expected


@pytest.mark.parametrize(
    ("category", "text", "expected"),
    [
        ("ust-giyim/tisort", "Basic Tişört", 1),
        ("ust-giyim/gomlek", "Klasik Yaka Gömlek", 4),
        ("ust-giyim/blazer", "Blazer Ceket", 4),
        ("ust-giyim/blazer", "Smokin Blazer", 5),
        ("alt-giyim/jean", "Slim Jean", 2),
        ("alt-giyim/pantolon", "Jogger Pantolon", 2),
        ("alt-giyim/pantolon", "Kumaş Pantolon", 4),
        ("ayakkabi/sneaker", "Spor Sneaker", 1),
        ("ayakkabi/loafer", "Deri Loafer", 4),
        ("ayakkabi/loafer", "Klasik Rugan Ayakkabı", 5),
        ("dis-giyim/kaban", "Yün Kaban", 4),
        ("dis-giyim/mont", "Şişme Mont", 2),
        ("aksesuar/kemer", "Deri Kemer", 3),
        ("bilinmeyen", "Bir Şey", 3),
    ],
)
def test_formality(category: str, text: str, expected: int) -> None:
    assert infer_formality(category, text) == expected


def test_formality_is_clamped_to_1_5() -> None:
    assert infer_formality("ust-giyim/tisort", "Spor Casual Günlük") == 1
    assert infer_formality("ust-giyim/blazer", "Klasik Smokin Davet") == 5


@pytest.mark.parametrize(
    ("category", "text", "expected"),
    [
        ("dis-giyim/mont", "Şişme Mont", [Season.AUTUMN, Season.WINTER]),
        ("ust-giyim/triko", "Kışlık Kalın Triko", [Season.AUTUMN, Season.WINTER]),
        ("ust-giyim/tisort", "Kısa Kollu Tişört", [Season.SPRING, Season.SUMMER]),
        ("alt-giyim/pantolon", "Keten Pantolon", [Season.SPRING, Season.SUMMER]),
        ("ust-giyim/gomlek", "2025 SPRING/SUMMER", [Season.SPRING, Season.SUMMER]),
        ("dis-giyim/ceket", "Mevsimlik Ceket", [Season.SPRING, Season.AUTUMN]),
        ("dis-giyim/kaban", "Kaban", [Season.AUTUMN, Season.WINTER]),
        ("dis-giyim/mont", "Mont", [Season.AUTUMN, Season.WINTER]),
        ("ayakkabi/sneaker", "Sneaker kış", [Season.AUTUMN, Season.WINTER]),
        ("ust-giyim/tisort", "Tişört", None),
        ("ust-giyim/tisort", "Kısa kollu kışlık", None),
    ],
)
def test_seasons(category: str, text: str, expected: list[Season] | None) -> None:
    assert infer_seasons(category, text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("%98 Pamuk %2 Elastan", "%98 Pamuk, %2 Elastan"),
        ("Kabuk: 100% Pamuk", "%100 Pamuk"),
        ("%60 Polyester, %40 Viskon", "%60 Polyester, %40 Viskon"),
        ("%2 Elastan %98 Pamuk", "%98 Pamuk, %2 Elastan"),
        ("Hakiki deri ayakkabı", "Deri"),
        ("Keten gömlek", "Keten"),
        ("Yünlü kaban", "Yün"),
        ("Süet bot", "Süet"),
        ("Denim ceket", "Denim"),
        ("Saten görünümlü", "Saten"),
        ("Basic gömlek", None),
        ("", None),
    ],
)
def test_material(text: str, expected: str | None) -> None:
    assert infer_material(text) == expected


def test_infer_attributes_bundles_everything_and_prefers_dedicated_material_field() -> None:
    result = infer_attributes(
        "ust-giyim/gomlek",
        "Slim Fit Çizgili Gömlek",
        "Şık bir gömlek; denim pantolonla kombinleyin.",
        material_texts=("%100 Pamuk",),
    )
    assert result.fit is Fit.SLIM
    assert result.pattern is Pattern.STRIPED
    assert result.material == "%100 Pamuk"
    assert 1 <= result.formality <= 5


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("Kadın",), Audience.WOMEN),
        (("Erkek",), Audience.MEN),
        (("Kadın", "Giyim"), Audience.WOMEN),
        (("gender-male",), Audience.MEN),
        (("feed-gender-female",), Audience.WOMEN),
        (("Male",), Audience.MEN),
        (("Women",), Audience.WOMEN),
        (("Erkek Çocuk",), Audience.KIDS),
        (("Kız Çocuk",), Audience.KIDS),
        (("Unisex",), Audience.UNISEX),
        (("Erkek", "Kadın"), None),
        (("Giyim",), None),
        ((None, ""), None),
    ],
)
def test_audience(texts: tuple[str | None, ...], expected: Audience | None) -> None:
    assert map_audience(*texts) is expected
