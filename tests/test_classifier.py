from bank_statement_analyzer.classification.classifier import (
    detect_anomalies,
    detect_recurring,
    tag_transactions,
)


def test_interest_credit_auto_tagged(make_txn):
    t = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "SB INT.PD FOR QTR", credit=250)
    tag_transactions([t], excluded_refs=set())
    assert t.itr_head == "Interest"
    assert t.itr_head_confidence == "high"
    assert "26AS-likely" in t.tags


def test_expense_category_tagged(make_txn):
    t = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "ATM CASH WDL", debit=2000)
    tag_transactions([t], excluded_refs=set())
    assert t.category == "Cash/ATM"


def test_excluded_refs_skipped(make_txn):
    t = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "UPI/DR/rrn1/xfer", debit=500)
    tag_transactions([t], excluded_refs={t.ref})
    assert t.category is None


def test_recurring_emi_detected(make_txn):
    txns = [
        make_txn(i, "HDFC", "hdfc.pdf", "ACC-1", d, "HOME LOAN EMI PAYMENT", debit=15000)
        for i, d in enumerate(["2025-01-05", "2025-02-05", "2025-03-05"], start=1)
    ]
    groups = detect_recurring(txns, excluded_refs=set())
    assert len(groups) == 1
    assert groups[0].kind == "EMI"
    assert len(groups[0].refs) == 3


def test_large_credit_anomaly(make_txn):
    small = [make_txn(i, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "merchant credit", credit=1000) for i in range(1, 6)]
    big = make_txn(10, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-02", "big merchant credit", credit=5_000_000)
    flags = detect_anomalies(small + [big], excluded_refs=set())
    kinds = {f.ref: f.kind for f in flags}
    assert kinds.get(big.ref) == "large_credit"


def test_round_trip_flagged(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "cash out to X", debit=10000)
    c = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "cash in from X", credit=10000)
    flags = detect_anomalies([d, c], excluded_refs=set())
    kinds = {f.ref: f.kind for f in flags}
    assert kinds.get(d.ref) == "round_trip"
    assert kinds.get(c.ref) == "round_trip"
