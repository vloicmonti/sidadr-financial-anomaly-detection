"""Générateur de données financières synthétiques avec défauts injectés et vérité terrain.

Toutes les données produites sont fictives. Aucune donnée réelle (BEAC, banque ou client)
n'est utilisée.
"""

from sidadr.synthetic.generator import GeneratorConfig, generate
from sidadr.synthetic.writers import write_dataset

__all__ = ["GeneratorConfig", "generate", "write_dataset"]
