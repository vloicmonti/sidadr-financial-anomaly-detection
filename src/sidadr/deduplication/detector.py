"""Module C — détection des doublons exacts.

Quatre niveaux, du plus certain au moins certain :

1. `exact_duplicate_row` : même identifiant, toutes les valeurs identiques, même fichier ;
2. `duplicate_key` : même identifiant dans un même fichier, mais au moins une valeur diffère ;
3. `cross_file_duplicate` : même identifiant présent dans plusieurs fichiers ;
4. `combination_match` : pas d'identifiant commun, mais une combinaison de champs
   configurée (compte, date, type, montant, devise) est identique.

Un enregistrement n'apparaît que dans un seul groupe par niveau, et une combinaison déjà
expliquée par un doublon d'identifiant n'est pas signalée une seconde fois. Rien n'est
supprimé : le détecteur produit des groupes et des constats.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from sidadr.config import DuplicateSettings, Schema, default_schema
from sidadr.findings import Finding, Severity
from sidadr.profiling.profile import comparable_frame


class DuplicateKind(StrEnum):
    EXACT_ROW = "exact_duplicate_row"
    KEY = "duplicate_key"
    CROSS_FILE = "cross_file_duplicate"
    COMBINATION = "combination_match"


_SEVERITY = {
    DuplicateKind.EXACT_ROW: Severity.CERTAIN_ERROR,
    DuplicateKind.KEY: Severity.CERTAIN_ERROR,
    DuplicateKind.CROSS_FILE: Severity.PROBABLE_INCONSISTENCY,
    DuplicateKind.COMBINATION: Severity.REVIEW_ALERT,
}

_RECOMMENDATION = {
    DuplicateKind.EXACT_ROW: "Vérifier l'extraction source ; si la ligne a été exportée deux fois, "
    "exclure la copie en conservant l'original.",
    DuplicateKind.KEY: "Comparer les valeurs divergentes avec le système source pour savoir "
    "laquelle est correcte avant toute correction.",
    DuplicateKind.CROSS_FILE: "Vérifier quelle source est responsable de l'opération : "
    "une même opération ne devrait figurer que dans un seul fichier.",
    DuplicateKind.COMBINATION: "Confirmer avec le système source s'il s'agit d'une même opération "
    "saisie deux fois ou de deux opérations légitimement identiques.",
}


@dataclass(frozen=True)
class FieldDifference:
    field: str
    values: tuple[tuple[str, str | None], ...]  # (record_id, valeur d'origine)


@dataclass(frozen=True)
class DuplicateGroup:
    group_id: str
    kind: DuplicateKind
    rule: str  # champs comparés, ou nom de la combinaison
    key_value: str
    record_ids: tuple[str, ...]
    imports: tuple[str, ...]
    sources: tuple[str, ...]  # « fichier:ligne » pour chaque enregistrement
    identical_fields: tuple[str, ...]
    differences: tuple[FieldDifference, ...]

    @property
    def size(self) -> int:
        return len(self.record_ids)

    @property
    def severity(self) -> Severity:
        return _SEVERITY[self.kind]

    def explanation(self) -> str:
        n = self.size
        if self.kind is DuplicateKind.EXACT_ROW:
            return f"{n} lignes strictement identiques, identifiant {self.key_value}."
        if self.kind is DuplicateKind.KEY:
            fields = ", ".join(d.field for d in self.differences)
            return f"Identifiant {self.key_value} présent {n} fois avec des valeurs différentes ({fields})."
        if self.kind is DuplicateKind.CROSS_FILE:
            files = ", ".join(self.imports)
            return f"Identifiant {self.key_value} présent dans plusieurs fichiers ({files})."
        return f"{n} enregistrements identiques sur la combinaison « {self.rule} » sans identifiant commun."

    def to_finding(self) -> Finding:
        first_diff = self.differences[0] if self.differences else None
        return Finding(
            finding_id=f"DUP-{self.group_id}",
            kind=self.kind.value,
            severity=self.severity,
            record_ids=self.record_ids,
            rule_id=f"DUP:{self.rule}",
            explanation=self.explanation(),
            recommendation=_RECOMMENDATION[self.kind],
            field=first_diff.field if first_diff else None,
            observed_value=self.key_value,
        )


@dataclass
class DuplicateReport:
    n_rows: int
    groups: list[DuplicateGroup]
    rows_without_key: int  # identifiant absent : ne peut pas être testé sur la clé
    settings: DuplicateSettings
    notes: list[str] = field(default_factory=list)

    def by_kind(self, kind: DuplicateKind) -> list[DuplicateGroup]:
        return [g for g in self.groups if g.kind is kind]

    def counts(self) -> dict[str, int]:
        found = Counter(g.kind.value for g in self.groups)
        return {k.value: found.get(k.value, 0) for k in DuplicateKind}

    def rows_involved(self, kinds: tuple[DuplicateKind, ...] | None = None) -> int:
        """Nombre d'enregistrements distincts impliqués dans au moins un groupe des niveaux donnés."""
        selected = [g for g in self.groups if kinds is None or g.kind in kinds]
        return len({r for g in selected for r in g.record_ids})

    def findings(self) -> list[Finding]:
        return [g.to_finding() for g in self.groups]


