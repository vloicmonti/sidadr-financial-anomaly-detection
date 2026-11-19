"""Normalisation des noms et clés de blocage."""

from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[\s\-'’.,_/]+")
_NOT_LETTER = re.compile(r"[^A-Z ]")


def normalize_name(name: str) -> str:
    """« Jean-Pierre  MANGA » → « JEAN PIERRE MANGA ».

    Accents retirés, majuscules, tirets/apostrophes/points remplacés par des espaces,
    espaces multiples réduits. Les chiffres et symboles restants sont supprimés.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    text = _SEPARATORS.sub(" ", text)
    text = _NOT_LETTER.sub("", text)
    return " ".join(text.split())


def tokens(normalized: str) -> tuple[str, ...]:
    return tuple(normalized.split())


def block_keys(name_tokens: tuple[str, ...]) -> set[str]:
    """Début et fin (3 lettres) de chaque mot d'au moins 3 lettres.

    Deux noms ne sont comparés que s'ils partagent au moins une clé. La fin de mot
    permet de retrouver une faute de frappe au début d'un nom (« MRNGA » / « MANGA »).
    """
    keys: set[str] = set()
    for t in name_tokens:
        if len(t) >= 3:
            keys.add(f"P:{t[:3]}")
            keys.add(f"S:{t[-3:]}")
    return keys
