"""
Consolidate every GFIM daily trading report into tidy tables.

Reads  data/raw/GFIM_Reports/<year>/TRADING-REPORT-FOR-GFIM-DDMMYYYY.xlsx
Writes one row per security per day, split by market segment.
"""

import datetime as dt
import os
import re
import sys
import warnings

import pandas as pd
from openpyxl import load_workbook

warnings.simplefilter("ignore")

ROOT = os.environ.get("GFIM_RAW", "data/raw/GFIM_Reports")
OUT = os.environ.get("GFIM_PROCESSED", "data/processed")

# ---------------------------------------------------------------- segments
SEGMENT_RULES = [
    ("ddep", ("DDEP",)),
    ("sell_buy_back", ("SELL BUY BACK", "SELLBUYBACK")),
    ("repo", ("REPO", "COLLATERALIZED", "GMRA")),
    ("corporate", ("CORPORATE",)),
    ("bills", ("BILL",)),                       # TREASURY BILLS, GOG-BILLS, BOG BILLS
    ("gog_new", ("NEW GOG",)),
    ("gog_old", ("OLD GOG",)),
    ("gog", ("GOG-NOTES", "GOG NOTES", "NOTES & BONDS", "NOTES AND BONDS")),
    ("summary", ("SUMMARY",)),
]


def segment_of(sheet_name: str):
    s = re.sub(r"\s+", " ", sheet_name.strip().upper())
    for seg, keys in SEGMENT_RULES:
        if any(k in s for k in keys):
            return seg
    return None


# ---------------------------------------------------------------- columns
def norm(h) -> str:
    """Normalise a header cell: collapse whitespace/newlines, upper-case."""
    if h is None:
        return ""
    return re.sub(r"\s+", " ", str(h)).strip().upper()


COLUMN_MAP = {
    "NO.": "row_no", "NO": "row_no",
    "ISSUERS": "issuer", "ISSUER": "issuer",
    "TENOR": "tenor",
    "SECURITY DESCRIPTION": "security",
    "ISIN": "isin",
    "OPENING YIELD": "opening_yield",
    "CLOSING YIELD": "closing_yield",
    "YIELD": "yield",
    "OPENING PRICE": "opening_price",
    "CLOSING PRICE": "closing_price",
    "END OF DAY CLOSING PRICE": "closing_price",
    "WEIGHTED AVERAGE CLOSING PRICE": "closing_price",
    "VOLUME": "volume", "VOLUME TRADED": "volume",
    "NUMBER TRADED": "num_trades",
    "DAY LOW YIELD": "day_low_yield", "DAY HIGH YIELD": "day_high_yield",
    "DAY LOW PRICE": "day_low_price", "DAY HIGH PRICE": "day_high_price",
    "YEAR LOW PRICE": "day_low_price", "YEAR HIGH PRICE": "day_high_price",
    "DAYS TO MATURITY": "days_to_maturity_reported",
    "MATURITY DATE": "maturity_date",
    "APPLICABLE DATE": "applicable_date",
}

NUMERIC = ["opening_yield", "closing_yield", "yield", "opening_price",
           "closing_price", "volume", "num_trades", "day_low_yield",
           "day_high_yield", "day_low_price", "day_high_price"]

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def date_from_filename(path: str):
    m = re.search(r"(\d{2})(\d{2})(\d{4})", os.path.basename(path))
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def find_header(rows, limit=12):
    """The header row is the first one mentioning ISIN (or SECURITY DESCRIPTION)."""
    for i, row in enumerate(rows[:limit]):
        cells = [norm(c) for c in row]
        if "ISIN" in cells:
            return i
        if "SECURITY DESCRIPTION" in cells and any(c for c in cells if "PRICE" in c or "YIELD" in c):
            return i
    return None


def to_number(v):
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--", "N/A", "NA", "#DIV/0!", "#VALUE!", "#REF!"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return dt.datetime.strptime(v.strip()[:10], fmt).date()
            except ValueError:
                pass
    return None


def parse_sheet(ws, report_date, segment, source):
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    h = find_header(rows)
    if h is None:
        return []

    headers = [norm(c) for c in rows[h]]
    # map each column index to a canonical field name
    fields = {}
    for i, head in enumerate(headers):
        if not head:
            continue
        key = COLUMN_MAP.get(head)
        if key is None:                      # tolerate small wording changes
            for pat, name in COLUMN_MAP.items():
                if head.replace(" ", "") == pat.replace(" ", ""):
                    key = name
                    break
        if key:
            fields.setdefault(key, []).append(i)   # sheets repeat some headers

    if "isin" not in fields:
        return []

    def pick(row, key):
        """First non-empty cell among the columns carrying this header."""
        for i in fields.get(key, []):
            if i < len(row):
                v = row[i]
                if v not in (None, ""):
                    return v
        return None

    out = []
    carry = {"tenor": None, "issuer": None}
    for row in rows[h + 1:]:
        isin = pick(row, "isin")
        isin = str(isin).strip().upper() if isin is not None else ""

        # carry the group labels down the block (they appear once per group)
        for k in ("tenor", "issuer"):
            v = pick(row, k)
            if v not in (None, ""):
                v = re.sub(r"\s+", " ", str(v)).strip()
                if v.upper() not in ("TOTAL", "GRAND TOTAL") and not v.isdigit():
                    carry[k] = v

        if not ISIN_RE.match(isin):          # skips TOTAL rows, notes, blanks
            continue

        rec = {"report_date": report_date, "segment": segment,
               "sheet": ws.title.strip(), "isin": isin,
               "tenor": carry["tenor"], "issuer": carry["issuer"],
               "source_file": source}

        for key in fields:
            if key in ("isin", "tenor", "issuer", "row_no"):
                continue
            v = pick(row, key)
            if key in NUMERIC:
                rec[key] = to_number(v)
            elif key in ("maturity_date", "applicable_date"):
                rec[key] = to_date(v)
            else:
                rec[key] = re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") else None
        md = rec.get("maturity_date")
        rec["days_to_maturity"] = (md - report_date).days if md else None
        out.append(rec)
    return out


def parse_file(path):
    report_date = date_from_filename(path)
    if report_date is None:
        return []
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:                   # corrupt or unreadable file
        print(f"   ! {os.path.basename(path)}: {e}")
        return []
    recs = []
    for ws in wb.worksheets:
        seg = segment_of(ws.title)
        if seg in (None, "summary", "repo"):  # repo sheets are a status matrix
            continue
        try:
            recs += parse_sheet(ws, report_date, seg, os.path.basename(path))
        except Exception as e:
            print(f"   ! {os.path.basename(path)} [{ws.title}]: {e}")
    wb.close()
    return recs


def main():
    files = []
    for year in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, year)
        if os.path.isdir(d) and year.isdigit():
            files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.lower().endswith((".xlsx", ".xls"))]
    print(f"{len(files)} report files")

    all_recs = []
    for i, f in enumerate(files, 1):
        all_recs += parse_file(f)
        if i % 50 == 0:
            print(f"   {i}/{len(files)}  rows so far: {len(all_recs):,}")

    df = pd.DataFrame(all_recs)
    print(f"\nTotal rows: {len(df):,}")
    print(df["segment"].value_counts())
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "parsed.pkl")
    df.to_pickle(path)
    print("saved", path)


if __name__ == "__main__":
    main()
