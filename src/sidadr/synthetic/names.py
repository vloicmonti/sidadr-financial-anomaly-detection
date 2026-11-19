"""Noms fictifs et variantes orthographiques.

Les noms sont des combinaisons aléatoires de prénoms et de noms de famille courants.
Toute ressemblance avec une personne réelle serait fortuite.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

FIRST_NAMES: tuple[str, ...] = (
    "Jean", "Pierre", "Paul", "Marie", "Claire", "Esther", "Hélène", "Joseph", "Emmanuel",
    "Christelle", "Brice", "Arnaud", "Sandrine", "Aminatou", "Ibrahim", "Fatimatou", "Rodrigue",
    "Yannick", "Carine", "Blaise", "Serge", "Gisèle", "Thérèse", "Alain", "Martial", "Josiane",
    "Hervé", "Prisca", "Landry", "Olivier", "Joël", "Nadège", "Armelle", "Didier", "Laurette",
    "Patrice", "Raïssa", "Cédric", "Mireille", "Fabrice",
)  # fmt: skip

LAST_NAMES: tuple[str, ...] = (
    "MANGA", "ATANGANA", "ESSOMBA", "KAMGA", "FOTSO", "TCHOUA", "NJOYA", "MBARGA", "ONANA",
    "EKANI", "NKOULOU", "ABENA", "BELLA", "NANA", "TAMO", "DJOMO", "KENGNE", "NGUEMA",
    "MOUSSA", "BELLO", "ABDOULAYE", "HAMADOU", "OYONO", "MVONDO", "ETOA", "NDJOCK", "EYENGA",
    "MEKONGO", "OWONA", "NDZANA", "FOUDA", "AYISSI", "TCHAMBA", "WANDJI", "NGO BIYONG",
    "MBIDA", "ZANGA", "EBODE", "NGANDO", "SIME",
)  # fmt: skip


class NameStyle(StrEnum):
    """Façon dont chaque système source enregistre un nom."""

    LAST_FIRST = "last_first"  # MANGA Jean Pierre
    FIRST_LAST = "first_last"  # Jean Pierre MANGA
    UPPER_LAST_FIRST = "upper_last_first"  # MANGA JEAN PIERRE


class Variant(StrEnum):
    """Altérations appliquées à un nom pour simuler une saisie différente."""

    NONE = "none"
    SWAPPED = "swapped"  # ordre nom/prénoms inversé par rapport au style de la source
    HYPHENATED = "hyphenated"  # Jean-Pierre
    COLLAPSED = "collapsed"  # Jeanpierre, NGOBIYONG
    INITIALS = "initials"  # J. P. MANGA
    NO_ACCENTS = "no_accents"  # Helene
    TYPO = "typo"  # une lettre remplacée, supprimée ou inversée dans le nom de famille
    EXTRA_SPACE = "extra_space"  # espaces superflus


@dataclass(frozen=True)
class PersonName:
    first_names: tuple[str, ...]
    last_name: str

    @property
    def is_compound(self) -> bool:
        return len(self.first_names) > 1 or " " in self.last_name


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def format_name(name: PersonName, style: NameStyle, swap: bool = False) -> str:
    """Rend un nom selon le style d'une source, éventuellement avec l'ordre inversé."""
    first = " ".join(name.first_names)
    last = name.last_name.upper()
    last_first = style in (NameStyle.LAST_FIRST, NameStyle.UPPER_LAST_FIRST)
    if swap:
        last_first = not last_first
    text = f"{last} {first}" if last_first else f"{first} {last}"
    return text.upper() if style is NameStyle.UPPER_LAST_FIRST else text


def applicable_variants(name: PersonName) -> list[Variant]:
    variants = [Variant.SWAPPED, Variant.INITIALS, Variant.TYPO, Variant.EXTRA_SPACE]
    if len(name.first_names) > 1:
        variants.append(Variant.HYPHENATED)
    if name.is_compound:
        variants.append(Variant.COLLAPSED)
    if strip_accents(" ".join(name.first_names)) != " ".join(name.first_names):
        variants.append(Variant.NO_ACCENTS)
    return variants


def _typo(word: str, rng: random.Random) -> str:
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    if len(letters) < 4:
        return word
    i = rng.choice(letters[1:-1])
    kind = rng.choice(("substitute", "delete", "transpose"))
    if kind == "delete":
        return word[:i] + word[i + 1 :]
    if kind == "transpose" and i + 1 < len(word) and word[i + 1].isalpha():
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    replacement = rng.choice([c for c in "AEIOUNMRST" if c != word[i].upper()])
    return word[:i] + (replacement if word[i].isupper() else replacement.lower()) + word[i + 1 :]


def render_variant(name: PersonName, style: NameStyle, variant: Variant, rng: random.Random) -> str:
    """Rend un nom dans le style d'une source, altéré par une variante donnée."""
    first_names = name.first_names
    last_name = name.last_name
    swap = False

    if variant is Variant.SWAPPED:
        swap = True
    elif variant is Variant.HYPHENATED:
        first_names = ("-".join(first_names),)
    elif variant is Variant.COLLAPSED:
        if len(first_names) > 1:
            first_names = ("".join(first_names).capitalize(),)
        else:
            last_name = last_name.replace(" ", "")
    elif variant is Variant.INITIALS:
        first_names = tuple(f"{f[0]}." for f in first_names)
    elif variant is Variant.TYPO:
        last_name = _typo(last_name, rng)

    text = format_name(PersonName(first_names, last_name), style, swap=swap)

    if variant is Variant.NO_ACCENTS:
        text = strip_accents(text)
    elif variant is Variant.EXTRA_SPACE:
        parts = text.split(" ")  # toujours >= 2 jetons : nom + au moins un prénom
        pos = rng.randrange(1, len(parts))
        text = " ".join(parts[:pos]) + "  " + " ".join(parts[pos:])
        if rng.random() < 0.5:
            text = text + " "
    return text


def random_name(rng: random.Random, compound_first_rate: float = 0.25) -> PersonName:
    first = [rng.choice(FIRST_NAMES)]
    if rng.random() < compound_first_rate:
        second = rng.choice([f for f in FIRST_NAMES if f != first[0]])
        first.append(second)
    return PersonName(tuple(first), rng.choice(LAST_NAMES))
