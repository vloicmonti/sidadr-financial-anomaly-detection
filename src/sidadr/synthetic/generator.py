"""Construction d'un jeu de transactions bancaires fictives avec défauts injectés.

Le générateur est entièrement déterministe : une même configuration (dont la graine)
produit exactement les mêmes données. Chaque défaut injecté est consigné dans une
liste de `Defect`, qui sert de vérité terrain pour mesurer les détecteurs de SIDADR.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from sidadr.synthetic.names import (
    NameStyle,
    PersonName,
    Variant,
    applicable_variants,
    format_name,
    random_name,
    render_variant,
)

GENERATOR_VERSION = "1.0"


# --------------------------------------------------------------------------- types de base


@dataclass(frozen=True)
class InvalidDate:
    """Date calendaire impossible (ex. 30 février), rendue telle quelle par les writers."""

    year: int
    month: int
    day: int

    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


CellValue = str | Decimal | date | InvalidDate | None


class OperationType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    FEE = "FEE"


class DefectType(StrEnum):
    EXACT_DUPLICATE_ROW = "exact_duplicate_row"
    DUPLICATE_KEY = "duplicate_key"
    CROSS_FILE_DUPLICATE = "cross_file_duplicate"
    MISSING_MANDATORY = "missing_mandatory"
    INVALID_DATE = "invalid_date"
    FUTURE_DATE = "future_date"
    UNKNOWN_CURRENCY = "unknown_currency"
    NEGATIVE_AMOUNT = "negative_amount"
    FEE_ABOVE_CAP = "fee_above_cap"
    UNKNOWN_BRANCH = "unknown_branch"
    VALUE_DATE_BEFORE_TRANSACTION = "value_date_before_transaction"
    AMOUNT_NOT_NUMERIC = "amount_not_numeric"
    ATYPICAL_AMOUNT = "atypical_amount"


#: Proportion de lignes touchées par chaque défaut (par rapport au nombre de lignes de base).
DEFAULT_DEFECT_RATES: dict[DefectType, float] = {
    DefectType.EXACT_DUPLICATE_ROW: 0.006,
    DefectType.DUPLICATE_KEY: 0.003,
    DefectType.CROSS_FILE_DUPLICATE: 0.001,
    DefectType.MISSING_MANDATORY: 0.012,
    DefectType.INVALID_DATE: 0.0015,
    DefectType.FUTURE_DATE: 0.001,
    DefectType.UNKNOWN_CURRENCY: 0.0015,
    DefectType.NEGATIVE_AMOUNT: 0.002,
    DefectType.FEE_ABOVE_CAP: 0.001,
    DefectType.UNKNOWN_BRANCH: 0.002,
    DefectType.VALUE_DATE_BEFORE_TRANSACTION: 0.002,
    DefectType.AMOUNT_NOT_NUMERIC: 0.001,
    DefectType.ATYPICAL_AMOUNT: 0.004,
}

FIELDS: tuple[str, ...] = (
    "transaction_id", "bank_code", "branch_code", "branch_city", "customer_id",
    "customer_name", "birth_date", "phone", "account_number", "operation_type",
    "transaction_date", "value_date", "amount", "currency",
)  # fmt: skip

MANDATORY_FIELDS_FOR_DEFECTS: tuple[str, ...] = (
    "customer_name", "amount", "transaction_date", "currency", "branch_code", "account_number",
)  # fmt: skip

CURRENCIES: tuple[tuple[str, str], ...] = (
    ("XAF", "Franc CFA (CEMAC)"),
    ("EUR", "Euro"),
    ("USD", "Dollar des États-Unis"),
)
UNKNOWN_CURRENCY_CODES: tuple[str, ...] = ("XFA", "CFA", "FCFA", "EURO")
NOT_NUMERIC_AMOUNTS: tuple[str, ...] = ("N/A", "#VALEUR!", "12O 000", "-")
#: Taux fixes utilisés uniquement pour générer des montants plausibles en devises.
XAF_PER_UNIT: dict[str, Decimal] = {
    "XAF": Decimal(1),
    "EUR": Decimal("655.957"),
    "USD": Decimal(600),
}


@dataclass(frozen=True)
class Bank:
    code: str
    name: str
    file_stem: str
    name_style: NameStyle
    share: float


BANKS: tuple[Bank, ...] = (
    Bank("BALP", "Banque Alpha", "alpha_T2", NameStyle.LAST_FIRST, 0.40),
    Bank("BBET", "Banque Beta", "beta_T2", NameStyle.FIRST_LAST, 0.34),
    Bank("BGAM", "Banque Gamma", "gamma_T2", NameStyle.UPPER_LAST_FIRST, 0.26),
)

CITIES: tuple[str, ...] = (
    "Douala", "Yaoundé", "Bafoussam", "Garoua", "Bamenda", "Kribi", "Libreville",
    "Port-Gentil", "Brazzaville", "Pointe-Noire", "N'Djamena", "Bangui", "Malabo",
)  # fmt: skip


@dataclass(frozen=True)
class Branch:
    bank_code: str
    code: str
    city: str


@dataclass(frozen=True)
class Person:
    """Identité réelle (fictive) d'un client : la vérité terrain de l'appariement."""

    person_id: str
    name: PersonName
    birth_date: date | None
    phone: str | None  # 9 chiffres, rendu avec l'indicatif fictif +999


@dataclass(frozen=True)
class Customer:
    """Fiche client telle qu'enregistrée dans une banque."""

    bank_code: str
    customer_id: str
    person_id: str
    branch_code: str
    recorded_name: str
    name_variant: Variant
    account_number: str
    birth_date: date | None
    phone: str | None
    homonym_of: str | None = None


