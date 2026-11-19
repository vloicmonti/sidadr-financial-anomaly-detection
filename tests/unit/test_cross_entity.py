from pathlib import Path

import pandas as pd
import pytest

from sidadr.config import (
    ConfigError,
    CrossEntitySettings,
    MatchingSettings,
    load_cross_entity_settings,
)
from sidadr.cross_entity import EntityReport, EntityScope, FieldAgreement, group_entities
from sidadr.findings import Severity
from sidadr.name_matching import Category, match_names


def _customer(
    n: int,
    name: str,
    bank: str,
    branch: str | None = None,
    birth: str | None = "1984-03-12",
    phone: str | None = None,
) -> dict[str, object]:
    return {
        "record_id": f"a:{n}", "import_id": "a", "source_file": "a.csv", "source_row": n,
        "transaction_id": f"TX-{n}", "bank_code": bank, "branch_code": branch or f"{bank}-001",
        "branch_city": "Douala", "customer_id": f"C-{n}", "customer_name": name, "birth_date": birth,
        "phone": phone, "account_number": f"A-{n}", "operation_type": "DEPOSIT",
        "transaction_date": "2025-04-01", "value_date": "2025-04-01", "amount": "1000", "currency": "XAF",
    }  # fmt: skip


def _groups(
    *customers: dict[str, object], settings: CrossEntitySettings | None = None
) -> EntityReport:
    matches = match_names(pd.DataFrame(list(customers), dtype=object), MatchingSettings())
    return group_entities(matches, settings or CrossEntitySettings())


def test_same_person_in_two_banks_forms_a_multi_bank_group() -> None:
    report = _groups(_customer(1, "MANGA Jean", "BALP"), _customer(2, "Jean MANGA", "BBET"))
    (group,) = report.groups
    assert group.scope is EntityScope.MULTI_BANK
    assert group.banks == ("BALP", "BBET")
    assert group.confidence is Category.HIGH
    assert group.direct_link_ratio == 1.0
    assert {f.field: f.agreement for f in group.fields} == {
        "birth_date": FieldAgreement.CONCORDANT,
        "phone": FieldAgreement.UNKNOWN,
    }


def test_two_branches_of_one_bank_form_a_multi_branch_group() -> None:
    a = _customer(1, "MANGA Jean", "BALP", "BALP-001")
    b = _customer(2, "MANGA JEAN", "BALP", "BALP-002")
    (group,) = _groups(a, b).groups
    assert group.scope is EntityScope.MULTI_BRANCH
    assert _groups(a, b, settings=CrossEntitySettings(include_multi_branch=False)).groups == []


def test_duplicates_in_the_same_branch_are_left_to_the_name_module() -> None:
    assert (
        _groups(_customer(1, "MANGA Jean", "BALP"), _customer(2, "MANGA JEAN", "BALP")).groups == []
    )


def test_corroborated_links_are_grouped_transitively() -> None:
    report = _groups(
        _customer(1, "MANGA Jean", "BALP"),
        _customer(2, "Jean MANGA", "BBET"),
        _customer(3, "MANGA JEAN", "BGAM"),
    )
    (group,) = report.groups
    assert len(group.members) == 3
    assert group.banks == ("BALP", "BBET", "BGAM")


def test_uncorroborated_links_are_never_chained() -> None:
    report = _groups(
        _customer(1, "FOUDA Paul", "BALP", birth=None),
        _customer(2, "FOUDA Paul", "BBET", birth=None),
        _customer(3, "FOUDA Paul", "BGAM", birth=None),
    )
    assert len(report.groups) == 3
    assert all(len(g.members) == 2 and g.confidence is Category.INTERMEDIATE for g in report.groups)
    only_high = _groups(
        _customer(1, "FOUDA Paul", "BALP", birth=None),
        _customer(2, "FOUDA Paul", "BBET", birth=None),
        settings=CrossEntitySettings(min_category="high"),
    )
    assert only_high.groups == []


def test_diverging_fields_are_shown() -> None:
    a = _customer(1, "FOUDA Paul", "BALP", phone="611111111")
    b = _customer(2, "FOUDA Paul", "BBET", birth="1990-01-01", phone="611111111")
    (group,) = _groups(a, b, settings=CrossEntitySettings(min_category="low")).groups
    birth = next(f for f in group.fields if f.field == "birth_date")
    assert birth.agreement is FieldAgreement.DISCORDANT
    assert "birth_date" in group.explanation()


def test_groups_become_review_alerts() -> None:
    (group,) = _groups(
        _customer(1, "MANGA Jean", "BALP"), _customer(2, "Jean MANGA", "BBET")
    ).groups
    finding = group.to_finding()
    assert finding.severity is Severity.REVIEW_ALERT
    assert finding.kind == "multi_bank"
    assert finding.record_ids == ("a:1", "a:2")
    assert "ne prouve pas une identité" in finding.recommendation


def test_default_configuration_loads() -> None:
    assert load_cross_entity_settings() == CrossEntitySettings("intermediate", True)


def test_invalid_configuration_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "cross_entity.yaml").write_text("min_category: certain\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_cross_entity_settings(tmp_path)
