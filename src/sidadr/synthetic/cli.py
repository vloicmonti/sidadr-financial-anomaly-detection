"""Ligne de commande : `sidadr-generate --rows 20000 --seed 42 --out data/synthetic`."""

from __future__ import annotations

import argparse
from pathlib import Path

from sidadr.synthetic.generator import GeneratorConfig, generate
from sidadr.synthetic.writers import write_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère le jeu de données synthétique SIDADR.")
    parser.add_argument("--rows", type=int, default=20_000, help="nombre de transactions de base")
    parser.add_argument("--seed", type=int, default=42, help="graine aléatoire (reproductibilité)")
    parser.add_argument(
        "--out", type=Path, default=Path("data/synthetic"), help="dossier de sortie"
    )
    args = parser.parse_args(argv)

    dataset = generate(GeneratorConfig(rows=args.rows, seed=args.seed))
    metadata = write_dataset(dataset, args.out)

    print(f"Jeu synthétique écrit dans {args.out} (graine {args.seed})")
    files = metadata["files"]
    assert isinstance(files, dict)
    for name, count in files.items():
        print(f"  {name:<16} {count:>7} lignes")
    defects = metadata["defects"]
    assert isinstance(defects, dict)
    print(f"  défauts injectés : {sum(defects.values())}")
    for name, count in defects.items():
        print(f"    {name:<32} {count:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