@dataclass
class Row:
    uid: int
    values: dict[str, CellValue]


@dataclass(frozen=True)
class Defect:
    defect_id: str
    defect_type: DefectType
    uid: int
    field: str | None
    original: CellValue
    injected: CellValue
    related_uids: tuple[int, ...] = ()


@dataclass(frozen=True)
class GeneratorConfig:
    rows: int = 20_000
    seed: int = 42
    period_start: date = date(2025, 4, 1)
    period_end: date = date(2025, 6, 30)
    reference_date: date = date(2025, 6, 30)
    multi_bank_rate: float = 0.04
    intra_bank_duplicate_rate: float = 0.02
    homonym_pairs: int | None = None
    fee_cap_xaf: Decimal = Decimal(50_000)
    defect_rates: dict[DefectType, float] = field(
        default_factory=lambda: dict(DEFAULT_DEFECT_RATES)
    )

    def __post_init__(self) -> None:
        if self.rows < 500:
            raise ValueError("rows doit être >= 500 pour que chaque défaut soit représenté.")
        if self.period_end < self.period_start:
            raise ValueError("period_end doit être postérieure à period_start.")
        if self.reference_date < self.period_end:
            raise ValueError("reference_date doit être >= period_end.")
        for defect_type, rate in self.defect_rates.items():
            if not 0 <= rate <= 0.05:
                raise ValueError(f"Taux hors bornes [0 ; 0,05] pour {defect_type}: {rate}")
        if sum(self.defect_rates.values()) > 0.2:
            raise ValueError("La somme des taux de défauts ne doit pas dépasser 0,2.")

    @property
    def n_homonym_pairs(self) -> int:
        return self.homonym_pairs if self.homonym_pairs is not None else max(5, self.rows // 500)


@dataclass
class GeneratedDataset:
    config: GeneratorConfig
    banks: tuple[Bank, ...]
    branches: list[Branch]
    persons: list[Person]
    customers: list[Customer]
    rows_by_bank: dict[str, list[Row]]
    defects: list[Defect]

    def bank(self, code: str) -> Bank:
        return next(b for b in self.banks if b.code == code)

    @property
    def total_rows(self) -> int:
        return sum(len(rows) for rows in self.rows_by_bank.values())


# --------------------------------------------------------------------------- construction


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _build_branches(rng: random.Random) -> list[Branch]:
    branches: list[Branch] = []
    for bank in BANKS:
        n = rng.randint(3, 6)
        numbers = sorted(rng.sample(range(1, 60), n))
        cities = rng.sample(CITIES, n)
        branches += [
            Branch(bank.code, f"{bank.code}-{num:03d}", city)
            for num, city in zip(numbers, cities, strict=True)
        ]
    return branches


def _build_persons(rng: random.Random, n: int) -> list[Person]:
    persons = []
    for i in range(n):
        birth = (
            None if rng.random() < 0.08 else _random_date(rng, date(1950, 1, 1), date(2004, 12, 31))
        )
        phone = None if rng.random() < 0.12 else f"6{rng.randint(0, 99_999_999):08d}"
        persons.append(Person(f"P{i:06d}", random_name(rng), birth, phone))
    return persons


def _build_homonyms(
    rng: random.Random, persons: list[Person], k: int
) -> list[tuple[Person, Person]]:
    """Crée k personnes distinctes portant exactement le même nom qu'une personne existante."""
    candidates = [p for p in persons if p.birth_date is not None]
    pairs = []
    for j, source in enumerate(rng.sample(candidates, k)):
        assert source.birth_date is not None
        birth = source.birth_date
        while birth == source.birth_date:
            birth = _random_date(rng, date(1950, 1, 1), date(2004, 12, 31))
        twin = Person(
            f"P{len(persons) + j:06d}",
            source.name,
            birth,
            f"6{rng.randint(0, 99_999_999):08d}",
        )
        pairs.append((source, twin))
    return pairs


class _CustomerFactory:
    def __init__(self, rng: random.Random, branches: list[Branch]) -> None:
        self.rng = rng
        self.branches_by_bank = {
            b.code: [br for br in branches if br.bank_code == b.code] for b in BANKS
        }
        self.counters = dict.fromkeys(self.branches_by_bank, 0)

    def make(
        self,
        person: Person,
        bank: Bank,
        variant: Variant = Variant.NONE,
        *,
        drop_optional: bool = False,
        homonym_of: str | None = None,
    ) -> Customer:
        self.counters[bank.code] += 1
        n = self.counters[bank.code]
        branch = self.rng.choice(self.branches_by_bank[bank.code])
        if variant is Variant.NONE:
            recorded = format_name(person.name, bank.name_style)
        else:
            recorded = render_variant(person.name, bank.name_style, variant, self.rng)
        check = (n * 7 + int(branch.code[-3:])) % 97
        return Customer(
            bank_code=bank.code,
            customer_id=f"C-{bank.code}-{n:05d}",
            person_id=person.person_id,
            branch_code=branch.code,
            recorded_name=recorded,
            name_variant=variant,
            account_number=f"{branch.code}-{n:07d}-{check:02d}",
            birth_date=None if drop_optional and self.rng.random() < 0.3 else person.birth_date,
            phone=None if drop_optional and self.rng.random() < 0.3 else person.phone,
            homonym_of=homonym_of,
        )


def _build_customers(
    rng: random.Random,
    config: GeneratorConfig,
    persons: list[Person],
    homonym_pairs: list[tuple[Person, Person]],
    branches: list[Branch],
) -> list[Customer]:
    factory = _CustomerFactory(rng, branches)
    weights = [b.share for b in BANKS]
    primary: dict[str, Bank] = {}
    customers: list[Customer] = []

    for person in persons:
        bank = rng.choices(BANKS, weights=weights)[0]
        primary[person.person_id] = bank
        customers.append(factory.make(person, bank))

    for source, twin in homonym_pairs:
        bank = primary[source.person_id] if rng.random() < 0.5 else rng.choices(BANKS, weights)[0]
        primary[twin.person_id] = bank
        customers.append(factory.make(twin, bank, homonym_of=source.person_id))

    homonym_ids = {p.person_id for pair in homonym_pairs for p in pair}
    eligible = [p for p in persons if p.person_id not in homonym_ids]
    n_multi = round(len(persons) * config.multi_bank_rate)
    n_intra = round(len(persons) * config.intra_bank_duplicate_rate)
    chosen = rng.sample(eligible, n_multi + n_intra)

    for person in chosen[:n_multi]:
        other_banks = [b for b in BANKS if b is not primary[person.person_id]]
        bank = rng.choice(other_banks)
        # Dans 30 % des cas, seule la différence de style entre banques sépare les deux fiches.
        variant = (
            Variant.NONE if rng.random() < 0.3 else rng.choice(applicable_variants(person.name))
        )
        customers.append(factory.make(person, bank, variant, drop_optional=True))

    for person in chosen[n_multi:]:
        variant = rng.choice(applicable_variants(person.name))
        customers.append(
            factory.make(person, primary[person.person_id], variant, drop_optional=True)
        )

    return customers


def _money(value: float, currency: str) -> Decimal:
    amount = Decimal(str(value)) / XAF_PER_UNIT[currency]
    quantum = Decimal(1) if currency == "XAF" else Decimal("0.01")
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)


