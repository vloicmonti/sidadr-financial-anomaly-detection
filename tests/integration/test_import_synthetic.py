"""Import et profilage du jeu de référence, mesurés contre la vérité terrain."""

import csv
from collections import Counter
from pathlib import Path

import pytest

from sidadr.anomaly_detection import detect_anomalies
from sidadr.business_rules import RuleReport, evaluate_rules, load_rules
from sidadr.cli import main
from sidadr.config import (
    CrossEntitySettings,
    load_anomaly_settings,
    load_duplicate_settings,
    load_matching_settings,
)
from sidadr.cross_entity import group_entities
from sidadr.deduplication import DuplicateKind, DuplicateReport, detect_duplicates
from sidadr.evaluation.anomalies import evaluate_anomalies, load_defects
from sidadr.evaluation.entities import evaluate_entities
from sidadr.evaluation.names import (
    NameEvaluation,
    evaluate_names,
    load_customer_truth,
    load_homonyms,
)
from sidadr.ingestion import ImportSession
from sidadr.ingestion.importer import _slug
from sidadr.name_matching import MatchReport, match_names
from sidadr.profiling import DatasetProfile, compute_score, profile_frame

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
FILES = ("alpha_T2.csv", "beta_T2.xlsx", "gamma_T2.csv")

pytestmark = pytest.mark.skipif(
    not (DATA / "metadata.json").exists(), reason="jeu de référence absent"
)


@pytest.fixture(scope="module")
def session() -> ImportSession:
    session = ImportSession()
    for name in FILES:
        inspection = session.inspect(DATA / name)
        session.add(inspection, inspection.mapping.confirm(session.schema))
    return session


@pytest.fixture(scope="module")
def profile(session: ImportSession) -> DatasetProfile:
    return profile_frame(session.frame, session.schema, session.settings)


