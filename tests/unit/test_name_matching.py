from pathlib import Path

import pandas as pd
import pytest

from sidadr.config import ConfigError, MatchingSettings, load_matching_settings
from sidadr.findings import Severity
from sidadr.name_matching import (
    Category,
    Corroboration,
    MatchReport,
    Strategy,
    match_names,
    normalize_name,
)
from sidadr.name_matching.matcher import initials_compatible
from sidadr.name_matching.normalize import block_keys, tokens

SETTINGS = MatchingSettings()


def _customer(
    n: int,
    name: str,
    birth: str | None = "1984-03-12",
    phone: str | None = None,
    bank: str = "BALP",
) -> dict[str, object]:
    return {
        "record_id": f"a:{n}", "import_id": "a", "source_file": "a.csv", "source_row": n,
        "transaction_id": f"TX-{n}", "bank_code": bank, "branch_code": f"{bank}-001",
        "branch_city": "Douala", "customer_id": f"C-{n}", "customer_name": name, "birth_date": birth,
        "phone": phone, "account_number": f"A-{n}", "operation_type": "DEPOSIT",
        "transaction_date": "2025-04-01", "value_date": "2025-04-01", "amount": "1000", "currency": "XAF",
    }  # fmt: skip


def _match(*customers: dict[str, object]) -> MatchReport:
    return match_names(pd.DataFrame(list(customers), dtype=object), SETTINGS)


def _only(report: MatchReport):  # type: ignore[no-untyped-def]
    assert len(report.matches) == 1, [m.explanation() for m in report.matches]
    return report.matches[0]


# ---------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jean-Pierre  MANGA ", "JEAN PIERRE MANGA"),
        ("J. P. MANGA", "J P MANGA"),
        ("Hélène KAMGA", "HELENE KAMGA"),
        ("N'GUEMA  Raïssa", "N GUEMA RAISSA"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_block_keys_use_word_starts_and_ends() -> None:
    keys = block_keys(tokens("MANGA JEAN"))
    assert {"P:MAN", "S:NGA", "P:JEA", "S:EAN"} == keys
    assert block_keys(tokens("MRNGA JEAN")) & block_keys(tokens("MANGA JEAN"))


# ---------------------------------------------------------------- exemples du brief


@pytest.mark.parametrize(
    "variant",
    ["Jean-Pierre Manga", "JEANPIERRE MANGA", "J. P. MANGA", "MANGA JEAN PIERRE"],
)
def test_brief_variants_are_matched_when_birth_dates_agree(variant: str) -> None:
    match = _only(_match(_customer(1, "JEAN PIERRE MANGA"), _customer(2, variant)))
    assert match.category is Category.HIGH
    assert match.corroboration is Corroboration.SUPPORTED


@pytest.mark.parametrize(
    ("variant", "strategy"),
    [
        ("Jean-Pierre Manga", Strategy.EXACT),
        ("MANGA JEAN PIERRE", Strategy.TOKEN_SORT),
        ("JEANPIERRE MANGA", Strategy.COLLAPSED),
        ("J. P. MANGA", Strategy.INITIALS),
    ],
)
def test_the_winning_strategy_is_recorded(variant: str, strategy: Strategy) -> None:
    match = _only(_match(_customer(1, "JEAN PIERRE MANGA"), _customer(2, variant)))
    assert match.strategy is strategy


def test_a_typo_in_the_last_name_is_found() -> None:
    match = _only(_match(_customer(1, "MANGA Cédric"), _customer(2, "MRNGA CEDRIC")))
    assert match.name_score >= SETTINGS.intermediate


# ---------------------------------------------------------------- faux positifs


def test_homonyms_with_different_birth_dates_are_never_high() -> None:
    a, b = _customer(1, "FOUDA Paul"), _customer(2, "FOUDA Paul", birth="1990-01-01")
    assert _match(a, b).matches == []  # 100 - 25 = 75, sous le seuil « faible »
    with_phone = _match({**a, "phone": "611111111"}, {**b, "phone": "611111111"})
    match = _only(with_phone)  # 100 - 25 + 5 = 80 : visible, mais contredit
    assert match.corroboration is Corroboration.CONTRADICTED
    assert match.category is Category.LOW


def test_name_alone_is_capped_at_intermediate() -> None:
    match = _only(
        _match(_customer(1, "FOUDA Paul", birth=None), _customer(2, "FOUDA Paul", birth=None))
    )
    assert match.corroboration is Corroboration.UNVERIFIABLE
    assert match.category is Category.INTERMEDIATE


def test_an_extra_first_name_is_not_treated_as_the_same_person() -> None:
    report = _match(
        _customer(1, "MANGA Jean", birth=None), _customer(2, "MANGA Jean Pierre", birth=None)
    )
    assert all(m.category is not Category.HIGH for m in report.matches)


def test_close_first_names_stay_below_intermediate() -> None:
    report = _match(_customer(1, "ABENA Jean", birth=None), _customer(2, "ABENA Joel", birth=None))
    assert all(m.final_score < SETTINGS.intermediate for m in report.matches)
    assert all(m.scores.get(Strategy.JARO_WINKLER, 0) < SETTINGS.low for m in report.matches)


def test_unrelated_names_are_not_even_compared() -> None:
    report = _match(_customer(1, "ONANA Paul"), _customer(2, "TCHOUA Esther"))
    assert report.candidate_pairs == set()


# ---------------------------------------------------------------- corroboration


def test_phone_numbers_are_compared_on_their_last_digits() -> None:
    a = _customer(1, "NANA Esther", birth=None, phone="+999 628 19 59 95")
    b = _customer(2, "Esther NANA", birth=None, phone="00999628195995", bank="BBET")
    match = _only(_match(a, b))
    assert [adj.field for adj in match.adjustments] == ["phone"]
    assert match.adjustments[0].points == SETTINGS.phone_match
    assert not match.same_bank


def test_initials_compatibility_rules() -> None:
    assert initials_compatible(("J", "P", "MANGA"), ("MANGA", "PAUL", "JEAN"))
    assert not initials_compatible(("J", "MANGA"), ("MANGA", "PIERRE"))
    assert not initials_compatible(("J", "P"), ("JEAN", "PIERRE"))
    assert not initials_compatible(("J", "MANGA"), ("JEAN", "PIERRE", "MANGA"))


# ---------------------------------------------------------------- sorties


def test_matches_become_review_alerts_with_a_warning() -> None:
    match = _only(_match(_customer(1, "KAMGA Hélène"), _customer(2, "KAMGA Helene")))
    finding = match.to_finding()
    assert finding.severity is Severity.REVIEW_ALERT
    assert finding.record_ids == ("a:1", "a:2")
    assert "ne prouve pas une identité" in finding.recommendation
    assert "score final" in match.explanation()


def test_matching_never_modifies_the_data() -> None:
    frame = pd.DataFrame([_customer(1, "KAMGA Hélène"), _customer(2, "KAMGA Helene")], dtype=object)
    before = frame.copy()
    match_names(frame, SETTINGS)
    pd.testing.assert_frame_equal(frame, before)


def test_default_configuration_loads() -> None:
    settings = load_matching_settings()
    assert (settings.high, settings.intermediate, settings.low) == (92, 88, 80)


@pytest.mark.parametrize(
    "content",
    [
        "thresholds:\n  high: 80\n  intermediate: 90\n  low: 70\n",
        "corroboration:\n  birth_date_mismatch: 10\n",
        "blocking:\n  max_block_size: 1\n",
    ],
)
def test_invalid_configuration_is_rejected(tmp_path: Path, content: str) -> None:
    (tmp_path / "matching.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_matching_settings(tmp_path)
