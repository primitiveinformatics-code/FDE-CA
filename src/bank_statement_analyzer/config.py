"""Central configuration: thresholds, taxonomies, pricing.

All numeric knobs the CA firm might tune live here rather than scattered
through the matching/classification code.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# ITR heads / expense taxonomy (fixed per PRD)
# ---------------------------------------------------------------------------

ITR_HEADS = [
    "Salary",
    "Interest",
    "Business/Professional receipts",
    "Rent",
    "Capital gains",
    "Other",
]

EXPENSE_CATEGORIES = [
    "UPI merchant spend",
    "Cash/ATM",
    "Loan EMIs",
    "Charges & fees",
    "Credit-card payments",
    "Taxes/TDS",
    "Other",
]

CONFIDENCE_LEVELS = ("high", "medium", "low")


# ---------------------------------------------------------------------------
# Matching / reconciliation thresholds
# ---------------------------------------------------------------------------

@dataclass
class MatchingConfig:
    # Self-transfer fallback matching (FR-3)
    transfer_date_window_days: int = 3
    transfer_narration_similarity_min: float = 0.35

    # Refund matching
    refund_date_window_days: int = 45
    refund_narration_similarity_min: float = 0.30

    # Anomaly thresholds (FR-9)
    large_credit_multiple_of_median: float = 5.0
    large_credit_floor: float = 200_000.0
    large_cash_deposit_threshold: float = 50_000.0
    round_trip_window_days: int = 1

    # Recurring detection (FR-8)
    recurring_min_occurrences: int = 3
    recurring_period_tolerance_days: int = 4


@dataclass
class CostConfig:
    # USD per million tokens, Claude Sonnet pricing tiers (input/output)
    input_price_per_mtok: float = 3.0
    output_price_per_mtok: float = 15.0
    warn_threshold_usd: float = 2.0


@dataclass
class AppConfig:
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    model_name: str = "claude-sonnet-5"
    keyring_service: str = "bank-statement-analyzer"
    keyring_username: str = "anthropic-api-key"


DEFAULT_CONFIG = AppConfig()


# ---------------------------------------------------------------------------
# Keyword banks used by deterministic heuristics
# ---------------------------------------------------------------------------

REFUND_KEYWORDS = [
    "refund", "reversal", "reversed", "rev chrg", "chargeback",
    "cancelled", "canceled", "cancellation", "txn fail", "failed txn",
    "auto refund", "dr rev", "cr rev", "refd", "amount reversed",
    "order cancel", "return refund", "reverse charge",
]

INTEREST_KEYWORDS = [
    "int.pd", "int pd", "interest credit", "sb int", "int credit",
    "savings interest", "interest paid", "int.coll", "fd interest",
    "td interest", "interest on deposit",
]

LOAN_DISBURSAL_KEYWORDS = [
    "loan disb", "disbursement", "loan credit", "loan sanction",
]

GIFT_KEYWORDS = ["gift", "gift received"]

CC_PAYMENT_KEYWORDS = [
    "credit card payment", "cc payment", "card bill", "card autopay",
    "creditcard", "cc bill", "card payment received",
]

CC_FEE_KEYWORDS = [
    "annual fee", "late payment fee", "card fee", "finance charge",
    "over limit fee", "gst on cc",
]

CASH_KEYWORDS = ["atm", "cash wdl", "cash withdrawal", "cash deposit", "cdm"]

EMI_KEYWORDS = ["emi", "loan inst", "installment", "instalment"]

SIP_KEYWORDS = ["sip", "mutual fund", "systematic investment"]

RENT_KEYWORDS = ["rent"]

SUBSCRIPTION_KEYWORDS = [
    "netflix", "spotify", "prime", "subscription", "hotstar", "youtube premium",
]

TDS_TAX_KEYWORDS = ["tds", "income tax", "advance tax", "self assessment tax", "gst"]

UPI_KEYWORDS = ["upi"]

TRANSFER_MODE_KEYWORDS = [
    "upi", "neft", "imps", "rtgs", "fund transfer", "funds transfer",
    "self transfer", "own account", "a/c transfer", "acct transfer",
]

RRN_PATTERNS = [
    r"\bRRN[:\s]*([0-9]{6,18})\b",
    r"\bUPI[/-]([0-9]{6,18})\b",
    r"\bIMPS[/-]([0-9]{6,18})\b",
    r"\bNEFT[/-]?([A-Z0-9]{6,20})\b",
    r"\b(?:REF|REFNO|REF NO)[:\s#]*([A-Z0-9]{6,20})\b",
]