@pytest.fixture(scope="module")
def manifest() -> list[dict[str, str]]:
    with (DATA / "_truth" / "defects_manifest.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _record_id(entry: dict[str, str]) -> str:
    return f"{_slug(entry['file'])}:{entry['row']}"


def test_every_file_is_mapped_automatically_and_completely(session: ImportSession) -> None:
    for table in session.tables:
        assert table.mapping.missing_required(session.schema) == []
        assert table.report.rows_rejected == []
    gamma = next(t for t in session.tables if t.import_id == "gamma_t2")
    assert gamma.report.unmapped_columns == ["commentaire"]
    assert gamma.report.empty_columns == ["commentaire"]


def test_rows_and_record_ids_match_the_files(
    session: ImportSession, manifest: list[dict[str, str]]
) -> None:
    frame = session.frame
    assert len(frame) == sum(t.report.rows_imported for t in session.tables)
    assert frame["record_id"].is_unique
    by_id = frame.set_index("record_id")["transaction_id"]
    for entry in manifest:
        assert by_id[_record_id(entry)] == entry["transaction_id"]


def test_missing_mandatory_values_are_all_found(
    profile: DatasetProfile, manifest: list[dict[str, str]]
) -> None:
    expected = Counter(e["field"] for e in manifest if e["defect_type"] == "missing_mandatory")
    for field_name, count in expected.items():
        assert profile[field_name].missing == count, field_name


@pytest.mark.parametrize(
    ("defect_type", "field_name"),
    [("invalid_date", "transaction_date"), ("amount_not_numeric", "amount")],
)
def test_invalid_values_match_the_manifest_exactly(
    profile: DatasetProfile, manifest: list[dict[str, str]], defect_type: str, field_name: str
) -> None:
    expected = {_record_id(e) for e in manifest if e["defect_type"] == defect_type}
    assert set(profile[field_name].invalid_record_ids) == expected  # rappel et précision = 100 %


def test_exact_duplicate_rows_are_counted(
    profile: DatasetProfile, manifest: list[dict[str, str]]
) -> None:
    exact = [e for e in manifest if e["defect_type"] == "exact_duplicate_row"]
    # Chaque copie forme un groupe avec sa source ; les doublons inter-fichiers dont le
    # téléphone est vide sont aussi des lignes complètes identiques après normalisation.
    assert profile.duplicate_groups >= len(exact)
    assert profile.duplicate_rows >= 2 * len(exact)


def test_cli_requires_explicit_confirmation(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["profile", *files]) == 2
    out = tmp_path / "profile.json"
    assert main(["profile", *files, "--accept-proposed", "--json", str(out)]) == 0
    assert out.exists() and '"score"' in out.read_text(encoding="utf-8")


def test_cli_applies_a_mapping_file(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("gamma_T2.csv:\n  ville: null\n", encoding="utf-8")
    assert main(["profile", str(DATA / "gamma_T2.csv"), "--mapping", str(mapping)]) == 0
    mapping.write_text("gamma_T2.csv:\n  montant: null\n", encoding="utf-8")
    assert main(["profile", str(DATA / "gamma_T2.csv"), "--mapping", str(mapping)]) == 1


# ---------------------------------------------------------------- doublons (Phase 4)


@pytest.fixture(scope="module")
def duplicates(session: ImportSession) -> DuplicateReport:
    return detect_duplicates(
        session.frame, load_duplicate_settings(schema=session.schema), session.schema
    )


def _expected_groups(manifest: list[dict[str, str]], defect_type: str) -> set[frozenset[str]]:
    groups = set()
    for e in manifest:
        if e["defect_type"] != defect_type:
            continue
        related = [
            _record_id({"file": r.split(":")[0], "row": r.split(":")[1]})
            for r in e["related_rows"].split(";")
        ]
        groups.add(frozenset([_record_id(e), *related]))
    return groups


@pytest.mark.parametrize(
    ("defect_type", "kind"),
    [
        ("exact_duplicate_row", DuplicateKind.EXACT_ROW),
        ("duplicate_key", DuplicateKind.KEY),
        ("cross_file_duplicate", DuplicateKind.CROSS_FILE),
    ],
)
def test_duplicates_match_the_manifest_exactly(
    duplicates: DuplicateReport,
    manifest: list[dict[str, str]],
    defect_type: str,
    kind: DuplicateKind,
) -> None:
    expected = _expected_groups(manifest, defect_type)
    found = {frozenset(g.record_ids) for g in duplicates.by_kind(kind)}
    assert found == expected  # rappel et précision = 100 %


def test_duplicate_key_groups_differ_only_on_the_injected_field(
    duplicates: DuplicateReport,
) -> None:
    for group in duplicates.by_kind(DuplicateKind.KEY):
        assert [d.field for d in group.differences] == ["amount"]


def test_no_spurious_combination_alert_on_the_reference_set(duplicates: DuplicateReport) -> None:
    assert duplicates.by_kind(DuplicateKind.COMBINATION) == []


def test_cli_duplicates_command(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["duplicates", *files]) == 2
    out = tmp_path / "dups.csv"
    assert main(["duplicates", *files, "--accept-proposed", "--csv", str(out), "--limit", "0"]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("group_id;kind;severity")
    assert len(lines) == 1 + 2 * 200  # 200 groupes de 2 enregistrements


# ---------------------------------------------------------------- noms similaires (Phase 5)


@pytest.fixture(scope="module")
def name_evaluation(session: ImportSession) -> NameEvaluation:
    report = match_names(session.frame, load_matching_settings(), session.schema)
    truth_file = DATA / "_truth" / "customers.csv"
    return evaluate_names(report, load_customer_truth(truth_file), load_homonyms(truth_file))


def test_blocking_never_loses_a_true_pair(name_evaluation: NameEvaluation) -> None:
    assert name_evaluation.true_pairs > 100
    assert name_evaluation.blocking_recall == 1.0


def test_high_similarity_is_precise(name_evaluation: NameEvaluation) -> None:
    high = name_evaluation["high"]
    assert high.precision == 1.0
    assert high.recall >= 0.85
    assert name_evaluation.homonyms_flagged_high == 0


def test_every_true_pair_is_at_least_intermediate(name_evaluation: NameEvaluation) -> None:
    assert name_evaluation[">= intermediate"].recall == 1.0


def test_cli_names_command(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["names", *files]) == 2
    out = tmp_path / "names.csv"
    assert (
        main(
            [
                "names",
                *files,
                "--accept-proposed",
                "--category",
                "high",
                "--csv",
                str(out),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("match_id;category;final_score")
    assert all(";high;" in line for line in lines[1:])


# ---------------------------------------------------------------- multi-banques (Phase 6)


@pytest.fixture(scope="module")
def name_report(session: ImportSession) -> MatchReport:
    return match_names(session.frame, load_matching_settings(), session.schema)


@pytest.mark.parametrize(
    ("min_category", "min_recall", "min_purity"), [("high", 0.85, 1.0), ("intermediate", 1.0, 0.6)]
)
def test_multi_bank_customers_are_found(
    name_report: MatchReport, min_category: str, min_recall: float, min_purity: float
) -> None:
    report = group_entities(name_report, CrossEntitySettings(min_category, True))
    truth = load_customer_truth(DATA / "_truth" / "customers.csv")
    evaluation = evaluate_entities(report, name_report.profiles, truth)
    assert evaluation.true_multi_bank_persons == 100
    assert evaluation.recall >= min_recall
    assert evaluation.purity >= min_purity
    assert max(len(g.members) for g in report.groups) <= 3  # pas d'enchaînement d'homonymes


def test_cli_entities_command(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["entities", *files]) == 2
    out = tmp_path / "entities.csv"
    assert (
        main(
            [
                "entities",
                *files,
                "--accept-proposed",
                "--scope",
                "multi_bank",
                "--csv",
                str(out),
                "--limit",
                "0",
            ]
        )
        == 0
    )
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("group_id;scope;confidence")
    assert all(";multi_bank;" in line for line in lines[1:])


# ---------------------------------------------------------------- anomalies (Phase 7)


def test_atypical_amounts_are_found(session: ImportSession) -> None:
    report = detect_anomalies(
        session.frame, load_anomaly_settings(schema=session.schema), session.schema
    )
    evaluation = evaluate_anomalies(report, load_defects(DATA / "_truth" / "defects_manifest.csv"))
    assert evaluation.injected_atypical == 80
    assert evaluation.recall >= 0.95
    assert (
        evaluation.explained_by_other_defects == 20
    )  # frais au-dessus du plafond, réellement extrêmes
    assert evaluation.precision >= 0.75
    assert all(a.severity.value == "atypical_value" for a in report.anomalies)


def test_cli_anomalies_command(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["anomalies", *files]) == 2
    out = tmp_path / "anomalies.csv"
    assert main(["anomalies", *files, "--accept-proposed", "--csv", str(out), "--limit", "0"]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("anomaly_id;record_id;field")
    assert len(lines) > 100


# ---------------------------------------------------------------- règles métier (Phase 8)

RULE_DEFECTS = {
    "R-MANDATORY": "missing_mandatory",
    "R-CURRENCY-ISO": "unknown_currency",
    "R-DATE-FUTURE": "future_date",
    "R-AMOUNT-NEG-DEPOSIT": "negative_amount",
    "R-FEE-CAP": "fee_above_cap",
    "R-VALUE-DATE": "value_date_before_transaction",
    "R-BRANCH-REF": "unknown_branch",
}


@pytest.fixture(scope="module")
def rule_report(session: ImportSession) -> RuleReport:
    return evaluate_rules(session.frame, load_rules(schema=session.schema), session.schema)


@pytest.mark.parametrize(("rule_id", "defect_type"), sorted(RULE_DEFECTS.items()))
def test_each_rule_finds_exactly_its_injected_defects(
    rule_report: RuleReport, rule_id: str, defect_type: str
) -> None:
    defects = load_defects(DATA / "_truth" / "defects_manifest.csv")
    assert {v.record_id for v in rule_report.for_rule(rule_id)} == defects[defect_type]


def test_consistency_dimension_is_now_evaluated(
    session: ImportSession, rule_report: RuleReport
) -> None:
    profile = profile_frame(session.frame, session.schema, session.settings)
    score = compute_score(profile, rule_violation_rows=rule_report.rows_violating("consistency"))
    assert score["consistency"].evaluated
    assert score["consistency"].numerator == rule_report.rows_violating("consistency") == 190
    assert all(d.evaluated for d in score.dimensions)


def test_cli_rules_command(tmp_path: Path) -> None:
    files = [str(DATA / name) for name in FILES]
    assert main(["rules", *files]) == 2
    out = tmp_path / "rules.csv"
    assert main(["rules", *files, "--accept-proposed", "--csv", str(out), "--limit", "0"]) == 0
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("violation_id;rule_id;severity")
    assert len(lines) == 1 + 430
    later = tmp_path / "later.csv"
    assert (
        main(
            [
                "rules",
                *files,
                "--accept-proposed",
                "--reference-date",
                "2027-01-01",
                "--csv",
                str(later),
                "--limit",
                "0",
            ]
        )
        == 0
    )
    assert not any(
        ";R-DATE-FUTURE;" in line for line in later.read_text(encoding="utf-8-sig").splitlines()
    )
