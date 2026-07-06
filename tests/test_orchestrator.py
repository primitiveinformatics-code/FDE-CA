from unittest.mock import patch

from bank_statement_analyzer.agents.orchestrator import FileInput, Orchestrator
from bank_statement_analyzer.parsing.models import ParseResult, StatementMeta


def _good_result(bank, pdf, account_ref, txns):
    meta = StatementMeta(
        bank=bank, pdf=pdf, account_ref=account_ref,
        account_holder_name="JOHN DOE", account_number_masked="XXXX1234", ifsc="HDFC0001234",
    )
    return ParseResult(meta=meta, transactions=txns, success=True)


def _flagged_result(bank, pdf, account_ref, reason):
    meta = StatementMeta(bank=bank, pdf=pdf, account_ref=account_ref, flagged=True, flag_reason=reason)
    return ParseResult(meta=meta, transactions=[], success=False, error=reason)


def test_run_without_api_key_produces_run_result(make_txn):
    txns_acc1 = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "SB INT.PD FOR QTR", credit=500),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-05", "ATM CASH WDL", debit=2000),
    ]

    def fake_process_file(pdf_path, bank, account_ref, password, llm_client, vault):
        return _good_result("HDFC", "hdfc.pdf", account_ref, txns_acc1)

    orch = Orchestrator()
    with patch("bank_statement_analyzer.agents.orchestrator.extraction_agent.process_file", side_effect=fake_process_file):
        run = orch.run([FileInput(path="hdfc.pdf", bank="HDFC")], client_label="Test Client", api_key=None)

    assert run.client_label == "Test Client"
    assert len(run.transactions) == 2
    assert run.run_totals is not None
    assert run.run_totals.total_credits == 500
    assert run.run_totals.total_debits == 2000


def test_flagged_file_excluded_and_reported(make_txn):
    good_txns = [make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "SB INT.PD", credit=100)]

    def fake_process_file(pdf_path, bank, account_ref, password, llm_client, vault):
        if "bad" in pdf_path:
            return _flagged_result("Unknown", "bad.pdf", account_ref, "Could not map statement columns")
        return _good_result("HDFC", "hdfc.pdf", account_ref, good_txns)

    orch = Orchestrator()
    with patch("bank_statement_analyzer.agents.orchestrator.extraction_agent.process_file", side_effect=fake_process_file):
        run = orch.run(
            [FileInput(path="hdfc.pdf", bank="HDFC"), FileInput(path="bad.pdf", bank=None)],
            client_label="Test Client", api_key=None,
        )

    # bad.pdf's txns excluded from downstream matching entirely
    assert len(run.transactions) == 1
    issues = [n.issue for n in run.needs_attention]
    assert any("Could not map statement columns" in i for i in issues)


def test_self_transfer_and_refund_flow_through_orchestrator(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-05", "UPI/DR/rrn222333444/self xfer", debit=7000),
        make_txn(1, "SBI", "sbi.pdf", "ACC-2", "2025-04-05", "UPI/CR/rrn222333444/self xfer", credit=7000),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-10", "Amazon order payment", debit=1200),
        make_txn(3, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-14", "Amazon refund reversed", credit=1200),
    ]

    def fake_process_file(pdf_path, bank, account_ref, password, llm_client, vault):
        if "sbi" in pdf_path:
            return _good_result("SBI", "sbi.pdf", account_ref, [txns[1]])
        return _good_result("HDFC", "hdfc.pdf", account_ref, [txns[0], txns[2], txns[3]])

    orch = Orchestrator()
    with patch("bank_statement_analyzer.agents.orchestrator.extraction_agent.process_file", side_effect=fake_process_file):
        run = orch.run(
            [FileInput(path="hdfc.pdf", bank="HDFC"), FileInput(path="sbi.pdf", bank="SBI")],
            client_label="Test Client", api_key=None,
        )

    assert len(run.self_transfers.confirmed) == 1
    assert len(run.refunds.confirmed) == 1
    # confirmed transfer/refund legs excluded from expense/income totals
    excluded = run.excluded_refs()
    assert len(excluded) == 4


def test_export_writes_files(make_txn, tmp_path):
    txns = [make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "SB INT.PD", credit=100)]

    def fake_process_file(pdf_path, bank, account_ref, password, llm_client, vault):
        return _good_result("HDFC", "hdfc.pdf", account_ref, txns)

    orch = Orchestrator()
    with patch("bank_statement_analyzer.agents.orchestrator.extraction_agent.process_file", side_effect=fake_process_file):
        run = orch.run([FileInput(path="hdfc.pdf", bank="HDFC")], client_label="Test Client", api_key=None)

    xlsx_path = tmp_path / "out.xlsx"
    csv_path = tmp_path / "out.csv"
    orch.export(run, str(xlsx_path), str(csv_path))
    assert xlsx_path.exists()
    assert csv_path.exists()
