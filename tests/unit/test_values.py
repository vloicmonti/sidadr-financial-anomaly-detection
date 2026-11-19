from datetime import date
from decimal import Decimal

import pytest

from sidadr.ingestion.values import (
    DateOrder,
    DateStatus,
    DecimalSeparator,
    ValueKind,
    classify,
    infer_date_order,
    infer_decimal_separator,
    infer_type,
    parse_date,
    parse_decimal,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1250000", Decimal("1250000")),
        ("1 250 000", Decimal("1250000")),
        ("1 250 000,50", Decimal("1250000.50")),
        ("1250000,50", Decimal("1250000.50")),
        ("1.250.000,50", Decimal("1250000.50")),
        ("1,250,000.50", Decimal("1250000.50")),
        ("-174720", Decimal("-174720")),
        ("+3", Decimal("3")),
        ("0.5", Decimal("0.5")),
        ("1.250", Decimal("1.250")),  # ambigu, point décimal par défaut
        ("1,250", Decimal("1250")),  # ambigu, virgule de milliers par défaut
    ],
)
def test_parse_decimal_accepts_common_formats(text: str, expected: Decimal) -> None:
    assert parse_decimal(text) == expected


@pytest.mark.parametrize(
    "text", ["N/A", "#VALEUR!", "12O 000", "-", "", "  ", "12,34,5", "1,2.3.4", "1.", None]
)
def test_parse_decimal_rejects_non_numbers(text: str | None) -> None:
    assert parse_decimal(text) is None


def test_ambiguous_number_follows_the_column_separator() -> None:
    assert parse_decimal("1,250", DecimalSeparator.COMMA) == Decimal("1.250")
    assert parse_decimal("1.250", DecimalSeparator.COMMA) == Decimal("1250")


def test_decimal_separator_is_inferred_from_unambiguous_values() -> None:
    separator, note = infer_decimal_separator(["150000", "12,50", "3,75", "1,250"])
    assert separator is DecimalSeparator.COMMA
    assert "2 valeurs" in note
    assert infer_decimal_separator(["150000", "200"])[0] is None


@pytest.mark.parametrize(
    ("text", "order", "status", "expected"),
    [
        ("2025-04-01", None, DateStatus.VALID, date(2025, 4, 1)),
        ("2025-04-01 00:00:00", None, DateStatus.VALID, date(2025, 4, 1)),
        ("01/04/2025", DateOrder.DMY, DateStatus.VALID, date(2025, 4, 1)),
        ("04/01/2025", DateOrder.MDY, DateStatus.VALID, date(2025, 4, 1)),
        ("30/02/2025", DateOrder.DMY, DateStatus.INVALID_CALENDAR, None),
        ("2025-13-05", None, DateStatus.INVALID_CALENDAR, None),
        ("hier", None, DateStatus.NOT_A_DATE, None),
    ],
)
def test_parse_date(
    text: str, order: DateOrder | None, status: DateStatus, expected: date | None
) -> None:
    assert parse_date(text, order) == (status, expected)


def test_date_order_is_decided_by_values_above_12() -> None:
    assert infer_date_order(["25/04/2025", "01/04/2025"]).order is DateOrder.DMY
    assert infer_date_order(["04/25/2025", "04/01/2025"]).order is DateOrder.MDY
    assert infer_date_order(["2025-04-01"]).order is DateOrder.ISO


def test_undecidable_date_order_is_flagged_not_hidden() -> None:
    decision = infer_date_order(["01/04/2025", "02/03/2025"])
    assert decision.ambiguous
    assert "vérifier" in decision.note


def test_a_few_contradictory_dates_do_not_flip_the_column_order() -> None:
    values = [f"{d}/04/2025" for d in range(13, 29)] * 10 + ["05/13/2025"]
    decision = infer_date_order(values)
    assert decision.order is DateOrder.DMY and not decision.ambiguous
    assert parse_date("05/13/2025", decision.order)[0] is DateStatus.INVALID_CALENDAR


def test_classify_and_infer_type() -> None:
    assert classify("150000") is ValueKind.INTEGER
    assert classify("12,50") is ValueKind.DECIMAL
    assert classify("2025-02-30") is ValueKind.INVALID_DATE
    assert classify("oui") is ValueKind.BOOLEAN
    assert classify("MANGA Jean") is ValueKind.TEXT
    inference = infer_type(["01/04/2025", "30/02/2025", "texte", None, " "])
    assert inference.dominant is ValueKind.DATE
    assert inference.non_empty == 3
    # La colonne a la forme d'une colonne de dates, mais seule une valeur est une date valide.
    assert inference.conformity == pytest.approx(1 / 3)
    assert infer_type([None, ""]).dominant is None
