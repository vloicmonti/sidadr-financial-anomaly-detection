from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

import pytest

from sidadr.synthetic.generator import (
    DEFAULT_DEFECT_RATES,
    DefectType,
    GeneratedDataset,
    GeneratorConfig,
    InvalidDate,
    OperationType,
    generate,
    upper_clip_xaf,
)
from sidadr.synthetic.names import Variant

DUPLICATE_TYPES = {
    DefectType.EXACT_DUPLICATE_ROW,
    DefectType.DUPLICATE_KEY,
    DefectType.CROSS_FILE_DUPLICATE,
}


def _rows_by_uid(ds: GeneratedDataset) -> dict[int, tuple[str, dict[str, object]]]:
    return {r.uid: (bank, r.values) for bank, rows in ds.rows_by_bank.items() for r in rows}


# ---------------------------------------------------------------- configuration


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rows": 100},
        {"period_start": date(2025, 7, 1), "period_end": date(2025, 6, 1)},
        {"reference_date": date(2025, 1, 1)},
        {"defect_rates": {DefectType.INVALID_DATE: 0.5}},
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GeneratorConfig(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------- reproductibilité


def test_same_seed_gives_identical_dataset(small_config: GeneratorConfig) -> None:
    a, b = generate(small_config), generate(small_config)
    assert a.rows_by_bank == b.rows_by_bank
    assert a.defects == b.defects
    assert a.customers == b.customers


def test_different_seed_gives_different_dataset(small_dataset: GeneratedDataset) -> None:
    other = generate(GeneratorConfig(rows=small_dataset.config.rows, seed=8))
    assert other.rows_by_bank != small_dataset.rows_by_bank


# ---------------------------------------------------------------- volumes et couverture


def test_total_rows_equals_base_rows_plus_duplicates(small_dataset: GeneratedDataset) -> None:
    n_dupes = sum(1 for d in small_dataset.defects if d.defect_type in DUPLICATE_TYPES)
    assert small_dataset.total_rows == small_dataset.config.rows + n_dupes


def test_every_defect_type_is_injected_with_expected_count(small_dataset: GeneratedDataset) -> None:
    counts = Counter(d.defect_type for d in small_dataset.defects)
    for defect_type, rate in DEFAULT_DEFECT_RATES.items():
        expected = max(1, round(rate * small_dataset.config.rows))
        assert counts[defect_type] == expected, defect_type


def test_single_row_defects_never_share_a_row(small_dataset: GeneratedDataset) -> None:
    uids = [d.uid for d in small_dataset.defects if d.defect_type not in DUPLICATE_TYPES]
    sources = [u for d in small_dataset.defects for u in d.related_uids]
    assert len(uids) == len(set(uids))
    assert not set(uids) & set(sources)


def test_every_customer_has_at_least_one_transaction(small_dataset: GeneratedDataset) -> None:
    seen = {
        v["customer_id"]
        for rows in small_dataset.rows_by_bank.values()
        for r in rows
        for v in [r.values]
    }
    assert {c.customer_id for c in small_dataset.customers} <= seen


# ---------------------------------------------------------------- défauts sur une ligne


def test_injected_values_are_present_in_rows(small_dataset: GeneratedDataset) -> None:
    rows = _rows_by_uid(small_dataset)
    for d in small_dataset.defects:
        if d.defect_type in DUPLICATE_TYPES or d.field is None:
            continue
        assert rows[d.uid][1][d.field] == d.injected, d


def test_defect_semantics(small_dataset: GeneratedDataset) -> None:
    rows = _rows_by_uid(small_dataset)
    cfg = small_dataset.config
    for d in small_dataset.defects:
        values = rows[d.uid][1]
        match d.defect_type:
            case DefectType.NEGATIVE_AMOUNT:
                assert values["operation_type"] == "DEPOSIT"
                assert isinstance(d.injected, Decimal) and d.injected < 0
            case DefectType.FEE_ABOVE_CAP:
                assert values["operation_type"] == "FEE"
                assert isinstance(d.injected, Decimal) and d.injected > cfg.fee_cap_xaf
            case DefectType.ATYPICAL_AMOUNT:
                op = OperationType(str(values["operation_type"]))
                assert isinstance(d.injected, Decimal)
                assert d.injected > Decimal(upper_clip_xaf(op))
            case DefectType.INVALID_DATE:
                assert isinstance(d.injected, InvalidDate)
            case DefectType.FUTURE_DATE:
                assert isinstance(d.injected, date) and d.injected > cfg.reference_date
            case DefectType.VALUE_DATE_BEFORE_TRANSACTION:
                tx_date = values["transaction_date"]
                assert isinstance(tx_date, date) and isinstance(d.injected, date)
                assert d.injected < tx_date
            case DefectType.UNKNOWN_BRANCH:
                assert d.injected not in {b.code for b in small_dataset.branches}
            case DefectType.UNKNOWN_CURRENCY:
                assert d.injected not in {"XAF", "EUR", "USD"}
            case DefectType.MISSING_MANDATORY:
                assert d.injected is None and d.original is not None
            case _:
                pass


def test_clean_amounts_stay_below_the_clip(small_dataset: GeneratedDataset) -> None:
    flagged = {d.uid for d in small_dataset.defects if d.field == "amount"}
    flagged |= {d.uid for d in small_dataset.defects if d.defect_type in DUPLICATE_TYPES}
    for uid, (_bank, values) in _rows_by_uid(small_dataset).items():
        if uid in flagged or values["currency"] != "XAF":
            continue
        amount = values["amount"]
        assert isinstance(amount, Decimal)
        assert amount <= Decimal(upper_clip_xaf(OperationType(str(values["operation_type"])))) + 1


# ---------------------------------------------------------------- doublons


def test_duplicates_relate_to_their_source(small_dataset: GeneratedDataset) -> None:
    rows = _rows_by_uid(small_dataset)
    for d in small_dataset.defects:
        if d.defect_type not in DUPLICATE_TYPES:
            continue
        (source_uid,) = d.related_uids
        copy_bank, copy = rows[d.uid]
        source_bank, source = rows[source_uid]
        assert copy["transaction_id"] == source["transaction_id"]
        if d.defect_type is DefectType.EXACT_DUPLICATE_ROW:
            assert copy == source and copy_bank == source_bank
        elif d.defect_type is DefectType.DUPLICATE_KEY:
            assert copy["amount"] != source["amount"] and copy_bank == source_bank
            assert {k: v for k, v in copy.items() if k != "amount"} == {
                k: v for k, v in source.items() if k != "amount"
            }
        else:
            assert copy == source and copy_bank != source_bank


# ---------------------------------------------------------------- identités (vérité terrain)


def test_homonyms_are_distinct_people_with_the_same_name(small_dataset: GeneratedDataset) -> None:
    persons = {p.person_id: p for p in small_dataset.persons}
    homonyms = [c for c in small_dataset.customers if c.homonym_of]
    assert len(homonyms) == small_dataset.config.n_homonym_pairs
    for c in homonyms:
        twin, source = persons[c.person_id], persons[c.homonym_of]  # type: ignore[index]
        assert twin.person_id != source.person_id
        assert twin.name == source.name
        assert twin.birth_date != source.birth_date


def test_multi_bank_and_intra_bank_duplicates_exist(small_dataset: GeneratedDataset) -> None:
    banks_per_person: dict[str, list[str]] = defaultdict(list)
    for c in small_dataset.customers:
        banks_per_person[c.person_id].append(c.bank_code)
    multi = [p for p, banks in banks_per_person.items() if len(set(banks)) > 1]
    intra = [p for p, banks in banks_per_person.items() if len(banks) > len(set(banks))]
    n_persons = len(small_dataset.persons) - small_dataset.config.n_homonym_pairs
    assert len(multi) == round(n_persons * small_dataset.config.multi_bank_rate)
    assert len(intra) == round(n_persons * small_dataset.config.intra_bank_duplicate_rate)


def test_intra_bank_duplicates_always_carry_a_name_variant(small_dataset: GeneratedDataset) -> None:
    seen: dict[tuple[str, str], int] = Counter(
        (c.person_id, c.bank_code) for c in small_dataset.customers
    )
    for c in small_dataset.customers:
        if seen[(c.person_id, c.bank_code)] > 1 and c.name_variant is not Variant.NONE:
            break
    else:
        pytest.fail("aucun doublon intra-banque avec variante de nom")
