import random

import pytest

from sidadr.synthetic.names import (
    NameStyle,
    PersonName,
    Variant,
    applicable_variants,
    format_name,
    render_variant,
    strip_accents,
)

JEAN_PIERRE = PersonName(("Jean", "Pierre"), "MANGA")


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (NameStyle.LAST_FIRST, "MANGA Jean Pierre"),
        (NameStyle.FIRST_LAST, "Jean Pierre MANGA"),
        (NameStyle.UPPER_LAST_FIRST, "MANGA JEAN PIERRE"),
    ],
)
def test_format_name_follows_source_style(style: NameStyle, expected: str) -> None:
    assert format_name(JEAN_PIERRE, style) == expected


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        (Variant.HYPHENATED, "Jean-Pierre MANGA"),
        (Variant.COLLAPSED, "Jeanpierre MANGA"),
        (Variant.INITIALS, "J. P. MANGA"),
        (Variant.SWAPPED, "MANGA Jean Pierre"),
    ],
)
def test_brief_examples_are_reproduced(variant: Variant, expected: str) -> None:
    rng = random.Random(0)
    assert render_variant(JEAN_PIERRE, NameStyle.FIRST_LAST, variant, rng) == expected


def test_collapsed_applies_to_compound_last_name_when_single_first_name() -> None:
    name = PersonName(("Esther",), "NGO BIYONG")
    assert render_variant(
        name, NameStyle.UPPER_LAST_FIRST, Variant.COLLAPSED, random.Random(0)
    ) == ("NGOBIYONG ESTHER")


def test_no_accents_removes_diacritics_only() -> None:
    name = PersonName(("Hélène",), "KAMGA")
    rendered = render_variant(name, NameStyle.LAST_FIRST, Variant.NO_ACCENTS, random.Random(0))
    assert rendered == "KAMGA Helene"
    assert strip_accents("Thérèse Raïssa") == "Therese Raissa"


def test_typo_is_a_single_small_edit_inside_the_last_name() -> None:
    for seed in range(200):
        rendered = render_variant(
            JEAN_PIERRE, NameStyle.FIRST_LAST, Variant.TYPO, random.Random(seed)
        )
        last = rendered.split(" ")[-1]
        assert rendered.startswith("Jean Pierre ")
        assert abs(len(last) - len("MANGA")) <= 1
        assert last[0] == "M"


def test_extra_space_inserts_a_double_space() -> None:
    for seed in range(50):
        rendered = render_variant(
            JEAN_PIERRE, NameStyle.LAST_FIRST, Variant.EXTRA_SPACE, random.Random(seed)
        )
        assert "  " in rendered
        assert rendered.split() == ["MANGA", "Jean", "Pierre"]


def test_applicable_variants_depend_on_the_name() -> None:
    simple = PersonName(("Paul",), "ONANA")
    assert Variant.HYPHENATED not in applicable_variants(simple)
    assert Variant.COLLAPSED not in applicable_variants(simple)
    assert Variant.NO_ACCENTS not in applicable_variants(simple)
    assert {Variant.HYPHENATED, Variant.COLLAPSED} <= set(applicable_variants(JEAN_PIERRE))
