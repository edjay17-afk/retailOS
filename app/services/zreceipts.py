"""Map saved official StoreHub Z-readings to 80mm receipt data.

No OCR and no recalculation: registration fields, section values and totals come from the report's own saved source text/tables.
"""
from __future__ import annotations

from ..models import ZReading


def _after(label: str, text: str) -> str:
    """Return the non-empty value printed after a header label, without guessing if it is absent."""
    lines = [line.strip() for line in text.splitlines()]
    for i, line in enumerate(lines):
        if line == label:
            for value in lines[i + 1:i + 6]:
                if value:
                    return "" if value.endswith(":") else value
    return ""


def _table_values(row: ZReading) -> dict[str, str]:
    values: dict[str, str] = {}
    for section in row.detail_json or []:
        for table in section.get("tables", []):
            for cells in table.get("rows", []):
                if len(cells) >= 2 and cells[0].endswith(":"):
                    values[cells[0]] = cells[1]
    return values


def _collections(row: ZReading) -> tuple[list[dict], dict[str, str]]:
    collections: list[dict] = []
    total = {"count": "", "amount": ""}
    for section in row.detail_json or []:
        if section.get("title", "").casefold() != "collections summary":
            continue
        for table in section.get("tables", []):
            for cells in table.get("rows", []):
                if not cells or cells[0] == "Payment Method":
                    continue
                if cells[0] == "Total Collection":
                    if len(cells) >= 3:
                        total = {"count": cells[1], "amount": cells[2]}
                elif len(cells) >= 3:
                    collections.append({"method": cells[0], "count": cells[1], "amount": cells[2]})
    return collections, total


def receipt_data(row: ZReading) -> dict | None:
    """Return the exact receipt fields for one fully read official report, or None when detail is unavailable."""
    if not row.detail_ok or not row.source_text or not row.detail_json:
        return None
    text = row.source_text
    values = _table_values(row)
    collections, collection_total = _collections(row)

    def v(label: str) -> str:
        return values.get(label + ":", "")

    counters = [(label, v(label)) for label in (
        "Beg. SI #", "End. SI #", "Beg. VOID #", "End. VOID #", "Beg. REFUND #", "End. REFUND #", "Beg. TRANS #", "End. TRANS #",
        "TRANS. Count", "SALES Count", "VOID Count", "REFUND Count",
    )]
    counters.extend([("Reset Counter No.:", _after("Reset Counter No.:", text)), ("Z Count No.:", _after("Z Count No.:", text))])
    transaction = [("Cash in Drawer:", v("Cash in Drawer"))]
    if "GCASH:" in values:
        transaction.append(("GCASH:", v("GCASH")))
    transaction.extend((label, v(label)) for label in ("Opening Fund", "Pay In", "Less Withdrawal", "Payment Received", "Expected Drawer", "Short/Over"))
    return {
        "store": _after("Store Name:", text),
        "operatedBy": _after("Operated By:", text),
        "accred": _after("Accred:", text),
        "min": _after("MIN:", text),
        "ptu": _after("PTU:", text),
        "sn": _after("S/N:", text),
        "reportDate": _after("Report Date:", text),
        "reportTime": _after("Report Time:", text),
        "startDateTime": _after("Start Date & Time:", text),
        "endDateTime": _after("End Date & Time:", text),
        "counters": counters,
        "sales": [(label, v(label)) for label in ("Present Accumulated Sales", "Previous Accumulated Sales", "Sales for the Day", "Present Accumulated Net Sales", "Previous Accumulated Net Sales")],
        "breakdown": [(label, v(label)) for label in ("VATABLE SALES", "VAT AMOUNT", "VAT EXEMPT SALES", "ZERO RATED SALES", "Gross Amount", "Less Discount", "Less Refund", "Less Void", "Less VAT Adjustment", "Net Amount")],
        "discount": [(label, v(label)) for label in ("SC Disc.", "PWD Disc.", "NAAC Disc.", "Medal of Valor Holder Disc.", "Solo Parent Disc.", "Other Disc.", "Total Discount")],
        "salesAdjustment": [(label, v(label)) for label in ("VOID", "Refund", "Total Sales Adjustments")],
        "vatAdjustment": [(label, v(label)) for label in ("SC TRANS.", "PWD TRANS.", "Solo Parent TRANS.", "REG. Disc. TRANS.", "ZERO-RATED TRANS.", "VAT on Refund", "VAT on VOID", "Other VAT Adjustments", "Total VAT Adjustments")],
        "collections": collections,
        "collectionTotal": collection_total,
        "transaction": transaction,
    }


# (receipt key, heading used in the CSV) for every block of label/value lines printed on the receipt, in printing order
_CSV_HEADER_FIELDS = (("operatedBy", "Operated By"), ("accred", "Accred #"), ("min", "MIN"), ("ptu", "PTU"), ("sn", "S/N"), ("reportDate", "Report Date"),
                      ("reportTime", "Report Time"), ("startDateTime", "Start Date & Time"), ("endDateTime", "End Date & Time"))
_CSV_SECTIONS = (("counters", ""), ("sales", ""), ("breakdown", "Breakdown of Sales: "), ("discount", "Discount Summary: "),
                 ("salesAdjustment", "Sales Adjustment: "), ("vatAdjustment", "VAT Adjustment: "), ("transaction", "Transaction Summary: "))


def csv_columns(receipts: list[dict | None]) -> tuple[list[str], list[list[str]]]:
    """Every field of the receipt as spreadsheet columns, one row per report (the same order as `receipts`; None = detail not read yet, left blank).
    Values are the text the official report printed. The payment methods differ from store to store, so each one found gets its own Trans/Amount columns."""
    def clean(label: str) -> str:
        return label[:-1] if label.endswith(":") else label

    found = [r for r in receipts if r]
    heads = ["Store Name (on receipt)"] + [h for _, h in _CSV_HEADER_FIELDS]
    labels: dict[str, list[str]] = {}
    for key, prefix in _CSV_SECTIONS:
        seen: list[str] = []
        for r in found:
            seen.extend(clean(label) for label, _ in r[key] if clean(label) not in seen)
        labels[key] = seen
        heads.extend(prefix + label for label in seen)
    methods: list[str] = []
    for r in found:
        methods.extend(c["method"] for c in r["collections"] if c["method"] not in methods)
    for m in methods:
        heads.extend((f"Collections: {m} Trans", f"Collections: {m} Amount"))
    heads.extend(("Collections: Total Collection Trans", "Collections: Total Collection Amount"))

    rows = []
    for r in receipts:
        if not r:
            rows.append([""] * len(heads))
            continue
        out = [r["store"]] + [r[key] for key, _ in _CSV_HEADER_FIELDS]
        for key, _ in _CSV_SECTIONS:
            printed = {clean(label): value for label, value in r[key]}
            out.extend(printed.get(label, "") for label in labels[key])
        by_method = {c["method"]: c for c in r["collections"]}
        for m in methods:
            out.extend((by_method[m]["count"], by_method[m]["amount"]) if m in by_method else ("", ""))
        out.extend((r["collectionTotal"]["count"], r["collectionTotal"]["amount"]))
        rows.append(out)
    return heads, rows
