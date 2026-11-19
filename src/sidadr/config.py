"""Chargement et validation de la configuration YAML (schéma, limites, score qualité)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml

FieldType = Literal["text", "decimal", "date", "enum"]
_FIELD_TYPES: tuple[str, ...] = ("text", "decimal", "date", "enum")


class ConfigError(ValueError):
    """Configuration invalide : levée au chargement, jamais en cours de traitement."""


def config_dir() -> Path:
    """Dossier de configuration : `SIDADR_CONFIG_DIR` ou `config/` à la racine du dépôt."""
    env = os.environ.get("SIDADR_CONFIG_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "config"


def _load(name: str, directory: Path | None) -> dict[str, Any]:
    path = (directory or config_dir()) / name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un dictionnaire YAML.")
    return cast(dict[str, Any], data)


# --------------------------------------------------------------------------- schéma


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    type: FieldType
    required: bool
    synonyms: tuple[str, ...]
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schema:
    fields: tuple[FieldSpec, ...]

    def __getitem__(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


def load_schema(directory: Path | None = None) -> Schema:
    raw = _load("schema.yaml", directory).get("fields")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("schema.yaml : la clé 'fields' doit lister au moins un champ.")
    fields = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"schema.yaml : le champ {name!r} doit être un dictionnaire.")
        ftype = spec.get("type")
        if ftype not in _FIELD_TYPES:
            raise ConfigError(f"schema.yaml : type inconnu {ftype!r} pour {name!r}.")
        values = tuple(str(v) for v in spec.get("values", []))
        if ftype == "enum" and not values:
            raise ConfigError(f"schema.yaml : le champ énuméré {name!r} doit lister 'values'.")
        fields.append(
            FieldSpec(
                name=str(name),
                label=str(spec.get("label", name)),
                type=cast(FieldType, ftype),
                required=bool(spec.get("required", False)),
                synonyms=tuple(str(s) for s in spec.get("synonyms", [])),
                values=values,
            )
        )
    return Schema(tuple(fields))


# --------------------------------------------------------------------------- limites


@dataclass(frozen=True)
class ImportSettings:
    allowed_extensions: tuple[str, ...] = (".csv", ".xlsx")
    max_file_mb: float = 50
    max_rows: int = 1_000_000
    max_columns: int = 200
    xlsx_max_uncompressed_mb: float = 300
    xlsx_max_compression_ratio: float = 100
    csv_sample_bytes: int = 65_536


@dataclass(frozen=True)
class ProfilingSettings:
    top_values: int = 5
    sample_record_ids: int = 5


@dataclass(frozen=True)
class Settings:
    imports: ImportSettings = ImportSettings()
    profiling: ProfilingSettings = ProfilingSettings()


def load_settings(directory: Path | None = None) -> Settings:
    raw = _load("settings.yaml", directory)
    imp = raw.get("import", {}) or {}
    prof = raw.get("profiling", {}) or {}
    try:
        imports = ImportSettings(
            allowed_extensions=tuple(
                str(e).lower() for e in imp.get("allowed_extensions", [".csv", ".xlsx"])
            ),
            max_file_mb=float(imp.get("max_file_mb", 50)),
            max_rows=int(imp.get("max_rows", 1_000_000)),
            max_columns=int(imp.get("max_columns", 200)),
            xlsx_max_uncompressed_mb=float(imp.get("xlsx_max_uncompressed_mb", 300)),
            xlsx_max_compression_ratio=float(imp.get("xlsx_max_compression_ratio", 100)),
            csv_sample_bytes=int(imp.get("csv_sample_bytes", 65_536)),
        )
        profiling = ProfilingSettings(
            top_values=int(prof.get("top_values", 5)),
            sample_record_ids=int(prof.get("sample_record_ids", 5)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"settings.yaml : valeur invalide ({exc}).") from exc
    if imports.max_file_mb <= 0 or imports.max_rows <= 0 or imports.max_columns <= 0:
        raise ConfigError("settings.yaml : les limites d'import doivent être positives.")
    return Settings(imports, profiling)


# --------------------------------------------------------------------------- score qualité


@dataclass(frozen=True)
class Dimension:
    name: str
    label: str
    weight: float
    definition: str


def load_quality_dimensions(directory: Path | None = None) -> tuple[Dimension, ...]:
    raw = _load("quality_score.yaml", directory).get("dimensions")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("quality_score.yaml : la clé 'dimensions' est obligatoire.")
    dims = []
    for name, spec in raw.items():
        try:
            weight = float(spec["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"quality_score.yaml : poids invalide pour {name!r}.") from exc
        if weight < 0:
            raise ConfigError(f"quality_score.yaml : poids négatif pour {name!r}.")
        dims.append(
            Dimension(
                str(name), str(spec.get("label", name)), weight, str(spec.get("definition", ""))
            )
        )
    if sum(d.weight for d in dims) <= 0:
        raise ConfigError("quality_score.yaml : la somme des poids doit être positive.")
    return tuple(dims)


@lru_cache(maxsize=1)
def default_schema() -> Schema:
    return load_schema()


@lru_cache(maxsize=1)
def default_settings() -> Settings:
    return load_settings()


# --------------------------------------------------------------------------- doublons


@dataclass(frozen=True)
class CombinationRule:
    name: str
    description: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateSettings:
    key_fields: tuple[str, ...] = ("transaction_id",)
    key_case_sensitive: bool = False
    combinations: tuple[CombinationRule, ...] = ()


def load_duplicate_settings(
    directory: Path | None = None, schema: Schema | None = None
) -> DuplicateSettings:
    raw = _load("duplicates.yaml", directory)
    schema = schema or load_schema(directory)
    key = raw.get("business_key") or {}
    key_fields = tuple(str(f) for f in key.get("fields", []))
    if not key_fields:
        raise ConfigError("duplicates.yaml : business_key.fields doit lister au moins un champ.")
    combinations = []
    for spec in raw.get("combinations") or []:
        fields = tuple(str(f) for f in spec.get("fields", []))
        if len(fields) < 2:
            raise ConfigError(
                f"duplicates.yaml : la combinaison {spec.get('name')!r} doit avoir 2 champs."
            )
        combinations.append(
            CombinationRule(str(spec.get("name")), str(spec.get("description", "")), fields)
        )
    unknown = sorted(
        {f for f in key_fields + tuple(f for c in combinations for f in c.fields)}
        - set(schema.names)
    )
    if unknown:
        raise ConfigError(f"duplicates.yaml : champs inconnus du schéma : {', '.join(unknown)}.")
    return DuplicateSettings(
        key_fields, bool(key.get("case_sensitive", False)), tuple(combinations)
    )