_AMOUNT_LAWS: dict[OperationType, tuple[float, float]] = {
    # (médiane en XAF, sigma du log)
    OperationType.DEPOSIT: (150_000, 1.0),
    OperationType.WITHDRAWAL: (80_000, 0.9),
    OperationType.TRANSFER_IN: (400_000, 1.1),
    OperationType.TRANSFER_OUT: (350_000, 1.1),
}


def upper_clip_xaf(op: OperationType) -> float:
    """Montant maximal (XAF) d'une opération saine : au-delà, la valeur est injectée."""
    if op is OperationType.FEE:
        return 15_000.0
    median, sigma = _AMOUNT_LAWS[op]
    return math.exp(math.log(median) + 3 * sigma)


def _draw_amount_xaf(rng: random.Random, op: OperationType) -> float:
    if op is OperationType.FEE:
        return float(rng.randint(500, 15_000))
    median, sigma = _AMOUNT_LAWS[op]
    mu = math.log(median)
    # Écrêtage à mu ± 3 sigma : les seules valeurs extrêmes du jeu sont celles injectées.
    value = rng.lognormvariate(mu, sigma)
    return min(max(value, math.exp(mu - 3 * sigma)), upper_clip_xaf(op))


def _build_rows(
    rng: random.Random, config: GeneratorConfig, customers: list[Customer], branches: list[Branch]
) -> dict[str, list[Row]]:
    city = {b.code: b.city for b in branches}
    branches_by_bank = {
        b.code: [br.code for br in branches if br.bank_code == b.code] for b in BANKS
    }
    activity = [rng.lognormvariate(0, 0.8) for _ in customers]
    ops = list(OperationType)
    op_weights = [30, 28, 15, 17, 10]

    if config.rows < len(customers):
        raise ValueError("rows doit être >= au nombre de fiches clients.")
    # Chaque fiche client apparaît au moins une fois, le reste suit l'activité de chacun.
    picked = list(customers) + rng.choices(
        customers, weights=activity, k=config.rows - len(customers)
    )
    drafts: dict[str, list[dict[str, CellValue]]] = {b.code: [] for b in BANKS}
    for customer in picked:
        op = rng.choices(ops, weights=op_weights)[0]
        currency = rng.choices(("XAF", "EUR", "USD"), weights=(92, 6, 2))[0]
        if op is OperationType.FEE:
            currency = "XAF"
        tx_date = _random_date(rng, config.period_start, config.period_end)
        branch = (
            customer.branch_code
            if rng.random() < 0.85
            else rng.choice(branches_by_bank[customer.bank_code])
        )
        drafts[customer.bank_code].append(
            {
                "transaction_id": None,
                "bank_code": customer.bank_code,
                "branch_code": branch,
                "branch_city": city[branch],
                "customer_id": customer.customer_id,
                "customer_name": customer.recorded_name,
                "birth_date": customer.birth_date,
                "phone": customer.phone,
                "account_number": customer.account_number,
                "operation_type": op.value,
                "transaction_date": tx_date,
                "value_date": tx_date + timedelta(days=rng.choice((0, 0, 0, 1, 1, 2))),
                "amount": _money(_draw_amount_xaf(rng, op), currency),
                "currency": currency,
            }
        )

    rows_by_bank: dict[str, list[Row]] = {}
    uid = 0
    for bank in BANKS:
        ordered = sorted(drafts[bank.code], key=lambda v: (v["transaction_date"], v["customer_id"]))
        rows = []
        for seq, values in enumerate(ordered, start=1):
            values["transaction_id"] = f"TX-{bank.code}-{seq:07d}"
            rows.append(Row(uid, values))
            uid += 1
        rows_by_bank[bank.code] = rows
    return rows_by_bank


