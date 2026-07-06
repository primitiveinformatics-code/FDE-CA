from bank_statement_analyzer.reconciliation.transfer_matcher import match_self_transfers


def test_rrn_match_confirms_pair(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "UPI/DR/rrn123456789/self/xfer", debit=5000)
    c = make_txn(1, "SBI", "sbi.pdf", "ACC-2", "2025-05-01", "UPI/CR/rrn123456789/self/xfer", credit=5000)
    result = match_self_transfers([d, c])
    assert len(result.confirmed) == 1
    pair = result.confirmed[0]
    assert pair.method == "rrn"
    assert pair.confidence == "high"
    assert {pair.leg_a_ref, pair.leg_b_ref} == {d.ref, c.ref}
    assert not result.unmatched


def test_amount_date_narration_fallback_low_confidence(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "NEFT to own SBI account transfer", debit=12000)
    c = make_txn(2, "SBI", "sbi.pdf", "ACC-2", "2025-05-02", "NEFT from own HDFC account transfer", credit=12000)
    result = match_self_transfers([d, c])
    assert len(result.confirmed) == 1
    pair = result.confirmed[0]
    assert pair.method == "amount_date_narration"
    assert pair.confidence == "low"


def test_unmatched_transfer_candidate_called_out(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "UPI/DR/rrn999999999/merchant payment", debit=750)
    result = match_self_transfers([d])
    assert not result.confirmed
    assert len(result.unmatched) == 1
    assert result.unmatched[0].kind == "self_transfer"


def test_same_account_debit_credit_not_matched(make_txn):
    # Same account can't be a self-transfer counterparty to itself.
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "UPI/DR/rrn555/xfer", debit=1000)
    c = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "UPI/CR/rrn555/xfer", credit=1000)
    result = match_self_transfers([d, c])
    assert not result.confirmed


def test_ambiguous_multiple_candidates_flagged(make_txn):
    d = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "fund transfer to own account", debit=3000)
    c1 = make_txn(2, "SBI", "sbi.pdf", "ACC-2", "2025-05-01", "fund transfer received", credit=3000)
    c2 = make_txn(3, "ICICI", "icici.pdf", "ACC-3", "2025-05-01", "fund transfer received", credit=3000)
    result = match_self_transfers([d, c1, c2])
    assert not result.confirmed
    assert len(result.ambiguous) == 1
    assert set(result.ambiguous[0].candidate_refs) == {c1.ref, c2.ref}
