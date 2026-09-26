"""CSV writing that is safe to open in a spreadsheet.

A cell starting with = + - @ TAB or CR is executed by Excel/Sheets as a formula,
so those cells get a leading single quote (BUILD-SEC section 6, exports).
"""
import csv
import io

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Render one value as text, neutralising formula injection."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def write_csv(header: list[str], rows) -> str:
    """Return a complete CSV document (header + rows) as text."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([safe_cell(cell) for cell in header])
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buffer.getvalue()
