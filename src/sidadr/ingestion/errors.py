"""Erreurs d'import : chaque refus porte un code stable et un message compréhensible."""

from __future__ import annotations


class ImportRejected(Exception):
    """Le fichier ne peut pas être importé. Aucune donnée n'est conservée."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class MappingError(ValueError):
    """La correspondance des colonnes est incomplète, contradictoire ou non confirmée."""
