import pytest

from sidadr.config import Schema, default_schema
from sidadr.ingestion.errors import MappingError
from sidadr.ingestion.mapping import Method, normalize_header, propose_mapping

FULL_HEADERS = [
    "Ref_Operation", "Code_Banque", "Code_Agence", "Num_Client", "Nom_Client", "Num_Compte",
    "Type_Operation", "Date_Operation", "Montant", "Devise",
]  # fmt: skip


@pytest.fixture(scope="module")
def schema() -> Schema:
    return default_schema()


def test_normalize_header() -> None:
    assert normalize_header("Nom_Client") == normalize_header("nom client") == "nomclient"
    assert normalize_header("Date d'opération") == "datedoperation"


def test_synonyms_are_proposed_with_full_confidence(schema: Schema) -> None:
    mapping = propose_mapping("f.csv", FULL_HEADERS, schema)
    assert not mapping.confirmed
    assert mapping.assignments["Nom_Client"] == "customer_name"
    assert all(p.method is Method.SYNONYM and p.confidence == 1.0 for p in mapping.proposals)
    assert mapping.missing_required(schema) == []


def test_close_header_is_proposed_but_flagged_for_review(schema: Schema) -> None:
    mapping = propose_mapping("f.csv", ["Nom du client", "Colonne inconnue"], schema)
    close = mapping.proposals[0]
    assert close.target == "customer_name" and close.method is Method.SIMILARITY
    assert 0.8 <= close.confidence < 1.0
    assert close in mapping.to_review()
    assert mapping.assignments["Colonne inconnue"] is None


def test_two_columns_for_one_field_are_never_both_mapped(schema: Schema) -> None:
    mapping = propose_mapping("f.csv", ["Montant", "Amount"], schema)
    assert list(mapping.assignments.values()).count("amount") == 1
    loser = next(p for p in mapping.proposals if p.target is None)
    assert "conflit" in loser.note


def test_unconfirmed_mapping_cannot_be_confirmed_when_required_fields_are_missing(
    schema: Schema,
) -> None:
    mapping = propose_mapping("f.csv", ["Montant", "Devise"], schema)
    with pytest.raises(MappingError, match="obligatoires"):
        mapping.confirm(schema)


def test_user_overrides_are_applied_and_marked_manual(schema: Schema) -> None:
    headers = [*FULL_HEADERS, "Libellé"]
    mapping = propose_mapping("f.csv", headers, schema).confirm(
        schema, {"Libellé": None, "Code_Agence": "branch_code"}
    )
    assert mapping.confirmed
    assert next(p for p in mapping.proposals if p.source == "Code_Agence").method is Method.MANUAL


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"Inexistante": "amount"}, "absentes du fichier"),
        ({"Devise": "champ_inconnu"}, "inconnus du schéma"),
        ({"Devise": "amount"}, "même champ"),
    ],
)
def test_invalid_overrides_are_rejected(
    schema: Schema, overrides: dict[str, str | None], message: str
) -> None:
    with pytest.raises(MappingError, match=message):
        propose_mapping("f.csv", FULL_HEADERS, schema).confirm(schema, overrides)
