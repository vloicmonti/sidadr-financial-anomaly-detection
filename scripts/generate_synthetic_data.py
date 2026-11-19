"""Génère le jeu synthétique de référence.

Exemple : python scripts/generate_synthetic_data.py --rows 20000 --seed 42 --out data/synthetic
"""

from sidadr.synthetic.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
