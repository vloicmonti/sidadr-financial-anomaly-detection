"""Module G — moteur de règles métier déclaratives."""

from sidadr.business_rules.engine import RuleOutcome, RuleReport, Violation, evaluate_rules
from sidadr.business_rules.model import Rule, RuleSet, load_rules

__all__ = [
    "Rule",
    "RuleOutcome",
    "RuleReport",
    "RuleSet",
    "Violation",
    "evaluate_rules",
    "load_rules",
]
