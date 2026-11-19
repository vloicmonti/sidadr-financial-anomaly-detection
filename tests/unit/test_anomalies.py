import dataclasses
import math
import random
from pathlib import Path

import pandas as pd
import pytest

from sidadr.anomaly_detection import IQR, ROBUST_Z, detect_anomalies
from sidadr.anomaly_detection.statistical import fmt_amount
from sidadr.config import AnomalySettings, ConfigError, load_anomaly_settings
from sidadr.findings import Severity

SETTINGS = AnomalySettings()


def _row(
    n: int, amount: object, op: str = "DEPOSIT", currency: str = "XAF", import_id: str = "a"
) -> dict[str, object]:
    return {
        "record_id": f"{import_id}:{n}", "import_id": import_id, "source_file": f"{import_id}.csv",
        "source_row": n, "operation_type": op, "currency": currency, "amount": amount,
    }  # fmt: skip


def _lognormal_rows(
    n: int, median: float = 100_000, sigma: float = 0.8, seed: int = 1, **kw: str
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    values = [rng.lognormvariate(math.log(median), sigma) for _ in range(n)]
    values = [
        min(max(v, median * math.exp(-2.5 * sigma)), median * math.exp(2.5 * sigma)) for v in values
    ]
    return [_row(i, str(round(v)), **kw) for i, v in enumerate(values, start=1)]


def _detect(rows: list[dict[str, object]], settings: AnomalySettings = SETTINGS):  # type: ignore[no-untyped-def]
    return detect_anomalies(pd.DataFrame(rows, dtype=object), settings)


def test_clean_lognormal_amounts_raise_no_alert() -> None:
    assert _detect(_lognormal_rows(500)).anomalies == []


def test_a_huge_amount_is_flagged_with_its_explanation() -> None:
    rows = [*_lognormal_rows(500), _row(999, "250 000 000")]
    report = _detect(rows)
    (anomaly,) = report.anomalies
    assert anomaly.record_id == "a:999"
    assert anomaly.direction == "high"
    assert set(anomaly.methods) == {ROBUST_Z, IQR}
    assert anomaly.robust_z is not None and anomaly.robust_z > SETTINGS.robust_z_threshold
    assert anomaly.severity is Severity.ATYPICAL_VALUE
    text = anomaly.explanation(SETTINGS)
    assert "DEPOSIT · XAF" in text and "z robuste" in text and "médiane" in text


def test_a_missing_zeros_amount_is_flagged_as_low() -> None:
    rows = [*_lognormal_rows(500), _row(999, "12")]
    (anomaly,) = _detect(rows).anomalies
    assert anomaly.direction == "low"


def test_amounts_are_compared_within_their_group_only() -> None:
    # Un dépôt de 5 000 000 est banal parmi des virements, mais pas parmi des retraits.
    rows = [
        *_lognormal_rows(300, median=5_000_000, op="TRANSFER_IN"),
        *[
            _row(1000 + r["source_row"], r["amount"], op="WITHDRAWAL")
            for r in _lognormal_rows(300, median=50_000, seed=2)
        ],  # type: ignore[operator]
        _row(5000, "5000000", op="WITHDRAWAL"),
    ]
    report = _detect(rows)
    assert [a.record_id for a in report.anomalies] == ["a:5000"]
    assert report.anomalies[0].group == ("WITHDRAWAL", "XAF")


def test_small_groups_and_non_positive_values_are_excluded_and_reported() -> None:
    rows = [*_lognormal_rows(200), _row(900, "-5000"), _row(901, "N/A"), _row(902, "0")]
    rows += [_row(950 + i, "1000", currency="EUR") for i in range(5)]
    report = _detect(rows)
    assert report.excluded_values == 3
    assert report.skipped_groups == {("DEPOSIT", "EUR"): 5}
    assert any("non analysés" in note for note in report.notes)


def test_each_file_uses_its_own_decimal_separator() -> None:
    a = [_row(r["source_row"], r["amount"], import_id="a") for r in _lognormal_rows(200)]  # type: ignore[arg-type]
    b = [
        _row(r["source_row"], f"{r['amount']},50", import_id="b")
        for r in _lognormal_rows(200, seed=3)
    ]  # type: ignore[arg-type]
    assert _detect(a + b).anomalies == []


def test_methods_can_be_switched() -> None:
    rows = [*_lognormal_rows(500), _row(999, "250000000")]
    only_iqr = dataclasses.replace(SETTINGS, robust_z_enabled=False)
    (anomaly,) = _detect(rows, only_iqr).anomalies
    assert anomaly.methods == (IQR,)


def test_findings_never_claim_an_error_or_fraud() -> None:
    rows = [*_lognormal_rows(500), _row(999, "250000000")]
    report = _detect(rows)
    (finding,) = report.findings()
    assert finding.severity is Severity.ATYPICAL_VALUE
    assert "fraude" in finding.recommendation and "pas une erreur" in finding.recommendation
    assert finding.field == "amount" and finding.record_ids == ("a:999",)


def test_detection_never_modifies_the_data() -> None:
    frame = pd.DataFrame([*_lognormal_rows(100), _row(999, "250000000")], dtype=object)
    before = frame.copy()
    detect_anomalies(frame, SETTINGS)
    pd.testing.assert_frame_equal(frame, before)


def test_amount_formatting() -> None:
    assert fmt_amount(1234567.8) == "1 234 568"
    assert fmt_amount(3.456) == "3,46"


def test_default_configuration_loads() -> None:
    settings = load_anomaly_settings()
    assert settings.group_by == ("operation_type", "currency")
    assert settings.robust_z_threshold == 3.5 and settings.log_transform


@pytest.mark.parametrize(
    "content",
    [
        "fields: [customer_name]\n",
        "group_by: [inconnu]\n",
        "methods:\n  robust_zscore: {enabled: false}\n  iqr: {enabled: false}\n",
        "methods:\n  robust_zscore: {threshold: -1}\n",
    ],
)
def test_invalid_configuration_is_rejected(tmp_path: Path, content: str) -> None:
    (tmp_path / "anomalies.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_anomaly_settings(tmp_path)
