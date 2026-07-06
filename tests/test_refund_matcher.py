from bank_statement_analyzer.reconciliation.refund_matcher import is_refund_like, match_refunds


def test_is_refund_like_detects_keywords(make_txn):
    r = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-05", "AMAZON REFUND FOR ORDER 123", credit=999)
    not_refund = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-05", "SALARY CREDIT MAY", credit=50000)
    assert is_refund_like(r)
    assert not is_refund_like(not_refund)


def test_rrn_match_confirms_refund_pair(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "UPI/DR/rrn777888999/Amazon Pay", debit=1500)
    r = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-04", "UPI/CR/rrn777888999/Amazon refund", credit=1500)
    result = match_refunds([d, r])
    assert len(result.confirmed) == 1
    pair = result.confirmed[0]
    assert pair.method == "rrn"
    assert pair.confidence == "high"
    assert pair.leg_a_ref == d.ref
    assert pair.leg_b_ref == r.ref


def test_amount_date_narration_fallback_same_account_medium_confidence(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "Flipkart order payment", debit=2200)
    r = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-10", "Flipkart order refund reversed", credit=2200)
    result = match_refunds([d, r])
    assert len(result.confirmed) == 1
    pair = result.confirmed[0]
    assert pair.method == "amount_date_narration"
    assert pair.confidence == "medium"


def test_cross_account_fallback_low_confidence(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "Flipkart order payment", debit=2200)
    r = make_txn(2, "SBI", "sbi.pdf", "ACC-2", "2025-05-10", "Flipkart order refund reversed", credit=2200)
    result = match_refunds([d, r])
    assert len(result.confirmed) == 1
    assert result.confirmed[0].confidence == "low"


def test_orphan_refund_flagged_unmatched(make_txn):
    r = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-04", "Merchant refund credit no matching debit", credit=800)
    result = match_refunds([r])
    assert not result.confirmed
    assert len(result.unmatched) == 1
    assert result.unmatched[0].kind == "refund"


def test_refund_before_original_debit_not_matched(make_txn):
    # A "refund" dated before any debit can't be its reversal.
    r = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "order refund reversed", credit=500)
    d = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-10", "order payment", debit=500)
    result = match_refunds([d, r])
    assert not result.confirmed
    assert len(result.unmatched) == 1


def test_ambiguous_multiple_original_debits(make_txn):
    r = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-10", "order refund reversed", credit=1000)
    d1 = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "order refund reversed payment", debit=1000)
    d2 = make_txn(3, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-02", "order refund reversed payment", debit=1000)
    result = match_refunds([d1, d2, r])
    assert not result.confirmed
    assert len(result.ambiguous) == 1
    assert set(result.ambiguous[0].candidate_refs) == {d1.ref, d2.ref}