# --------------------------------------------------------------------------- injection des défauts


class _Injector:
    def __init__(
        self,
        rng: random.Random,
        config: GeneratorConfig,
        rows_by_bank: dict[str, list[Row]],
        branches: list[Branch],
    ) -> None:
        self.rng = rng
        self.config = config
        self.rows_by_bank = rows_by_bank
        self.by_uid = {r.uid: (bank, r) for bank, rows in rows_by_bank.items() for r in rows}
        self.used: set[int] = set()
        self.defects: list[Defect] = []
        self.next_uid = max(self.by_uid) + 1
        self.known_branches = {b.code for b in branches}
        # (uid d'ancrage, banque cible, nouvelle ligne) : insérée juste après l'ancre.
        self.insertions: list[tuple[int, str, Row]] = []

    def count(self, defect_type: DefectType) -> int:
        rate = self.config.defect_rates.get(defect_type, 0.0)
        return 0 if rate == 0 else max(1, round(rate * self.config.rows))

    def pick(self, n: int, predicate: Callable[[Row], bool] = lambda _r: True) -> list[Row]:
        pool = [r for _bank, r in self.by_uid.values() if r.uid not in self.used and predicate(r)]
        pool.sort(key=lambda r: r.uid)
        chosen = self.rng.sample(pool, min(n, len(pool)))
        self.used.update(r.uid for r in chosen)
        return chosen

    def record(
        self,
        defect_type: DefectType,
        uid: int,
        field_name: str | None,
        original: CellValue,
        injected: CellValue,
        related: tuple[int, ...] = (),
    ) -> None:
        defect_id = f"D{len(self.defects) + 1:05d}"
        self.defects.append(
            Defect(defect_id, defect_type, uid, field_name, original, injected, related)
        )

    def mutate(
        self, row: Row, defect_type: DefectType, field_name: str, injected: CellValue
    ) -> None:
        original = row.values[field_name]
        row.values[field_name] = injected
        self.record(defect_type, row.uid, field_name, original, injected)

    # ---- défauts sur une ligne

    def inject_single_row_defects(self) -> None:
        rng = self.rng
        cap = self.config.fee_cap_xaf

        def op_is(*ops: OperationType) -> Callable[[Row], bool]:
            names = {o.value for o in ops}
            return lambda r: r.values["operation_type"] in names

        def is_xaf(r: Row) -> bool:
            return r.values["currency"] == "XAF"

        for row in self.pick(self.count(DefectType.NEGATIVE_AMOUNT), op_is(OperationType.DEPOSIT)):
            amount = row.values["amount"]
            assert isinstance(amount, Decimal)
            self.mutate(row, DefectType.NEGATIVE_AMOUNT, "amount", -amount)

        for row in self.pick(
            self.count(DefectType.FEE_ABOVE_CAP),
            lambda r: op_is(OperationType.FEE)(r) and is_xaf(r),
        ):
            injected = Decimal(rng.randint(int(cap) + 25_000, int(cap) * 6))
            self.mutate(row, DefectType.FEE_ABOVE_CAP, "amount", injected)

        for row in self.pick(
            self.count(DefectType.ATYPICAL_AMOUNT),
            lambda r: is_xaf(r) and not op_is(OperationType.FEE)(r),
        ):
            amount = row.values["amount"]
            assert isinstance(amount, Decimal)
            # Au moins 1,5 fois le plafond des montants sains du même type d'opération,
            # pour que le défaut soit réellement atypique dans son groupe.
            op = OperationType(str(row.values["operation_type"]))
            floor = upper_clip_xaf(op) * rng.uniform(1.5, 4.0)
            injected = max(amount * Decimal(str(round(rng.uniform(40, 120), 1))), Decimal(floor))
            self.mutate(row, DefectType.ATYPICAL_AMOUNT, "amount", injected.quantize(Decimal(1)))

        for row in self.pick(self.count(DefectType.UNKNOWN_CURRENCY)):
            self.mutate(
                row, DefectType.UNKNOWN_CURRENCY, "currency", rng.choice(UNKNOWN_CURRENCY_CODES)
            )

        impossible = (InvalidDate(2025, 2, 30), InvalidDate(2025, 4, 31), InvalidDate(2025, 6, 31),
                      InvalidDate(2025, 13, 5), InvalidDate(2025, 0, 12))  # fmt: skip
        for row in self.pick(self.count(DefectType.INVALID_DATE)):
            self.mutate(row, DefectType.INVALID_DATE, "transaction_date", rng.choice(impossible))

        for row in self.pick(self.count(DefectType.FUTURE_DATE)):
            future = self.config.reference_date + timedelta(days=rng.randint(30, 400))
            self.mutate(row, DefectType.FUTURE_DATE, "transaction_date", future)
            row.values["value_date"] = future

        for row in self.pick(self.count(DefectType.VALUE_DATE_BEFORE_TRANSACTION)):
            tx_date = row.values["transaction_date"]
            assert isinstance(tx_date, date)
            earlier = tx_date - timedelta(days=rng.randint(3, 20))
            self.mutate(row, DefectType.VALUE_DATE_BEFORE_TRANSACTION, "value_date", earlier)

        for row in self.pick(self.count(DefectType.UNKNOWN_BRANCH)):
            bank = str(row.values["bank_code"])
            unknown = f"{bank}-{rng.randint(60, 99):03d}"
            assert unknown not in self.known_branches
            self.mutate(row, DefectType.UNKNOWN_BRANCH, "branch_code", unknown)

        for row in self.pick(self.count(DefectType.MISSING_MANDATORY)):
            field_name = rng.choice(MANDATORY_FIELDS_FOR_DEFECTS)
            self.mutate(row, DefectType.MISSING_MANDATORY, field_name, None)

        for row in self.pick(self.count(DefectType.AMOUNT_NOT_NUMERIC)):
            self.mutate(
                row, DefectType.AMOUNT_NOT_NUMERIC, "amount", rng.choice(NOT_NUMERIC_AMOUNTS)
            )

    # ---- doublons (nouvelles lignes)

    def _new_row(self, values: dict[str, CellValue]) -> Row:
        row = Row(self.next_uid, dict(values))
        self.next_uid += 1
        return row

    def _anchor_near(self, bank: str, row: Row) -> int:
        rows = self.rows_by_bank[bank]
        index = next(i for i, r in enumerate(rows) if r.uid == row.uid)
        return rows[min(len(rows) - 1, index + self.rng.randint(0, 25))].uid

    def inject_duplicates(self) -> None:
        rng = self.rng
        for row in self.pick(self.count(DefectType.EXACT_DUPLICATE_ROW)):
            bank, _ = self.by_uid[row.uid]
            copy = self._new_row(row.values)
            self.insertions.append((self._anchor_near(bank, row), bank, copy))
            self.record(DefectType.EXACT_DUPLICATE_ROW, copy.uid, None, None, None, (row.uid,))

        for row in self.pick(
            self.count(DefectType.DUPLICATE_KEY), lambda r: r.values["currency"] == "XAF"
        ):
            bank, _ = self.by_uid[row.uid]
            copy = self._new_row(row.values)
            amount = row.values["amount"]
            assert isinstance(amount, Decimal)
            changed = (amount * Decimal(str(1 + rng.uniform(0.01, 0.10)))).quantize(Decimal(1))
            copy.values["amount"] = changed
            self.insertions.append((self._anchor_near(bank, row), bank, copy))
            self.record(DefectType.DUPLICATE_KEY, copy.uid, "amount", amount, changed, (row.uid,))

        for row in self.pick(self.count(DefectType.CROSS_FILE_DUPLICATE)):
            source_bank, _ = self.by_uid[row.uid]
            target_bank = rng.choice(sorted(b for b in self.rows_by_bank if b != source_bank))
            copy = self._new_row(row.values)
            anchor = rng.choice(self.rows_by_bank[target_bank]).uid
            self.insertions.append((anchor, target_bank, copy))
            self.record(DefectType.CROSS_FILE_DUPLICATE, copy.uid, None, None, None, (row.uid,))

    def apply_insertions(self) -> None:
        after: dict[int, list[Row]] = {}
        for anchor, _bank, row in self.insertions:
            after.setdefault(anchor, []).append(row)
        for bank, rows in self.rows_by_bank.items():
            merged: list[Row] = []
            for row in rows:
                merged.append(row)
                merged.extend(after.get(row.uid, []))
            self.rows_by_bank[bank] = merged


# --------------------------------------------------------------------------- point d'entrée


def generate(config: GeneratorConfig | None = None) -> GeneratedDataset:
    """Génère un jeu complet, déterministe pour une configuration donnée."""
    config = config or GeneratorConfig()
    rng = random.Random(config.seed)

    branches = _build_branches(rng)
    persons = _build_persons(rng, max(200, config.rows // 8))
    homonym_pairs = _build_homonyms(rng, persons, config.n_homonym_pairs)
    all_persons = persons + [twin for _source, twin in homonym_pairs]
    customers = _build_customers(rng, config, persons, homonym_pairs, branches)
    rows_by_bank = _build_rows(rng, config, customers, branches)

    injector = _Injector(rng, config, rows_by_bank, branches)
    injector.inject_single_row_defects()
    injector.inject_duplicates()
    injector.apply_insertions()

    return GeneratedDataset(
        config=config,
        banks=BANKS,
        branches=branches,
        persons=all_persons,
        customers=customers,
        rows_by_bank=injector.rows_by_bank,
        defects=injector.defects,
    )
