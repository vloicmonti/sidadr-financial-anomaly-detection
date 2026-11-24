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
    allowed_extensions: tuple[str, ...] = (".csv", ".xlsx", ".xls", ".ods")
    max_file_mb: float = 50
    max_rows: int = 1_000_000
    max_columns: int = 200
    xlsx_max_uncompressed_mb: float = 300
    xlsx_max_compression_ratio: float = 100
    csv_sample_bytes: int = 65_536


# Valeurs qu'un export écrit à la place d'une donnée absente. Comparées sans tenir compte
# de la casse ni des espaces de bord. « NA » n'y figure pas : c'est aussi un code pays.
DEFAULT_MISSING_MARKERS: tuple[str, ...] = (
    "ERROR",
    "UNKNOWN",
    "N/A",
    "NULL",
    "NONE",
    "NAN",
    "#N/A",
    "#VALUE!",
    "#VALEUR!",
    "#REF!",
    "#DIV/0!",
    "-",
    "?",
)


@dataclass(frozen=True)
class ProfilingSettings:
    top_values: int = 5
    sample_record_ids: int = 5
    # Mode générique : valeurs de remplissage traitées comme absentes (jamais modifiées).
    missing_markers: tuple[str, ...] = DEFAULT_MISSING_MARKERS


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
                str(e).lower()
                for e in imp.get("allowed_extensions", [".csv", ".xlsx", ".xls", ".ods"])
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
            missing_markers=tuple(
                str(m).strip()
                for m in prof.get("missing_markers", DEFAULT_MISSING_MARKERS)
                if str(m).strip()
            ),
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


# --------------------------------------------------------------------------- noms


@dataclass(frozen=True)
class MatchingSettings:
    high: float = 92
    intermediate: float = 88
    low: float = 80
    initials_score: float = 88
    birth_date_match: float = 5
    birth_date_mismatch: float = -25
    phone_match: float = 5
    phone_mismatch: float = -10
    phone_significant_digits: int = 9
    max_block_size: int = 600
    cap_unverifiable: bool = True
    cap_contradicted: bool = True


