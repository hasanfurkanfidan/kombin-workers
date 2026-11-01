import pytest

from kombin_workers.normalize import categories
from kombin_workers.normalize.categories import CATEGORY_SLUGS, map_category


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Tişört", "ust-giyim/tisort"),
        ("Bisiklet Yaka Baskılı T-Shirt", "ust-giyim/tisort"),
        ("Polo Yaka Tişört", "ust-giyim/tisort"),
        ("Klasik Yaka Gömlek", "ust-giyim/gomlek"),
        ("Denim Gömlek", "ust-giyim/gomlek"),
        ("Kazak", "ust-giyim/triko"),
        ("Fitilli Triko Hırka", "ust-giyim/triko"),
        ("Blazer Ceket", "ust-giyim/blazer"),
        ("Pantolon", "alt-giyim/pantolon"),
        ("Jogger Pantolon", "alt-giyim/pantolon"),
        ("Chino", "alt-giyim/pantolon"),
        ("Slim Fit Jean Pantolon", "alt-giyim/jean"),
        ("Kot Pantolon", "alt-giyim/jean"),
        ("Midi Etek", "alt-giyim/etek"),
        ("Şişme Mont", "dis-giyim/mont"),
        ("Kapüşonlu Parka", "dis-giyim/mont"),
        ("Deri Mont", "dis-giyim/mont"),
        ("Kaban", "dis-giyim/kaban"),
        ("Yün Palto", "dis-giyim/kaban"),
        ("Trençkot", "dis-giyim/kaban"),
        ("Deri Ceket", "dis-giyim/ceket"),
        ("Bomber Ceket", "dis-giyim/ceket"),
        ("Jean Ceket", "dis-giyim/ceket"),
        ("Gömlek Ceket", "dis-giyim/ceket"),
        ("Erkek Deri Sneaker", "ayakkabi/sneaker"),
        ("Spor Ayakkabı", "ayakkabi/sneaker"),
        ("Chelsea Bot", "ayakkabi/bot"),
        ("Kadın Çizme", "ayakkabi/bot"),
        ("Erkek Deri Loafer", "ayakkabi/loafer"),
        ("Klasik Ayakkabı", "ayakkabi/loafer"),
        ("Makosen", "ayakkabi/loafer"),
        ("Omuz Çantası", "aksesuar/canta"),
        ("Sırt Çantası", "aksesuar/canta"),
        ("Deri Kemer", "aksesuar/kemer"),
        ("Klasik Kemerler", "aksesuar/kemer"),
    ],
)
def test_keyword_rules(label: str, expected: str) -> None:
    assert map_category(label) == expected
    assert expected in CATEGORY_SLUGS


@pytest.mark.parametrize(
    "label",
    [
        "Sweatshirt",
        "Kapüşonlu Hoodie",
        "Elbise",
        "Gömlek Elbise",
        "Şort",
        "Bermuda",
        "Sandalet",
        "Terlik",
        "Çorap",
        "Cüzdan",
        "Kravat",
        "Takım Elbise",
        "Atkı",
        "Bluz",
        "Tulum",
        "Yelek",
        "Topuklu Ayakkabı",
        "Parfüm",
        "Hologram",
        "",
    ],
)
def test_unknown_or_unsupported_labels_are_skipped(label: str) -> None:
    assert map_category(label) is None


def test_none_and_empty_labels() -> None:
    assert map_category(None, "", None) is None


def test_belted_garments_are_not_belts() -> None:
    # "kemerli" is an adjective ("with a belt"): the coat is a coat, the dress is skipped, neither is a belt
    assert map_category("Kemerli Trençkot") == "dis-giyim/kaban"
    assert map_category("Kemerli Elbise") is None


def test_t_shirt_is_not_filed_as_shirt_and_sweatshirt_not_as_tshirt() -> None:
    assert map_category("Basic T-Shirt") == "ust-giyim/tisort"
    assert map_category("Basic Sweatshirt") is None


def test_the_first_label_that_decides_wins() -> None:
    assert map_category("Slim Jean", "Pantolon") == "alt-giyim/jean"
    assert map_category("Basic Model", "Gömlek") == "ust-giyim/gomlek"
    assert map_category("Elbise", "Gömlek") is None  # a skip word in an earlier label stops the search


def test_per_source_override_beats_the_rules() -> None:
    assert map_category("Sweats", source="jack-jones") is None
    assert map_category("Jeans", source="jack-jones") == "alt-giyim/jean"
    assert map_category("T-Shirts & Tops", source="jack-jones") == "ust-giyim/tisort"
    # the same label without the source's table follows the generic rules
    assert map_category("Sweats") is None
    assert map_category("Knitwear", source="jack-jones") == "ust-giyim/triko"


def test_override_can_force_a_mapping_the_rules_would_not_make(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(categories.SOURCE_OVERRIDES, "acme", {"kombinler": "ust-giyim/gomlek"})
    assert map_category("Kombinler", source="acme") == "ust-giyim/gomlek"
    assert map_category("Kombinler") is None
    assert map_category("Kombinler", source="other") is None


def test_every_rule_targets_a_known_slug() -> None:
    assert {slug for _, slug in categories.RULES} <= CATEGORY_SLUGS
    for table in categories.SOURCE_OVERRIDES.values():
        assert {slug for slug in table.values() if slug} <= CATEGORY_SLUGS
