import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from bank_statement_analyzer.parsing.models import Transaction


@pytest.fixture
def make_txn():
    def _make(serial, bank, pdf, account_ref, txn_date, description, debit=0.0, credit=0.0, balance=None):
        return Transaction(
            serial=serial, bank=bank, pdf=pdf, account_ref=account_ref,
            txn_date=txn_date if isinstance(txn_date, date) else date.fromisoformat(txn_date),
            description=description, debit=debit, credit=credit, balance=balance,
        )
    return _make