def load_matching_settings(directory: Path | None = None) -> MatchingSettings:
    raw = _load("matching.yaml", directory)
    thresholds = raw.get("thresholds") or {}
    corroboration = raw.get("corroboration") or {}
    blocking = raw.get("blocking") or {}
    try:
        settings = MatchingSettings(
            high=float(thresholds.get("high", 92)),
            intermediate=float(thresholds.get("intermediate", 88)),
            low=float(thresholds.get("low", 80)),
            initials_score=float(raw.get("initials_score", 88)),
            birth_date_match=float(corroboration.get("birth_date_match", 5)),
            birth_date_mismatch=float(corroboration.get("birth_date_mismatch", -25)),
            phone_match=float(corroboration.get("phone_match", 5)),
            phone_mismatch=float(corroboration.get("phone_mismatch", -10)),
            phone_significant_digits=int(corroboration.get("phone_significant_digits", 9)),
            max_block_size=int(blocking.get("max_block_size", 600)),
            cap_unverifiable=bool(raw.get("cap_unverifiable_at_intermediate", True)),
            cap_contradicted=bool(raw.get("cap_contradicted_at_low", True)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"matching.yaml : valeur invalide ({exc}).") from exc
    if not 0 <= settings.low <= settings.intermediate <= settings.high <= 100:
        raise ConfigError("matching.yaml : il faut 0 <= low <= intermediate <= high <= 100.")
    if settings.birth_date_mismatch > 0 or settings.phone_mismatch > 0:
        raise ConfigError("matching.yaml : une discordance doit diminuer le score (valeur <= 0).")
    if settings.max_block_size < 2 or settings.phone_significant_digits < 4:
        raise ConfigError("matching.yaml : max_block_size >= 2 et phone_significant_digits >= 4.")
    return settings


# --------------------------------------------------------------------------- multi-entités


@dataclass(frozen=True)
class CrossEntitySettings:
    min_category: str = "intermediate"
    include_multi_branch: bool = True


def load_cross_entity_settings(directory: Path | None = None) -> CrossEntitySettings:
    raw = _load("cross_entity.yaml", directory)
    min_category = str(raw.get("min_category", "intermediate"))
    if min_category not in ("high", "intermediate", "low"):
        raise ConfigError("cross_entity.yaml : min_category doit valoir high, intermediate ou low.")
    return CrossEntitySettings(min_category, bool(raw.get("include_multi_branch", True)))


# --------------------------------------------------------------------------- anomalies


@dataclass(frozen=True)
class AnomalySettings:
    fields: tuple[str, ...] = ("amount",)
    group_by: tuple[str, ...] = ("operation_type", "currency")
    log_transform: bool = True
    min_group_size: int = 30
    robust_z_enabled: bool = True
    robust_z_threshold: float = 3.5
    iqr_enabled: bool = True
    iqr_k: float = 3.0


def load_anomaly_settings(
    directory: Path | None = None, schema: Schema | None = None
) -> AnomalySettings:
    raw = _load("anomalies.yaml", directory)
    schema = schema or load_schema(directory)
    methods = raw.get("methods") or {}
    rz = methods.get("robust_zscore") or {}
    iqr = methods.get("iqr") or {}
    try:
        settings = AnomalySettings(
            fields=tuple(str(f) for f in raw.get("fields", ["amount"])),
            group_by=tuple(str(f) for f in raw.get("group_by", [])),
            log_transform=bool(raw.get("log_transform", True)),
            min_group_size=int(raw.get("min_group_size", 30)),
            robust_z_enabled=bool(rz.get("enabled", True)),
            robust_z_threshold=float(rz.get("threshold", 3.5)),
            iqr_enabled=bool(iqr.get("enabled", True)),
            iqr_k=float(iqr.get("k", 3.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"anomalies.yaml : valeur invalide ({exc}).") from exc
    for name in settings.fields:
        if name not in schema.names or schema[name].type != "decimal":
            raise ConfigError(f"anomalies.yaml : {name!r} n'est pas un champ numérique du schéma.")
    unknown = sorted(set(settings.group_by) - set(schema.names))
    if unknown:
        raise ConfigError(
            f"anomalies.yaml : champs de regroupement inconnus : {', '.join(unknown)}."
        )
    if not (settings.robust_z_enabled or settings.iqr_enabled):
        raise ConfigError("anomalies.yaml : au moins une méthode doit être activée.")
    if settings.robust_z_threshold <= 0 or settings.iqr_k <= 0 or settings.min_group_size < 5:
        raise ConfigError("anomalies.yaml : seuils positifs et min_group_size >= 5 requis.")
    return settings


# --------------------------------------------------------------------------- sérialisation du schéma


def schema_to_dict(schema: Schema) -> list[dict[str, Any]]:
    return [
        {
            "name": f.name,
            "label": f.label,
            "type": f.type,
            "required": f.required,
            "values": list(f.values),
        }
        for f in schema.fields
    ]


def schema_from_dict(raw: list[dict[str, Any]]) -> Schema:
    fields = []
    for spec in raw:
        ftype = spec.get("type")
        if ftype not in _FIELD_TYPES:
            raise ConfigError(
                f"Schéma de session : type inconnu {ftype!r} pour {spec.get('name')!r}."
            )
        fields.append(
            FieldSpec(
                name=str(spec["name"]),
                label=str(spec.get("label", spec["name"])),
                type=cast(FieldType, ftype),
                required=bool(spec.get("required", False)),
                synonyms=(),
                values=tuple(str(v) for v in spec.get("values", [])),
            )
        )
    if not fields:
        raise ConfigError("Schéma de session vide.")
    return Schema(tuple(fields))
