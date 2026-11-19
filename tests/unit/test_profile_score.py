import pandas as pd
import pytest

from sidadr.config import Dimension, Schema, default_schema
from sidadr.profiling import compute_score, profile_frame


@pytest.fixture(scope="module")
def schema() -> Schema:
    return default_schema()


def _row(schema: Schema, n: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "record_id": f"t:{n}", "import_id": "t", "source_file": "t.csv", "source_row": n,
        "transaction_id": f"TX-{n}", "bank_code": "BALP", "branch_code": "BALP-001",
        "branch_city": "Douala", "customer_id": f"C-{n}", "customer_name": f"CLIENT {n}",
        "birth_date": "1980-01-01", "phone": None, "account_number": f"A-{n}",
        "operation_type": "DEPOSIT", "transaction_date": "2025-04-01", "value_date": "2025-04-02",
        "amount": str(1000 + n), "currency": "XAF",
    }  # fmt: skip
    assert set(schema.names) <= set(row)
    row.update(overrides)
    return row


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, dtype=object)


def test_a_clean_dataset_scores_100(schema: Schema) -> None:
    profile = profile_frame(_frame([_row(schema, n) for n in range(10)]), schema)
    score = compute_score(profile)
    assert score.score == 100.0
    assert score["consistency"].evaluated is False
    assert any("renormalisés" in note for note in score.notes)


def test_each_defect_lowers_the_right_dimension(schema: Schema) -> None:
    rows = [_row(schema, n) for n in range(10)]
    rows[1]["customer_name"] = None  # complétude
    rows[2]["amount"] = "N/A"  # validité
    rows[3]["transaction_date"] = "2025-02-30"  # validité
    rows[4]["operation_type"] = "VIREMENT"  # validité (hors énumération)
    rows.append({**rows[5], "record_id": "t:99", "source_row": 99})  # doublon de ligne complète
    profile = profile_frame(_frame(rows), schema)
    score = compute_score(profile)

    assert profile["customer_name"].missing == 1
    assert profile["amount"].invalid_record_ids == ["t:2"]
    assert profile["transaction_date"].invalid_reasons == {"date impossible au calendrier": 1}
    assert profile["operation_type"].invalid == 1
    assert profile.duplicate_rows == 2 and profile.duplicate_groups == 1

    n_rows, n_required = 11, len(schema.required)
    assert score["completeness"].value == pytest.approx(1 - 1 / (n_rows * n_required))
    assert score["uniqueness"].value == pytest.approx(1 - 2 / n_rows)
    assert score["validity"].numerator == 3


def test_score_is_recomputable_by_hand_from_the_weights(schema: Schema) -> None:
    rows = [_row(schema, n) for n in range(4)]
    rows.append({**rows[0], "record_id": "t:9", "source_row": 9})
    profile = profile_frame(_frame(rows), schema)
    dims = (
        Dimension("completeness", "C", 1.0, ""),
        Dimension("uniqueness", "U", 3.0, ""),
    )
    score = compute_score(profile, dims)
    uniqueness = 1 - 2 / 5
    assert score.score == pytest.approx(round(100 * (0.25 * 1.0 + 0.75 * uniqueness), 1))


def test_dates_and_amounts_are_compared_after_normalization(schema: Schema) -> None:
    a = _row(schema, 1, import_id="a", record_id="a:1")
    b = _row(
        schema, 1, import_id="b", record_id="b:1", transaction_date="01/04/2025",
        value_date="02/04/2025", birth_date="01/01/1980", amount="1 001,00",
    )  # fmt: skip
    filler = [
        _row(schema, n, import_id="b", record_id=f"b:{n}", transaction_date="25/04/2025")
        for n in range(2, 5)
    ]
    profile = profile_frame(_frame([a, b, *filler]), schema)
    assert profile.duplicate_rows == 2


def test_numeric_statistics_ignore_invalid_values(schema: Schema) -> None:
    rows = [_row(schema, n, amount=str(v)) for n, v in enumerate([10, 20, 30, 40])]
    rows.append(_row(schema, 9, amount="N/A"))
    stats = profile_frame(_frame(rows), schema)["amount"].numeric
    assert stats is not None
    assert (stats.count, stats.minimum, stats.maximum, stats.mean) == (4, 10, 40, 25.0)