def _compare(
    record_index: list[int], comparable: pd.DataFrame, raw: pd.DataFrame, fields: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[FieldDifference, ...]]:
    identical, differences = [], []
    ids = raw.loc[record_index, "record_id"].tolist()
    for f in fields:
        values = comparable.loc[record_index, f].tolist()
        if len({v for v in values}) == 1:
            identical.append(f)
        else:
            originals = raw.loc[record_index, f].tolist()
            differences.append(
                FieldDifference(
                    f,
                    tuple(
                        (rid, None if v is None else str(v))
                        for rid, v in zip(ids, originals, strict=True)
                    ),
                )
            )
    return tuple(identical), tuple(differences)


def detect_duplicates(
    frame: pd.DataFrame, settings: DuplicateSettings, schema: Schema | None = None
) -> DuplicateReport:
    schema = schema or default_schema()
    frame = frame.reset_index(drop=True)
    comparable = comparable_frame(frame, schema)
    record_ids = [str(v) for v in frame["record_id"]]
    import_ids = [str(v) for v in frame["import_id"]]
    source_files = [str(v) for v in frame["source_file"]]
    source_rows = [int(v) for v in frame["source_row"]]
    fields = schema.names
    key_fields = settings.key_fields
    if not settings.key_case_sensitive:
        for f in key_fields:
            comparable[f] = comparable[f].map(lambda v: v.upper() if isinstance(v, str) else v)

    groups: list[DuplicateGroup] = []
    explained: list[frozenset[str]] = []  # groupes déjà signalés (identifiant ou ligne complète)

    def add(kind: DuplicateKind, rule: str, key_value: str, index: list[int]) -> None:
        ordered = sorted(index, key=lambda i: (import_ids[i], source_rows[i]))
        identical, differences = _compare(ordered, comparable, frame, fields)
        groups.append(
            DuplicateGroup(
                group_id=f"{len(groups) + 1:05d}",
                kind=kind,
                rule=rule,
                key_value=key_value,
                record_ids=tuple(record_ids[i] for i in ordered),
                imports=tuple(dict.fromkeys(import_ids[i] for i in ordered)),
                sources=tuple(f"{source_files[i]}:{source_rows[i]}" for i in ordered),
                identical_fields=identical,
                differences=differences,
            )
        )

    # 1-3. Doublons sur l'identifiant.
    key_present = comparable[list(key_fields)].notna().all(axis=1)
    rule = "+".join(key_fields)
    with_key = comparable[key_present]
    for key_value, members in with_key.groupby(list(key_fields), sort=True):
        index = [int(i) for i in members.index]
        if len(index) < 2:
            continue
        value = " | ".join(map(str, key_value)) if isinstance(key_value, tuple) else str(key_value)
        imports = {import_ids[i] for i in index}
        all_identical = len(comparable.loc[index, list(fields)].drop_duplicates()) == 1
        if len(imports) > 1:
            kind = DuplicateKind.CROSS_FILE
        elif all_identical:
            kind = DuplicateKind.EXACT_ROW
        else:
            kind = DuplicateKind.KEY
        add(kind, rule, value, index)
        explained.append(frozenset(record_ids[i] for i in index))

    # Lignes sans identifiant : seule l'identité complète de la ligne est testée.
    without_key = comparable[~key_present]
    for _values, members in without_key.groupby(list(fields), dropna=False, sort=True):
        index = [int(i) for i in members.index]
        if len(index) > 1:
            add(DuplicateKind.EXACT_ROW, "ligne complète", "(identifiant absent)", index)
            explained.append(frozenset(record_ids[i] for i in index))

    # 4. Combinaisons de champs.
    for combination in settings.combinations:
        cols = list(combination.fields)
        present = comparable[cols].notna().all(axis=1)
        for values, members in comparable[present].groupby(cols, sort=True):
            index = [int(i) for i in members.index]
            if len(index) < 2:
                continue
            records = frozenset(record_ids[i] for i in index)
            if any(records <= known for known in explained):
                continue  # déjà signalé comme doublon d'identifiant
            value = " | ".join(map(str, values)) if isinstance(values, tuple) else str(values)
            add(DuplicateKind.COMBINATION, combination.name, value, index)

    notes = []
    rows_without_key = int((~key_present).sum())
    if rows_without_key:
        notes.append(
            f"{rows_without_key} enregistrements sans {rule} : seuls les doublons de ligne complète "
            "et les combinaisons sont recherchés pour eux."
        )
    return DuplicateReport(len(frame), groups, rows_without_key, settings, notes)
