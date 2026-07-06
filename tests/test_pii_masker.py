from bank_statement_analyzer.pii.masker import PIIVault, mask_narration


def test_masks_pan_email_mobile_and_long_numbers():
    vault = PIIVault()
    text = "Payment to ABCDE1234F, contact a.b@example.com or 9876543210, acct 123456789012"
    masked = mask_narration(vault, text)
    assert "ABCDE1234F" not in masked
    assert "a.b@example.com" not in masked
    assert "9876543210" not in masked
    assert "123456789012" not in masked
    assert "[PAN-1]" in masked
    assert "[EMAIL-1]" in masked
    assert "[MOBILE-1]" in masked


def test_rehydrate_restores_original():
    vault = PIIVault()
    original = "Refund from ABCDE1234F re order"
    masked = mask_narration(vault, original)
    assert masked != original
    restored = vault.rehydrate(masked)
    assert restored == original


def test_same_value_reuses_same_token():
    vault = PIIVault()
    text = "9876543210 called about 9876543210"
    masked = mask_narration(vault, text)
    assert masked.count("[MOBILE-1]") == 2
