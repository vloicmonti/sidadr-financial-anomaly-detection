"""Chargement et validation des règles métier déclaratives (YAML).

Toute erreur de configuration est détectée au chargement, avec l'identifiant de la règle
en cause : une règle mal écrite ne doit jamais produire de résultats silencieusement faux.
Aucune expression n'est évaluée comme du code.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from sidadr.config import ConfigError, Schema, config_dir, default_schema
from sidadr.findings import Severity

COMPARISONS = ("<", "<=", ">", ">=", "==", "!=")
SET_OPS = ("in", "not_in")
OPERATORS = (*COMPARISONS, *SET_OPS, "not_null", "regex")
DIMENSIONS = ("consistency", "completeness", "validity")

RuleValue = Decimal | date | str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    description: str
    fields: tuple[str, ...]
    op: str
    severity: Severity
    recommendation: str
    dimension: str = "consistency"
    value: RuleValue | None = None
    other_field: str | None = None
    allowed: frozenset[str] = frozenset()
    reference_name: str | None = None
    pattern: re.Pattern[str] | None = None
    when: tuple[tuple[str, frozenset[str]], ...] = ()

    def expected(self) -> str:
        """Ce que la règle attend, en clair."""
        if self.op == "not_null":
            return "une valeur renseignée"
        if self.op in SET_OPS:
            if self.reference_name:
                place = (
                    "dans le" if self.op == "in" else "hors du"
                ) + f" référentiel « {self.reference_name} »"
            else:
                place = ("dans la" if self.op == "in" else "hors de la") + " liste autorisée"
            return f"{place} ({len(self.allowed)} valeurs)"
        if self.op == "regex":
            assert self.pattern is not None
            return f"au format {self.pattern.pattern}"
        target = self.other_field if self.other_field else _show(self.value)
        return f"{self.op} {target}"


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    parameters: dict[str, RuleValue]
    source: Path

    def __getitem__(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.rule_id == rule_id)


def _show(value: RuleValue | None) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _coerce(raw: Any, field_type: str, where: str) -> RuleValue:
    """Convertit une valeur de règle dans le type du champ comparé."""
    if field_type == "decimal":
        try:
            return Decimal(str(raw))
        except InvalidOperation as exc:
            raise ConfigError(f"{where} : {raw!r} n'est pas un nombre.") from exc
    if field_type == "date":
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw))
        except ValueError as exc:
            raise ConfigError(f"{where} : {raw!r} n'est pas une date AAAA-MM-JJ.") from exc
    return str(raw).strip()


def _load_reference(spec: Any, base: Path, name: str) -> frozenset[str]:
    if not isinstance(spec, dict) or "file" not in spec or "column" not in spec:
        raise ConfigError(f"Référentiel {name!r} : clés 'file' et 'column' obligatoires.")
    path = (base / str(spec["file"])).resolve()
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            column = str(spec["column"])
            if reader.fieldnames is None or column not in reader.fieldnames:
                raise ConfigError(
                    f"Référentiel {name!r} : colonne {column!r} absente de {path.name}."
                )
            values = frozenset(str(row[column]).strip() for row in reader if row[column])
    except FileNotFoundError as exc:
        raise ConfigError(f"Référentiel {name!r} introuvable : {path}") from exc
    if not values:
        raise ConfigError(f"Référentiel {name!r} vide : {path}")
    return values


def load_rules(
    path: Path | None = None, schema: Schema | None = None, overrides: dict[str, Any] | None = None
) -> RuleSet:
    """Charge un fichier de règles. `overrides` remplace des paramètres (ex. date d'arrêté)."""
    path = path or config_dir() / "rules" / "transactions.yaml"
    schema = schema or default_schema()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Fichier de règles introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list) or not raw["rules"]:
        raise ConfigError(f"{path.name} : la clé 'rules' doit lister au moins une règle.")

    parameters: dict[str, Any] = {**(raw.get("parameters") or {}), **(overrides or {})}
    references = {
        name: _load_reference(spec, path.parent, name)
        for name, spec in (raw.get("references") or {}).items()
    }

    rules: list[Rule] = []
    for index, spec in enumerate(raw["rules"], start=1):
        rule_id = str(spec.get("id") or f"#{index}") if isinstance(spec, dict) else f"#{index}"
        where = f"Règle {rule_id}"
        if not isinstance(spec, dict):
            raise ConfigError(f"{where} : doit être un dictionnaire.")
        if any(r.rule_id == rule_id for r in rules):
            raise ConfigError(f"{where} : identifiant en double.")
        check = spec.get("check")
        if not isinstance(check, dict):
            raise ConfigError(f"{where} : clé 'check' obligatoire.")

        fields = tuple(str(f) for f in (check.get("fields") or [check.get("field")]) if f)
        if not fields:
            raise ConfigError(f"{where} : 'field' ou 'fields' obligatoire.")
        unknown = [f for f in fields if f not in schema.names]
        if unknown:
            raise ConfigError(f"{where} : champs inconnus du schéma : {', '.join(unknown)}.")
        op = str(check.get("op", ""))
        if op not in OPERATORS:
            raise ConfigError(
                f"{where} : opérateur {op!r} inconnu (attendus : {', '.join(OPERATORS)})."
            )
        try:
            severity = Severity(str(spec.get("severity", "")))
        except ValueError as exc:
            raise ConfigError(f"{where} : sévérité inconnue {spec.get('severity')!r}.") from exc
        dimension = str(spec.get("dimension", "consistency"))
        if dimension not in DIMENSIONS:
            raise ConfigError(f"{where} : dimension {dimension!r} inconnue.")

        types = {schema[f].type for f in fields}
        field_type = types.pop() if len(types) == 1 else "text"
        value: RuleValue | None = None
        other_field = None
        allowed: frozenset[str] = frozenset()
        reference_name = None
        pattern = None

        if op in COMPARISONS:
            if "other_field" in check:
                other_field = str(check["other_field"])
                if other_field not in schema.names:
                    raise ConfigError(f"{where} : champ de comparaison inconnu {other_field!r}.")
                if schema[other_field].type != field_type:
                    raise ConfigError(
                        f"{where} : {other_field} n'a pas le même type que {fields[0]}."
                    )
            elif "value" in check:
                raw_value = check["value"]
                if isinstance(raw_value, str) and raw_value.startswith("$"):
                    name = raw_value[1:]
                    if name not in parameters:
                        raise ConfigError(f"{where} : paramètre ${name} non défini.")
                    raw_value = parameters[name]
                value = _coerce(raw_value, field_type, where)
            else:
                raise ConfigError(f"{where} : 'value' ou 'other_field' obligatoire pour {op}.")
            if field_type in ("text", "enum") and op not in ("==", "!="):
                raise ConfigError(f"{where} : {op} n'a pas de sens sur un champ texte.")
        elif op in SET_OPS:
            if "reference" in check:
                reference_name = str(check["reference"])
                if reference_name not in references:
                    raise ConfigError(f"{where} : référentiel {reference_name!r} non déclaré.")
                allowed = references[reference_name]
            elif isinstance(check.get("values"), list) and check["values"]:
                allowed = frozenset(str(v).strip() for v in check["values"])
            else:
                raise ConfigError(f"{where} : 'values' ou 'reference' obligatoire pour {op}.")
        elif op == "regex":
            try:
                pattern = re.compile(str(check.get("pattern", "")))
            except re.error as exc:
                raise ConfigError(f"{where} : expression régulière invalide ({exc}).") from exc
            if not pattern.pattern:
                raise ConfigError(f"{where} : 'pattern' obligatoire pour regex.")

        when_spec = spec.get("when") or {}
        if not isinstance(when_spec, dict):
            raise ConfigError(f"{where} : 'when' doit être un dictionnaire champ -> valeurs.")
        when = []
        for f, values in when_spec.items():
            if f not in schema.names:
                raise ConfigError(f"{where} : champ de condition inconnu {f!r}.")
            listed = values if isinstance(values, list) else [values]
            when.append((str(f), frozenset(str(v).strip().upper() for v in listed)))

        rules.append(
            Rule(
                rule_id=rule_id,
                description=str(spec.get("description", "")),
                fields=fields,
                op=op,
                severity=severity,
                recommendation=str(spec.get("recommendation", "")),
                dimension=dimension,
                value=value,
                other_field=other_field,
                allowed=allowed,
                reference_name=reference_name,
                pattern=pattern,
                when=tuple(when),
            )
        )

    clean_params = {
        k: _coerce(v, "date" if isinstance(v, date) else "text", "paramètres")
        for k, v in parameters.items()
    }
    return RuleSet(tuple(rules), clean_params, path)
