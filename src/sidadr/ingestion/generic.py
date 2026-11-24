"""Mode générique : un schéma déduit des fichiers eux-mêmes, pour n'importe quel tableau.

Chaque colonne devient un champ ; le nom technique est dérivé de l'en-tête et le type
(date, nombre ou texte) est déduit des valeurs. Rien n'est obligatoire par défaut :
c'est l'utilisateur qui désigne les champs obligatoires et la clé d'unicité.

Les colonnes de même nom (après normalisation) dans plusieurs fichiers sont regroupées
dans un même champ ; une colonne absente d'un fichier y est simplement vide.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from sidadr.config import FieldSpec, FieldType, Schema
from sidadr.ingestion.errors import MappingError
from sidadr.ingestion.mapping import ColumnMapping, Method, Proposal
from sidadr.ingestion.readers import RawTable
from sidadr.ingestion.values import ValueKind, infer_type

Mode = Literal["generic", "banking"]

#: Part minimale de valeurs conformes pour typer une colonne en date ou en nombre.
TYPE_CONFIDENCE = 0.8
# Types qu'un utilisateur peut imposer : une liste de valeurs (enum) exigerait ses valeurs.
OVERRIDABLE_TYPES: tuple[FieldType, ...] = ("text", "decimal", "date")

#: Mots qui désignent un identifiant ou un code : la colonne reste du texte même si elle est
#: numérique (on ne calcule pas de moyenne ni d'anomalie sur un numéro de compte).
IDENTIFIER_HINTS = (
    "id", "code", "num", "numero", "no", "ref", "reference", "siret", "siren", "iban", "bic",
    "compte", "account", "tel", "telephone", "phone", "mobile", "zip", "postal", "cp", "matricule",
)  # fmt: skip


def field_name(header: str) -> str:
    """« Date d'opération » → « date_d_operation » ; « N° client » → « n_client »."""
    text = unicodedata.normalize("NFKD", header)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not text:
        text = "colonne"
    if text[0].isdigit():
        text = f"c_{text}"
    return text[:60]


def _looks_like_identifier(name: str, values: list[str]) -> bool:
    tokens = set(name.split("_"))
    if tokens & set(IDENTIFIER_HINTS):
        return True
    # Zéros en tête (« 00125 ») : un code, pas une quantité.
    return any(len(v) > 1 and v.startswith("0") and v.isdigit() for v in values[:500])


@dataclass(frozen=True)
class InferredField:
    name: str
    label: str
    type: FieldType
    conformity: float  # part des valeurs renseignées compatibles avec le type retenu
    filled: int
    sources: tuple[str, ...]  # fichiers où la colonne est présente
    reason: str


@dataclass
class GenericSchema:
    schema: Schema
    fields: list[InferredField]
    mappings: dict[str, ColumnMapping] = field(default_factory=dict)  # fichier -> correspondance

    def with_options(
        self,
        required: list[str] | None = None,
        types: Mapping[str, FieldType] | None = None,
    ) -> GenericSchema:
        """Marque des champs obligatoires et impose des types choisis par l'utilisateur.

        Les champs sont désignés par nom technique ou par en-tête d'origine. Un type
        imposé ne transforme aucune valeur : celles qui ne s'y conforment pas seront
        comptées comme non conformes par le profilage, jamais écartées en silence.
        """
        wanted = {self.resolve(r) for r in (required or [])}
        forced: dict[str, FieldType] = {}
        for column, ftype in (types or {}).items():
            if ftype not in OVERRIDABLE_TYPES:
                raise MappingError(
                    f"Type {ftype!r} non disponible pour {column!r} : "
                    f"choisir parmi {', '.join(OVERRIDABLE_TYPES)}."
                )
            forced[self.resolve(column)] = ftype
        fields = tuple(
            FieldSpec(f.name, f.label, forced.get(f.name, f.type), f.name in wanted, (), f.values)
            for f in self.schema.fields
        )
        inferred = [
            dataclasses.replace(f, type=forced[f.name], reason="type choisi à l'import")
            if f.name in forced and forced[f.name] != f.type
            else f
            for f in self.fields
        ]
        return GenericSchema(Schema(fields), inferred, self.mappings)

    def resolve(self, column: str) -> str:
        """Retrouve un champ à partir de son nom technique ou de son en-tête d'origine."""
        names = self.schema.names
        if column in names:
            return column
        candidate = field_name(column)
        if candidate in names:
            return candidate
        raise MappingError(
            f"Colonne inconnue : {column!r}. Colonnes disponibles : {', '.join(names)}."
        )


def infer_generic_schema(tables: list[RawTable]) -> GenericSchema:
    if not tables:
        raise MappingError("Aucun fichier à analyser.")
    order: list[str] = []
    labels: dict[str, str] = {}
    values: dict[str, list[str | None]] = {}
    sources: dict[str, list[str]] = {}
    header_to_field: dict[str, dict[str, str]] = {}

    for table in tables:
        used: dict[str, str] = {}
        for header in table.headers:
            base = field_name(header)
            name, n = base, 2
            while name in used.values():  # deux colonnes d'un même fichier donnant le même nom
                name, n = f"{base}_{n}", n + 1
            used[header] = name
            if name not in labels:
                order.append(name)
                labels[name] = header
                values[name] = []
                sources[name] = []
            values[name].extend(table.column(header))
            sources[name].append(table.source_file)
        header_to_field[table.source_file] = used

    inferred: list[InferredField] = []
    for name in order:
        column = values[name]
        present = [v for v in column if v is not None and v.strip()]
        inference = infer_type(column)
        ftype: FieldType = "text"
        reason = "texte"
        if inference.dominant in (ValueKind.DATE,) and inference.conformity >= TYPE_CONFIDENCE:
            ftype, reason = "date", f"date ({inference.conformity:.0%} des valeurs)"
        elif (
            inference.dominant in (ValueKind.INTEGER, ValueKind.DECIMAL)
            and inference.conformity >= TYPE_CONFIDENCE
        ):
            if _looks_like_identifier(name, [v.strip() for v in present]):
                reason = "numérique, mais traité comme un identifiant (nom ou zéros en tête)"
            else:
                ftype, reason = "decimal", f"nombre ({inference.conformity:.0%} des valeurs)"
        elif not present:
            reason = "colonne vide"
        inferred.append(
            InferredField(
                name,
                labels[name],
                ftype,
                inference.conformity,
                len(present),
                tuple(sources[name]),
                reason,
            )
        )

    schema = Schema(tuple(FieldSpec(f.name, f.label, f.type, False, ()) for f in inferred))
    mappings = {
        source: ColumnMapping(
            source,
            tuple(Proposal(header, name, 1.0, Method.IDENTITY) for header, name in used.items()),
        )
        for source, used in header_to_field.items()
    }
    return GenericSchema(schema, inferred, mappings)


def suggest_mode(missing_required_by_file: dict[str, list[str]]) -> Mode:
    """Le mode bancaire n'est proposé que si chaque fichier couvre tous les champs obligatoires."""
    return (
        "banking"
        if all(not missing for missing in missing_required_by_file.values())
        else "generic"
    )
