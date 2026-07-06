"""Source-reference helpers.

Every row in the output carries a `serial · bank · pdf` reference so any
figure in the workbook can be traced back to its source line (P0
traceability requirement).
"""
from __future__ import annotations

REF_SEP = " · "  # middle dot, matches "serial · bank · pdf" in the spec


def make_ref(serial: int, bank: str, pdf: str) -> str:
    return f"{serial}{REF_SEP}{bank}{REF_SEP}{pdf}"


def parse_ref(ref: str) -> tuple[str, str, str]:
    parts = ref.split(REF_SEP)
    if len(parts) != 3:
        raise ValueError(f"Malformed reference: {ref!r}")
    serial, bank, pdf = parts
    return serial, bank, pdf
