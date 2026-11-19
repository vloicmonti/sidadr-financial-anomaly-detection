"""Le jeu versionné dans data/synthetic doit être exactement celui que produit le code."""

import json
from pathlib import Path

import pytest

from sidadr.synthetic.generator import GeneratorConfig, generate
from sidadr.synthetic.writers import write_dataset

COMMITTED = Path(__file__).resolve().parents[2] / "data" / "synthetic"


@pytest.mark.skipif(not (COMMITTED / "metadata.json").exists(), reason="jeu de référence absent")
def test_committed_dataset_is_reproducible(tmp_path: Path) -> None:
    metadata = json.loads((COMMITTED / "metadata.json").read_text(encoding="utf-8"))
    config = GeneratorConfig(rows=metadata["base_rows"], seed=metadata["seed"])
    write_dataset(generate(config), tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix != ".xlsx":
            committed = COMMITTED / path.relative_to(tmp_path)
            assert committed.read_bytes() == path.read_bytes(), f"{committed} n'est plus à jour"
