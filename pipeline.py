"""
Taal Cafe – Master Data Pipeline
==================================
Detects new / changed source files, re-processes only what changed,
and always rebuilds the outputs.

Usage
-----
  python3 pipeline.py              # full run
  python3 pipeline.py --force      # re-process all files regardless of hash
  python3 pipeline.py --status     # show which files are new/changed
"""

import argparse
import hashlib
import json
import os
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
OUT  = BASE / "Dashboard Data"
OUT.mkdir(exist_ok=True)

MANIFEST_FILE = BASE / ".pipeline_manifest.json"
LOG_FILE      = OUT  / "pipeline_log.txt"

WATCH_DIRS = {
    "sales":    BASE / "Sales",
    "hours":    BASE / "Hours",
    "costs":    BASE / "Costs",
    "products": BASE / "Product Sales",
    "labor":    BASE / "Labor",
    "ecom":     BASE / "e-com data",
}

# ---------------------------------------------------------------------------
# Manifest helpers  (track which files have already been processed)
# ---------------------------------------------------------------------------
def file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE) as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict):
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)


def scan_source_files() -> dict[str, str]:
    """Return {relative_path: md5} for all source files in watched dirs."""
    result = {}
    for folder in WATCH_DIRS.values():
        for ext in ("*.xlsx", "*.csv", "*.pdf"):
            for p in sorted(folder.glob(ext)):
                rel = str(p.relative_to(BASE))
                result[rel] = file_hash(p)
    return result


def changed_files(force: bool = False) -> tuple[list[str], list[str], list[str]]:
    """Return (new, modified, removed) relative paths since last run."""
    current  = scan_source_files()
    previous = load_manifest().get("files", {})
    new      = [k for k in current if k not in previous]
    modified = [k for k in current if k in previous and current[k] != previous[k]]
    removed  = [k for k in previous if k not in current]
    if force:
        new = list(current.keys())
        modified, removed = [], []
    return new, modified, removed


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class Logger:
    def __init__(self):
        self._lines: list[str] = []

    def log(self, msg: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}" if msg else ""
        print(line)
        self._lines.append(line)

    def section(self, title: str):
        bar = "─" * (len(title) + 4)
        self.log()
        self.log(bar)
        self.log(f"  {title}")
        self.log(bar)

    def flush(self):
        with open(LOG_FILE, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Pipeline run: {datetime.now().isoformat()}\n")
            f.write('\n'.join(self._lines))
            f.write("\n")


log = Logger()


# ---------------------------------------------------------------------------
# STEP 1 – Parse DayReports
# ---------------------------------------------------------------------------
MONTH_MAP = {
    "January":1,"February":2,"March":3,"April":4,
    "May":5,"June":6,"July":7,"August":8,
    "September":9,"October":10,"November":11,"December":12,
}

SKIP_DAY = {
    "DayReport_2025-07-01_2025-07-31__generated_at_2025-07-31_17.38.xlsx"
}


def parse_dayreport(filepath: Path) -> pd.DataFrame:
    df = pd.read_excel(filepath, sheet_name="Sheet0", header=None)
    entity   = str(df.iloc[0, 1]).strip()
    raw_end  = str(df.iloc[2, 1])
    gen_date = str(df.iloc[3, 1])
    month_name = str(df.iloc[5, 0]).strip()
    month_num  = MONTH_MAP.get(month_name)
    year_m = re.search(r"\d{4}", raw_end)
    year   = int(year_m.group()) if year_m else None
    col_names = [str(c).replace("\n", " ").strip() for c in df.iloc[7].tolist()]
    data = df.iloc[8:].copy()
    data.columns = col_names
    data = data[pd.to_numeric(data["Day"], errors="coerce").notna()].copy()
    data["Day"] = pd.to_numeric(data["Day"]).astype(int)
    if month_num and year:
        data["date"] = pd.to_datetime(
            {"year": year, "month": month_num, "day": data["Day"]},
            errors="coerce",
        )
    else:
        data["date"] = pd.NaT
    data["entity"]      = entity
    data["source_file"] = filepath.name
    data["generated"]   = gen_date
    return data


def build_daily_summary():
    log.section("STEP 1 – Daily Summary (DayReports)")
    day_files = sorted((BASE / "Sales Retail").glob("DayReport_*.xlsx"))
    frames = []
    skipped = []
    for f in day_files:
        if f.name in SKIP_DAY:
            skipped.append(f.name)
            continue
        frames.append(parse_dayreport(f))
    if skipped:
        log.log(f"  Skipped duplicate: {skipped[0]}")
    if not frames:
        log.log("  No DayReport files found in Sales Retail – skipping.")
        pd.DataFrame().to_csv(OUT / "daily_summary.csv", index=False)
        return
    daily = pd.concat(frames, ignore_index=True)
    daily.columns = [str(c).replace("\n", " ").strip() for c in daily.columns]
    front = ["date", "entity", "Day"]
    rest  = [c for c in daily.columns if c not in front + ["source_file", "generated"]]
    daily = daily[front + rest + ["source_file", "generated"]]
    daily = daily.sort_values(["entity", "date"]).reset_index(drop=True)
    num_cols = daily.select_dtypes(include="number").columns
    daily[num_cols] = daily[num_cols].round(2)
    daily.to_csv(OUT / "daily_summary.csv", index=False)
    log.log(f"  daily_summary.csv → {len(daily)} rows, "
            f"{daily['entity'].nunique()} entities, "
            f"{daily['date'].min().date()} to {daily['date'].max().date()}")


# ---------------------------------------------------------------------------
# STEP 2 – Parse SalesReports (transactions)
# ---------------------------------------------------------------------------
def parse_salesreport(filepath: Path) -> pd.DataFrame:
    df = pd.read_excel(filepath, sheet_name="Sheet0", header=None)
    entity   = str(df.iloc[0, 1]).strip()
    gen_date = str(df.iloc[3, 1])
    col_names = None
    rows = []
    for i, row in df.iterrows():
        v0 = str(row[0]).strip()
        if v0 == "Invoice ID":
            col_names = [str(c).replace("\n", " ").strip() for c in row.tolist()]
        elif col_names and v0 not in ["", "nan", "Total", "Grand Total"] and "|" in v0:
            rows.append(row.tolist())
    if not rows or col_names is None:
        return pd.DataFrame()
    result = pd.DataFrame(rows, columns=col_names)
    result["entity"]      = entity
    result["source_file"] = filepath.name
    result["generated"]   = gen_date
    return result


def build_transactions():
    log.section("STEP 2 – Transactions (SalesReports)")
    sales_files = sorted((BASE / "Sales Retail").glob("SalesReport_*.xlsx"))
    if not sales_files:
        log.log("  No SalesReport files found in Sales Retail – skipping.")
        pd.DataFrame(columns=["date","Invoice ID","revenue_net_eur","revenue_incl_vat_eur",
                               "source_file"]).to_csv(OUT / "transactions.csv", index=False)
        return
    frames = [parse_salesreport(f) for f in sales_files]
    transactions = pd.concat(frames, ignore_index=True)
    transactions["Date started"] = pd.to_datetime(transactions["Date started"], errors="coerce")
    transactions["Date closed"]  = pd.to_datetime(transactions["Date closed"],  errors="coerce")
    transactions["date"] = transactions["Date started"].dt.normalize()
    before = len(transactions)
    transactions = transactions.drop_duplicates(subset=["Invoice ID"], keep="last").reset_index(drop=True)
    log.log(f"  Removed {before - len(transactions)} duplicate Invoice IDs")
    for c in ["Subtotal","Discount amount","Surcharge amount",
              "Net Amount VAT 9%","Tax Amount VAT 9%",
              "Net Amount VAT 21%","Tax Amount VAT 21%",
              "Net Amount VAT 0%","Tips amount","Quantity of products"]:
        if c in transactions.columns:
            transactions[c] = pd.to_numeric(transactions[c], errors="coerce").round(2)
    # Calculated revenue
    transactions["revenue_net_eur"] = (
        transactions["Net Amount VAT 9%"].fillna(0) +
        transactions["Net Amount VAT 21%"].fillna(0) +
        transactions["Net Amount VAT 0%"].fillna(0)
    ).round(2)
    transactions["revenue_incl_vat_eur"] = (
        transactions["Net Amount VAT 9%"].fillna(0)  + transactions["Tax Amount VAT 9%"].fillna(0) +
        transactions["Net Amount VAT 21%"].fillna(0) + transactions["Tax Amount VAT 21%"].fillna(0) +
        transactions["Net Amount VAT 0%"].fillna(0)
    ).round(2)
    front = ["date","Date started","Date closed","entity","Invoice ID","Ledger","Currency",
             "Subtotal","revenue_net_eur","revenue_incl_vat_eur",
             "Net Amount VAT 9%","Tax Amount VAT 9%","Net Amount VAT 21%","Tax Amount VAT 21%",
             "Net Amount VAT 0%","Tips amount","Quantity of products",
             "Table","Staff name","Receipt"]
    existing = [c for c in front if c in transactions.columns]
    rest = [c for c in transactions.columns if c not in existing + ["source_file","generated"]]
    transactions = transactions[existing + rest + ["source_file","generated"]]
    transactions = transactions.sort_values("Date started").reset_index(drop=True)
    transactions.to_csv(OUT / "transactions.csv", index=False)
    log.log(f"  transactions.csv → {len(transactions)} unique invoices, "
            f"{transactions['date'].min().date()} to {transactions['date'].max().date()}")


# ---------------------------------------------------------------------------
# STEP 3 – Parse labor hours
# ---------------------------------------------------------------------------
def build_labor_hours():
    log.section("STEP 3 – Labor Hours")
    hours_files = sorted((BASE / "Hours").glob("gewerkte-uren*.xlsx"))
    # Deduplicate by MD5 – keep unique files only
    seen_hashes = set()
    unique_files = []
    for f in hours_files:
        h = file_hash(f)
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_files.append(f)
    dupes = len(hours_files) - len(unique_files)
    if dupes:
        log.log(f"  Removed {dupes} byte-identical Hours file(s)")

    frames = []
    for f in unique_files:
        df = pd.read_excel(f, sheet_name="table", header=None)
        col_names = [str(c).strip() for c in df.iloc[16].tolist()]
        data = df.iloc[17:].copy()
        data.columns = col_names
        data = data.dropna(how="all")
        data["source_file"] = f.name
        frames.append(data)

    if not frames:
        log.log("  No Hours files found – skipping.")
        pd.DataFrame(columns=["date","employee","hours_decimal","estimated_cost_eur",
                               "source_file"]).to_csv(OUT / "labor_hours.csv", index=False)
        return

    labor = pd.concat(frames, ignore_index=True)
    labor.rename(columns={
        "first name":"first_name","last name":"last_name","▲ date":"date",
        "team name":"team","type":"shift_type","hours":"hours_worked",
        "start to end time":"shift_time","break time":"break_time","meals":"meals",
        "hourly wage":"hourly_wage","amount team members":"team_size","support ID":"support_id",
    }, inplace=True)

    labor["employee"] = labor["first_name"].str.strip() + " " + labor["last_name"].str.strip()
    labor["date"] = pd.to_datetime(labor["date"], format="%d/%m/%Y", errors="coerce")
    labor["hourly_wage_eur"] = pd.to_numeric(
        labor["hourly_wage"].astype(str).str.replace("€","").str.strip(), errors="coerce"
    )

    def hhmm_to_decimal(v):
        try:
            p = str(v).split(":")
            return round(int(p[0]) + int(p[1]) / 60, 2)
        except Exception:
            return None

    labor["hours_decimal"]      = labor["hours_worked"].apply(hhmm_to_decimal)
    labor["estimated_cost_eur"] = (labor["hours_decimal"] * labor["hourly_wage_eur"]).round(2)
    # Drop grand-total rows (no name)
    labor = labor[labor["first_name"].notna()].reset_index(drop=True)

    front = ["date","employee","first_name","last_name","team","shift_type","shift_time",
             "hours_worked","hours_decimal","break_time","hourly_wage","hourly_wage_eur",
             "estimated_cost_eur","team_size","meals","support_id"]
    existing = [c for c in front if c in labor.columns]
    rest = [c for c in labor.columns if c not in existing]
    labor = labor[existing + rest].sort_values("date").reset_index(drop=True)
    labor.to_csv(OUT / "labor_hours.csv", index=False)
    log.log(f"  labor_hours.csv → {len(labor)} shifts, "
            f"{labor['employee'].nunique()} employees, "
            f"{labor['date'].min().date()} to {labor['date'].max().date()}")


# ---------------------------------------------------------------------------
# STEP 4 – Parse general ledger (costs)
# ---------------------------------------------------------------------------
ENGLISH_COLS = {
    "Dagboek":"journal_code","Dagboeknaam":"journal_name","Datum":"date",
    "Omschrijving":"description","Factuurnummer":"invoice_number","Debet":"debit",
    "Credit":"credit","Saldo":"balance","Cumulatief":"cumulative","Btw-%":"vat_pct",
    "Btw-controle":"vat_check","Gemarkeerd":"flagged",
}

CATEGORY_MAP = {
    # ── Revenue ──────────────────────────────────────────────────────────────
    "8110": "Revenue – Kiosk",
    "8000": "Revenue – Retail",        "8010": "Revenue – Retail",
    "8100": "Revenue – Retail",        "8140": "Revenue – Retail",
    "8199": "Revenue – Retail",
    "8111": "Matcha Company – E-com",
    "8160": "Revenue – Export & EU",   "8170": "Revenue – Export & EU",
    "8260": "Revenue – Export & EU",   "8270": "Revenue – Export & EU",
    "8200": "Revenue – Services",      "8210": "Revenue – Services",
    # ── COGS ─────────────────────────────────────────────────────────────────
    "7001": "COGS – Food & Bev Kiosk", "7004": "COGS – Food & Bev Kiosk",
    "7020": "Matcha Company – Matcha",
    "7024": "COGS – Matcha",           "7026": "Matcha Company – Matcha",
    "7002": "COGS – Retail & Other",   "7003": "COGS – Retail & Other",
    "7010": "COGS – Retail & Other",   "7011": "COGS – Retail & Other",
    "7013": "COGS – Retail & Other",   "7021": "COGS – Retail & Other",
    "7022": "COGS – Retail & Other",   "7025": "COGS – Retail & Other",
    "4327": "COGS – Packaging",        "4328": "COGS – Packaging",
    "7023": "COGS – Packaging",
    "7990": "COGS – Inventory Adj.",
    # ── Labor ─────────────────────────────────────────────────────────────────
    "7100": "Labor – Freelancers",
    "1613": "Labor – Payroll",
    # ── Occupancy ────────────────────────────────────────────────────────────
    "4203": "Occupancy – Rent",
    "4210": "Occupancy – Utilities",
    "4207": "Occupancy – Maintenance", "4208": "Occupancy – Maintenance",
    "4153": "Occupancy – Depreciation",
    "4290": "Occupancy – Other",
    # ── SG&A ─────────────────────────────────────────────────────────────────
    "4400": "SG&A – Marketing",        "4403": "SG&A – Marketing",
    "4418": "SG&A – Marketing",
    "4405": "SG&A – Travel & Entertainment",
    "4406": "SG&A – Travel & Entertainment",
    "4606": "SG&A – Subscriptions",    "4611": "SG&A – Subscriptions",
    "4700": "SG&A – Professional Services",
    "4703": "SG&A – Professional Services",
    "4704": "SG&A – Professional Services",
    "7014": "SG&A – Professional Services",
    "4754": "SG&A – Payment Processing",
    "4755": "SG&A – Payment Processing",
    "4758": "SG&A – Payment Processing",
    "4601": "SG&A – Shipping",
    "4753": "SG&A – Bank Charges",
    "4302": "SG&A – Equipment & Repairs",
    "4303": "SG&A – Equipment & Repairs",
    "4310": "SG&A – Equipment & Repairs",
    "4159": "SG&A – Depreciation Inventaris",
    "4408": "SG&A – Other",  "4421": "SG&A – Other",  "4423": "SG&A – Other",
    "4508": "SG&A – Other",  "4510": "SG&A – Other",  "4609": "SG&A – Other",
    "4650": "SG&A – Other",  "4651": "SG&A – Other",  "4756": "SG&A – Other",
    "4757": "SG&A – Other",  "4798": "SG&A – Other",  "7980": "SG&A – Other",
    # ── Below the Line ───────────────────────────────────────────────────────
    "4705": "Matcha Company – Management Fee",
    "4957": "Matcha Company – Interest",
    "4943": "Matcha Company – Interest",
}

DASHBOARD_GROUP = {
    "Revenue – Kiosk":                     "Revenue",
    "Revenue – Retail":                    "Revenue",
    "Revenue – Online":                    "Revenue",
    "Revenue – Export & EU":               "Revenue",
    "Revenue – Services":                  "Revenue",
    "COGS – Food & Bev Kiosk":            "COGS",
    "COGS – Matcha":                       "COGS",
    "Matcha Company – E-com":              "Matcha Company",
    "Matcha Company – Matcha":             "Matcha Company",
    "COGS – Retail & Other":               "COGS",
    "COGS – Packaging":                    "COGS",
    "COGS – Inventory Adj.":               "COGS",
    "Labor – Freelancers":                 "Labor",
    "Labor – Payroll":                     "Labor",
    "Occupancy – Rent":                    "Occupancy",
    "Occupancy – Utilities":               "Occupancy",
    "Occupancy – Maintenance":             "Occupancy",
    "Occupancy – Depreciation":            "Occupancy",
    "Occupancy – Other":                   "Occupancy",
    "SG&A – Marketing":                    "SG&A",
    "SG&A – Travel & Entertainment":       "SG&A",
    "SG&A – Subscriptions":                "SG&A",
    "SG&A – Professional Services":        "SG&A",
    "SG&A – Payment Processing":           "SG&A",
    "SG&A – Shipping":                     "SG&A",
    "SG&A – Bank Charges":                 "SG&A",
    "SG&A – Equipment & Repairs":          "SG&A",
    "SG&A – Depreciation Inventaris":      "SG&A",
    "SG&A – Other":                        "SG&A",
    "Matcha Company – Management Fee":     "Matcha Company",
    "Matcha Company – Interest":           "Matcha Company",
    "Matcha Company – Packaging":          "Matcha Company",
}

PL_GROUPS = {"COGS", "Labor", "Occupancy", "SG&A", "Matcha Company"}


def parse_ledger_file(filepath: Path, year: int) -> pd.DataFrame:
    df = pd.read_excel(filepath, sheet_name="Sheet", header=None)
    col_names = df.iloc[2].tolist()
    records = []
    current_code = current_name = None
    for i, row in df.iterrows():
        v0 = str(row[0]).strip()
        if v0.startswith("Grootboekrekening:"):
            m = re.match(r"Grootboekrekening:\s*(\d+):\s*(.+?)\s*\(Aantal=\d+\)", v0)
            if m:
                current_code = m.group(1).strip()
                current_name = m.group(2).strip()
            continue
        if v0 in ["Dagboek","nan","","Total","Grand Total"] or v0.startswith("Grootboek"):
            continue
        try:
            int(float(v0))
        except (ValueError, TypeError):
            continue
        rec = {col_names[j]: row[j] for j in range(len(col_names))}
        rec["account_code"] = current_code
        rec["account_name"] = current_name
        rec["year_file"]    = year
        records.append(rec)
    return pd.DataFrame(records).rename(columns=ENGLISH_COLS)


def build_ledger():
    log.section("STEP 4 – General Ledger (Costs)")
    cost_files = sorted(f for f in (BASE / "Costs").glob("*.xlsx") if not f.name.startswith("~$"))

    # Parse each file and tag with a file_index (lower = earlier in sort order)
    frames = []
    file_ranges = []   # (file_index, filename, min_date, max_date, row_count)
    for idx, f in enumerate(cost_files):
        year_m = re.search(r"(\d{4})", f.name)
        year   = int(year_m.group(1)) if year_m else 0
        df = parse_ledger_file(f, year)
        df["_file_index"] = idx
        df["_source_name"] = f.name
        frames.append(df)
        log.log(f"  Parsed [{idx}]: {f.name}  ({len(df)} rows)")

    ledger = pd.concat(frames, ignore_index=True)
    ledger["date"]    = pd.to_datetime(ledger["date"], errors="coerce")
    ledger["month"]   = ledger["date"].dt.to_period("M").astype(str)
    ledger["debit"]   = pd.to_numeric(ledger["debit"],  errors="coerce").round(2)
    ledger["credit"]  = pd.to_numeric(ledger["credit"], errors="coerce").round(2)
    ledger["net_amount"] = (ledger["debit"] - ledger["credit"]).round(2)
    ledger["account_code"] = ledger["account_code"].astype(str)

    # ── Overlap detection & deduplication ────────────────────────────────────
    # For each file, compute its date range
    for idx, f in enumerate(cost_files):
        rows = ledger[ledger["_file_index"] == idx]
        if rows["date"].notna().any():
            file_ranges.append((idx, f.name,
                                rows["date"].min(), rows["date"].max(),
                                len(rows)))

    # Warn about overlapping files
    overlapping_months: dict[str, list[str]] = {}   # month → [filenames]
    for i, (idx_i, name_i, lo_i, hi_i, _) in enumerate(file_ranges):
        for idx_j, name_j, lo_j, hi_j, _ in file_ranges[i+1:]:
            if lo_j <= hi_i and lo_i <= hi_j:   # ranges overlap
                # find which months overlap
                overlap_start = max(lo_i, lo_j)
                overlap_end   = min(hi_i, hi_j)
                mo = pd.period_range(overlap_start, overlap_end, freq="M")
                for m in mo:
                    overlapping_months.setdefault(str(m), [])
                    if name_i not in overlapping_months[str(m)]:
                        overlapping_months[str(m)].append(name_i)
                    if name_j not in overlapping_months[str(m)]:
                        overlapping_months[str(m)].append(name_j)

    if overlapping_months:
        log.log(f"  ⚠ Overlapping months detected across files:")
        for mo, names in sorted(overlapping_months.items()):
            log.log(f"    {mo}: covered by {len(names)} files")

    # Dedup strategy: for each month that appears in multiple files,
    # keep only rows from the file with the widest date span (most rows in that month).
    # Tie-break: higher file_index (later in sorted order = more recent export).
    if overlapping_months:
        before = len(ledger)

        # For every month, pick the "winner" file_index.
        # Primary: most rows in that specific month (more rows = more complete export).
        # Tie-break: highest file_index (later in sorted order = more recent file).
        month_winner: dict[str, int] = {}
        for mo in ledger["month"].dropna().unique():
            mo_rows = ledger[ledger["month"] == mo]
            files_in_month = mo_rows["_file_index"].unique()
            if len(files_in_month) == 1:
                month_winner[mo] = files_in_month[0]
                continue
            best_idx  = -1
            best_count = -1
            for fi in files_in_month:
                count = (mo_rows["_file_index"] == fi).sum()
                if count > best_count or (count == best_count and fi > best_idx):
                    best_count = count
                    best_idx   = fi
            month_winner[mo] = best_idx

        ledger["_winner"] = ledger["month"].map(month_winner)
        dropped_months: dict[str, int] = {}
        keep_mask = ledger["_file_index"] == ledger["_winner"]
        dropped = ledger[~keep_mask]
        for mo, grp in dropped.groupby("month"):
            dropped_months[mo] = len(grp)
        ledger = ledger[keep_mask].copy()

        after = len(ledger)
        log.log(f"  Removed {before - after} duplicate rows from overlapping files:")
        for mo, cnt in sorted(dropped_months.items()):
            log.log(f"    {mo}: dropped {cnt} rows (kept file with most entries for this month)")
    else:
        log.log("  No overlapping files detected — no deduplication needed.")

    # ── Finish tagging ────────────────────────────────────────────────────────
    ledger["category"]        = ledger["account_code"].map(CATEGORY_MAP)
    ledger["dashboard_group"] = ledger["category"].map(DASHBOARD_GROUP)
    ledger["is_pl"]           = ledger["dashboard_group"].isin(PL_GROUPS)
    front = ["date","month","account_code","account_name","category","dashboard_group",
             "is_pl","description","invoice_number","debit","credit","net_amount",
             "journal_code","journal_name","vat_pct","year_file"]
    ledger = ledger[front].sort_values(["date","account_code"]).reset_index(drop=True)
    ledger.to_csv(OUT / "costs_ledger.csv", index=False)
    log.log(f"  costs_ledger.csv → {len(ledger)} entries, "
            f"{ledger['date'].min().date()} to {ledger['date'].max().date()}")


# ---------------------------------------------------------------------------
# STEP 5 – Monthly P&L
# ---------------------------------------------------------------------------
PL_LINES = [
    # ── REVENUE ──────────────────────────────────────────────────────────────
    ("8110", "Kiosk Sales",               "Revenue", "Kiosk",       -1),
    ("8000", "Retail Sales – High VAT",   "Revenue", "Retail",      -1),
    ("8010", "Retail Sales – Low VAT",    "Revenue", "Retail",      -1),
    ("8100", "Retail Sales – Product HV", "Revenue", "Retail",      -1),
    ("8140", "Retail Sales – Zero Rate",  "Revenue", "Retail",      -1),
    ("8199", "Retail Sales – Other",      "Revenue", "Retail",      -1),
    ("8111", "E-com Revenue",             "Matcha Company", "E-com", +1),
    ("8160", "Export (Outside EU)",       "Revenue", "Export & EU", -1),
    ("8170", "EU B2B Sales",              "Revenue", "Export & EU", -1),
    ("8260", "Service Export / EU",       "Revenue", "Export & EU", -1),
    ("8270", "Other Export / EU",         "Revenue", "Export & EU", -1),
    ("8200", "Services – High VAT",       "Revenue", "Services",    -1),
    ("8210", "Services – Low VAT",        "Revenue", "Services",    -1),
    # ── COGS – Food & Bev ────────────────────────────────────────────────────
    ("7001", "Food & Bev Kiosk (Low VAT)",  "COGS", "Food & Bev – Kiosk", +1),
    ("7004", "Food & Bev Kiosk (High VAT)", "COGS", "Food & Bev – Kiosk", +1),
    # ── COGS – Matcha ────────────────────────────────────────────────────────
    ("7024", "Matcha Tools Import",        "COGS", "Matcha", +1),
    # ── Matcha Company (moved from COGS) ──────────────────────────────────────
    ("7020", "Matcha Purchases (KG)",      "Matcha Company", "Matcha", +1),
    ("7026", "Matcha Retail Import",       "Matcha Company", "Matcha", +1),
    # ── COGS – Retail & Other Purchases ──────────────────────────────────────
    ("7002", "Retail Products NL",         "COGS", "Retail & Other", +1),
    ("7003", "Exempt Purchases",           "COGS", "Retail & Other", +1),
    ("7010", "Imports EU – Other",         "COGS", "Retail & Other", +1),
    ("7011", "Retail Products EU (HV)",    "COGS", "Retail & Other", +1),
    ("7013", "Imports EU – Overig",        "COGS", "Retail & Other", +1),
    ("7021", "Retail Products Outside EU", "COGS", "Retail & Other", +1),
    ("7022", "Imports Outside EU – Other", "COGS", "Retail & Other", +1),
    ("7025", "Small Equipment EU (COGS)",  "COGS", "Retail & Other", +1),
    # ── COGS – Packaging Materials ───────────────────────────────────────────
    ("4327", "Packaging Materials",        "COGS", "Packaging", +1),
    ("4328", "Packaging Materials EU",     "COGS", "Packaging", +1),
    ("7023", "Packaging Import (Ext EU)",  "COGS", "Packaging", +1),
    # ── COGS – Inventory Adjustment ──────────────────────────────────────────
    ("7990", "Inventory Adjustment",       "COGS", "Inventory Adj.", +1),
    # ── LABOR ────────────────────────────────────────────────────────────────
    ("7100", "Freelancers / Outsourced Work", "Labor", "Freelancers", +1),
    ("1613", "Payroll",                       "Labor", "Payroll",     +1),
    # ── OCCUPANCY ────────────────────────────────────────────────────────────
    ("4203", "Rent",                  "Occupancy", "Rent",              +1),
    ("4210", "Utilities (GWE)",       "Occupancy", "Utilities",         +1),
    ("4207", "Maintenance",           "Occupancy", "Maintenance",       +1),
    ("4208", "Cleaning",              "Occupancy", "Maintenance",       +1),
    ("4153", "Depreciation Fit-out",  "Occupancy", "Depreciation",      +1),
    ("4290", "Other Occupancy",       "Occupancy", "Other",             +1),
    # ── SG&A – Marketing & Advertising ───────────────────────────────────────
    ("4400", "Marketing & Advertising",   "SG&A", "Marketing", +1),
    ("4403", "Client Gifts",              "SG&A", "Marketing", +1),
    ("4418", "Advertising EU",            "SG&A", "Marketing", +1),
    # ── SG&A – Travel & Entertainment ────────────────────────────────────────
    ("4405", "Representation",            "SG&A", "Travel & Entertainment", +1),
    ("4406", "Travel & Accommodation",    "SG&A", "Travel & Entertainment", +1),
    # ── SG&A – Subscriptions ─────────────────────────────────────────────────
    ("4606", "Subscriptions",             "SG&A", "Subscriptions", +1),
    ("4611", "Subscriptions EU",          "SG&A", "Subscriptions", +1),
    # ── SG&A – Professional Services ─────────────────────────────────────────
    ("4700", "Accounting & Advisory",     "SG&A", "Professional Services", +1),
    ("4703", "Legal Costs",               "SG&A", "Professional Services", +1),
    ("4704", "Other Advisory",            "SG&A", "Professional Services", +1),
    ("7014", "Advisory Services EU",      "SG&A", "Professional Services", +1),
    # ── SG&A – Payment Processing ────────────────────────────────────────────
    ("4754", "Adyen Costs",               "SG&A", "Payment Processing", +1),
    ("4755", "PayPal Costs",              "SG&A", "Payment Processing", +1),
    ("4758", "Tebi / Other Payments",     "SG&A", "Payment Processing", +1),
    # ── SG&A – Shipping ──────────────────────────────────────────────────────
    ("4601", "Postage & Shipping",        "SG&A", "Shipping", +1),
    # ── SG&A – Bank Charges ──────────────────────────────────────────────────
    ("4753", "Bank Charges",              "SG&A", "Bank Charges", +1),
    # ── SG&A – Equipment & Repairs ───────────────────────────────────────────
    ("4302", "Equipment Leasing",         "SG&A", "Equipment", +1),
    ("4303", "Small Equipment Purchases", "SG&A", "Equipment", +1),
    ("4310", "Machine Repair",            "SG&A", "Equipment", +1),
    # ── SG&A – Depreciation Inventaris ───────────────────────────────────────
    ("4159", "Depreciation Inventaris",   "SG&A", "Depreciation", +1),
    # ── SG&A – Other ─────────────────────────────────────────────────────────
    ("4408", "Canteen Costs",             "SG&A", "Other SG&A", +1),
    ("4421", "Uniforms",                  "SG&A", "Other SG&A", +1),
    ("4423", "Other Sales Costs",         "SG&A", "Other SG&A", +1),
    ("4508", "Car Rental",                "SG&A", "Other SG&A", +1),
    ("4510", "Fines & Penalties",         "SG&A", "Other SG&A", +1),
    ("4609", "Other Admin Costs",         "SG&A", "Other SG&A", +1),
    ("4650", "Liability Insurance",       "SG&A", "Other SG&A", +1),
    ("4651", "Other Insurance",           "SG&A", "Other SG&A", +1),
    ("4756", "Payment Differences",       "SG&A", "Other SG&A", +1),
    ("4757", "Tax Fines",                 "SG&A", "Other SG&A", +1),
    ("4798", "Other Costs",               "SG&A", "Other SG&A", +1),
    ("7980", "COGS Payment Differences",  "SG&A", "Other SG&A", +1),
    # ── BELOW THE LINE ───────────────────────────────────────────────────────
    ("4705", "Management Fee",            "Matcha Company", "Management Fee", +1),
    ("4957", "Interest Expense",          "Matcha Company", "Interest",       +1),
    ("4943", "Interest Income",           "Matcha Company", "Interest",       -1),
]

# Account 7100 invoices dated 1st–7th are reallocated to the prior month (accrual matching)
REALLOCATE_ACCOUNTS = {"7100"}

HIGHLIGHT = {
    "TOTAL REVENUE", "TOTAL COGS", "GROSS PROFIT", "GROSS MARGIN %",
    "TOTAL LABOR", "TOTAL OCCUPANCY", "TOTAL SG&A",
    "EBIT", "EBIT MARGIN %", "EBITDA", "EBITDA MARGIN %",
    "EBT", "EBT MARGIN %", "TOTAL MATCHA COMPANY",
}


# ---------------------------------------------------------------------------
# STEP 4b – Parse Journaalposten (payroll journals) from Labor/
# ---------------------------------------------------------------------------
# Mapping: journaalpost account section → GL account code in PL_LINES
PAYROLL_SECTION_MAP = {
    "4000": "4003",   # Bruto lonen → Wages (Lonen)
    "4010": "4006",   # Vakantiegeld reservering → Holiday Pay
    "4100": "4020",   # Sociale lasten WG → Social Security Premiums
    "4200": "4030",   # Pensioenbijdrage WG → Pension Premiums
}
PAYROLL_ACCOUNT_NAMES = {
    "4003": "Wages (Lonen) [payroll est.]",
    "4006": "Holiday Pay (Vakantiegeld) [payroll est.]",
    "4020": "Social Security Premiums [payroll est.]",
    "4030": "Pension Premiums [payroll est.]",
}

def _parse_dutch_amount(s: str) -> float:
    """Convert Dutch amount string like '4.329,04' to float 4329.04."""
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def build_payroll_journal() -> pd.DataFrame:
    """Parse Journaalposten PDFs, return DataFrame with wage breakdown per month.
    Returns empty DataFrame if pdfplumber not available or no files found."""
    labor_dir = BASE / "Labor"
    pdf_files = sorted(labor_dir.glob("Journaalposten_*.pdf"))
    if not pdf_files:
        return pd.DataFrame()

    try:
        import pdfplumber
    except ImportError:
        log.log("  pdfplumber not installed – skipping payroll journal parsing.")
        return pd.DataFrame()

    # Regex to extract month from "Periodes:2026-1-M t/m 2026-1-M"
    period_re = re.compile(r"Periodes:(\d{4})-(\d{1,2})-")
    # Regex for "Totaal X" or "Totaal X Y" at end of a section
    totaal_re = re.compile(r"^Totaal\s+([\d.,]+)(?:\s+([\d.,]+))?$")

    rows = []
    for pdf_path in pdf_files:
        text_pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text_pages.append(page.extract_text() or "")
        full_text = "\n".join(text_pages)
        lines = full_text.split("\n")

        # Extract period → month string
        month_str = None
        for line in lines:
            m = period_re.search(line)
            if m:
                year, mon = int(m.group(1)), int(m.group(2))
                month_str = f"{year}-{mon:02d}"
                break
        if not month_str:
            log.log(f"  Could not determine month for {pdf_path.name} – skipping.")
            continue

        # Walk through sections collecting totals
        current_section = None
        section_amounts = {}
        for line in lines:
            line = line.strip()
            # Detect section header like "4000 | Bruto lonen"
            sec_match = re.match(r"^(4\d{3})\s*\|", line)
            if sec_match:
                current_section = sec_match.group(1)
            # Detect Totaal line
            if current_section and current_section in PAYROLL_SECTION_MAP:
                t = totaal_re.match(line)
                if t and current_section not in section_amounts:
                    # Take first (debit) amount
                    section_amounts[current_section] = _parse_dutch_amount(t.group(1))

        for jp_code, gl_code in PAYROLL_SECTION_MAP.items():
            rows.append({
                "month":        month_str,
                "jp_account":   jp_code,
                "gl_account":   gl_code,
                "account_name": PAYROLL_ACCOUNT_NAMES[gl_code],
                "amount":       section_amounts.get(jp_code, 0.0),
                "source_file":  pdf_path.name,
            })
        log.log(f"  {pdf_path.name} → month {month_str}: "
                + ", ".join(f"{PAYROLL_SECTION_MAP[k]}={section_amounts.get(k,0):,.2f}"
                            for k in PAYROLL_SECTION_MAP if k in section_amounts))

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(OUT / "payroll_journal.csv", index=False)
        log.log(f"  payroll_journal.csv → {len(df)} rows, months: {sorted(df['month'].unique())}")
    return df


def build_monthly_pl():
    log.section("STEP 5 – Monthly P&L")
    ledger = pd.read_csv(OUT / "costs_ledger.csv")
    ledger["date"]         = pd.to_datetime(ledger["date"], errors="coerce")
    ledger["account_code"] = ledger["account_code"].astype(str)
    ledger["net_amount"]   = pd.to_numeric(ledger["net_amount"], errors="coerce").fillna(0)

    # Reallocation: 7100/7101 days 1-7 → previous month
    mask = (
        ledger["account_code"].isin(REALLOCATE_ACCOUNTS) &
        (ledger["date"].dt.day <= 7)
    )
    ledger["accounting_date"] = ledger["date"].copy()
    ledger.loc[mask, "accounting_date"] = ledger.loc[mask, "date"].apply(
        lambda d: d - relativedelta(months=1)
    )
    ledger["accounting_month"] = ledger["accounting_date"].dt.to_period("M").astype(str)
    n_reallocated = mask.sum()
    log.log(f"  Reallocated {n_reallocated} outsourced-staff entries to prior month")

    all_months = sorted(m for m in ledger["accounting_month"].dropna().unique()
                        if m >= "2024-01")

    sort_order = {code: i for i, (code, *_) in enumerate(PL_LINES)}
    rows = []
    for code, label, section, subsection, sign in PL_LINES:
        sub     = ledger[ledger["account_code"] == code]
        monthly = sub.groupby("accounting_month")["net_amount"].sum()
        row = {"sort_order": sort_order[code], "section": section,
               "subsection": subsection, "account_code": code, "line_item": label}
        for m in all_months:
            row[m] = round(monthly.get(m, 0.0) * sign, 2)
        rows.append(row)

    pl = pd.DataFrame(rows).sort_values("sort_order").drop(columns="sort_order")
    mc = [c for c in pl.columns if c.startswith("20")]

    # ── Shanghai Senqi Packaging carve-out from account 7023 ─────────────────
    # Entries for Shanghai Senqi stay in costs_ledger as COGS – Packaging, but
    # we reclassify them to "Matcha Company" for P&L display purposes.
    senqi_mask = (
        (ledger["account_code"].astype(str) == "7023") &
        (ledger["is_pl"] == True) &
        ledger["description"].str.contains("Shanghai Senqi", case=False, na=False)
    )
    senqi_monthly = ledger[senqi_mask].groupby("accounting_month")["net_amount"].sum()
    if not senqi_monthly.empty:
        senqi_row = {
            "section": "Matcha Company", "subsection": "Packaging",
            "account_code": "7023s", "line_item": "Packaging"
        }
        for m in mc:
            senqi_row[m] = round(float(senqi_monthly.get(m, 0.0)), 2)
        pl = pd.concat([pl, pd.DataFrame([senqi_row])], ignore_index=True)
        # Subtract Shanghai Senqi amounts from the original 7023 COGS row
        idx_7023 = pl[pl["account_code"] == "7023"].index
        if len(idx_7023):
            for m in mc:
                pl.loc[idx_7023, m] = round(
                    float(pl.loc[idx_7023[0], m]) - float(senqi_monthly.get(m, 0.0)), 2
                )

    # ── Q4 2025 revenue reallocation ──────────────────────────────────────────
    # Accountant booked all Q4 2025 revenue to December. Redistribute Oct/Nov/Dec
    # proportionally to each month's total cost (COGS + Labor + Occupancy + SG&A).
    _q4 = [m for m in mc if m in ("2025-10", "2025-11", "2025-12")]
    if len(_q4) == 3:
        cost_mask = pl["section"].isin({"COGS", "Labor", "Occupancy", "SG&A"})
        q4_costs  = (pl.loc[cost_mask, _q4]
                     .apply(pd.to_numeric, errors="coerce").fillna(0).sum())
        rev_mask  = pl["section"] == "Revenue"
        q4_rev_total = (pl.loc[rev_mask, _q4]
                        .apply(pd.to_numeric, errors="coerce").fillna(0).sum().sum())
        total_q4_cost = q4_costs.sum()
        if total_q4_cost > 0 and q4_rev_total > 0:
            q4_actual    = (pl.loc[rev_mask, _q4]
                            .apply(pd.to_numeric, errors="coerce").fillna(0).sum())
            q4_allocated = (q4_costs / total_q4_cost * q4_rev_total).round(2)
            q4_adj       = (q4_allocated - q4_actual).round(2)
            adj_row = {"section": "Revenue", "subsection": "Q4 Adj.",
                       "account_code": "", "line_item": "Revenue Reallocation (Q4)"}
            for m in mc:
                adj_row[m] = float(q4_adj.get(m, 0.0)) if m in _q4 else 0.0
            pl = pd.concat([pl, pd.DataFrame([adj_row])], ignore_index=True)
            log.log(f"  Q4 revenue reallocation applied: "
                    + ", ".join(f"{m} {q4_adj[m]:+,.0f}" for m in _q4))

    # ── Transit revenue (unclosed periods) ───────────────────────────────────
    # When accounting is not yet closed, revenue sits in payment-clearing accounts
    # (Ontvangsten Adyen/Stripe/PayPal/Tebi) instead of 8xxx revenue GLs.
    # Detect AFTER all prior revenue adjustments (incl. Q4 reallocation) so we
    # compare transit against already-adjusted Revenue totals, avoiding double-count.
    _TRANSIT_ACCS = {"1204", "1205", "1206", "1207", "1208"}
    _TRANSIT_MIN  = 2000  # EUR minimum transit balance to consider

    transit_sub     = ledger[ledger["account_code"].isin(_TRANSIT_ACCS)]
    transit_monthly = transit_sub.groupby("accounting_month")["net_amount"].sum()

    # Revenue already in pl (including Q4 reallocation) per month
    _pl_rev_now = (pl[pl["section"] == "Revenue"][mc]
                   .apply(pd.to_numeric, errors="coerce").fillna(0).sum())

    transit_by_month = {}
    for _m, _v in transit_monthly.items():
        _transit_amt = round(float(-_v), 2)
        _rev_now     = float(_pl_rev_now.get(_m, 0))
        _total       = _transit_amt + max(_rev_now, 0)
        # Only flag as "unclosed" when transit dominates (> 75% of combined total)
        # — filters out reconciliation noise in closed months and Q4-reallocated months
        if _transit_amt > _TRANSIT_MIN and _total > 0 and _transit_amt / _total > 0.75:
            transit_by_month[_m] = _transit_amt

    if transit_by_month:
        tr_row = {"section": "Revenue", "subsection": "Transit",
                  "account_code": "", "line_item": "Transit Revenue (uncleared *)"}
        for m in mc:
            tr_row[m] = transit_by_month.get(m, 0.0)
        pl = pd.concat([pl, pd.DataFrame([tr_row])], ignore_index=True)
        log.log("  Transit revenue (unclosed periods): "
                + ", ".join(f"{m} €{v:,.0f}" for m, v in sorted(transit_by_month.items())))
    with open(OUT / "transit_months.json", "w") as _f:
        json.dump(sorted(transit_by_month.keys()), _f)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def subtotal(section_filter, label, section, subsection="— Total"):
        mask = (pl["section"].isin(section_filter) if isinstance(section_filter, list)
                else pl["section"] == section_filter)
        vals = pl.loc[mask, mc].apply(pd.to_numeric, errors="coerce").fillna(0).sum()
        return {"section": section, "subsection": subsection, "account_code": "",
                "line_item": label, **vals.round(2).to_dict()}

    def sga_sub(subsection_val, label):
        mask = (pl["section"] == "SG&A") & (pl["subsection"] == subsection_val)
        vals = pl.loc[mask, mc].apply(pd.to_numeric, errors="coerce").fillna(0).sum()
        return {"section": "SG&A", "subsection": "— Total", "account_code": "",
                "line_item": label, **vals.round(2).to_dict()}

    # ── Section totals ────────────────────────────────────────────────────────
    rev_t  = subtotal("Revenue",        "TOTAL REVENUE",   "Revenue")
    cogs_t = subtotal("COGS",           "TOTAL COGS",      "COGS")
    gp_v   = {m: round(rev_t[m] - cogs_t[m], 2) for m in mc}
    gm_v   = {m: (round(gp_v[m] / rev_t[m] * 100, 1) if rev_t.get(m, 0) else None) for m in mc}

    labor_t = subtotal("Labor",     "TOTAL LABOR",     "Labor")
    occ_t   = subtotal("Occupancy", "TOTAL OCCUPANCY", "Occupancy")
    sga_t   = subtotal("SG&A",      "TOTAL SG&A",      "SG&A")

    # SG&A sub-group totals
    sga_mkt_t  = sga_sub("Marketing",              "Marketing & Advertising")
    sga_trav_t = sga_sub("Travel & Entertainment", "Travel & Entertainment")
    sga_subs_t = sga_sub("Subscriptions",          "Subscriptions")
    sga_pro_t  = sga_sub("Professional Services",  "Professional Services")
    sga_pay_t  = sga_sub("Payment Processing",     "Payment Processing")
    sga_ship_t = sga_sub("Shipping",               "Shipping / Postage")
    sga_bank_t = sga_sub("Bank Charges",           "Bank Charges")
    sga_eq_t   = sga_sub("Equipment",              "Equipment & Repairs")
    sga_depr_t = sga_sub("Depreciation",           "Depreciation Inventaris")
    sga_oth_t  = sga_sub("Other SG&A",             "Other SG&A")

    # EBIT = GP - Labor - Occupancy - SG&A
    ebit_v     = {m: round(gp_v[m] - labor_t[m] - occ_t[m] - sga_t[m], 2) for m in mc}
    ebit_pct_v = {m: (round(ebit_v[m] / rev_t[m] * 100, 1) if rev_t.get(m, 0) else None)
                  for m in mc}

    # EBITDA = EBIT + depreciation add-back (4153 Fit-out + 4159 Inventaris)
    depr_codes = {"4153", "4159"}
    depr_add = pl.loc[pl["account_code"].isin(depr_codes), mc].apply(
        pd.to_numeric, errors="coerce"
    ).fillna(0).sum()
    ebitda_v     = {m: round(ebit_v[m] + float(depr_add.get(m, 0)), 2) for m in mc}
    ebitda_pct_v = {m: (round(ebitda_v[m] / rev_t[m] * 100, 1) if rev_t.get(m, 0) else None)
                    for m in mc}

    # EBT = EBIT − Matcha Company (Mgmt Fee + Interest + Matcha purchases + Packaging)
    matcha_co_t = subtotal("Matcha Company", "TOTAL MATCHA COMPANY", "Matcha Company")
    ebt_v       = {m: round(ebit_v[m] - matcha_co_t[m], 2) for m in mc}
    ebt_pct_v = {m: (round(ebt_v[m] / rev_t[m] * 100, 1) if rev_t.get(m, 0) else None)
                 for m in mc}

    blank   = {"section": "—", "subsection": "", "account_code": "", "line_item": "",
               **{m: None for m in mc}}

    def df_row(d): return pd.DataFrame([d])

    def sga_rows(subsection_val):
        return pl[(pl["section"] == "SG&A") & (pl["subsection"] == subsection_val)]

    pl_final = pd.concat([
        # ── Revenue ──────────────────────────────────────────────────────────
        pl[pl["section"] == "Revenue"],
        df_row(rev_t), df_row(blank),
        # ── COGS ─────────────────────────────────────────────────────────────
        pl[pl["section"] == "COGS"],
        df_row(cogs_t), df_row(blank),
        # ── Gross Profit ──────────────────────────────────────────────────────
        df_row({**{"section": "Gross Profit", "subsection": "— Derived",
                    "account_code": "", "line_item": "GROSS PROFIT"}, **gp_v}),
        df_row({**{"section": "Gross Profit", "subsection": "— Derived",
                    "account_code": "", "line_item": "GROSS MARGIN %"}, **gm_v}),
        df_row(blank),
        # ── Labor ─────────────────────────────────────────────────────────────
        pl[pl["section"] == "Labor"],
        df_row(labor_t), df_row(blank),
        # ── Occupancy ─────────────────────────────────────────────────────────
        pl[pl["section"] == "Occupancy"],
        df_row(occ_t), df_row(blank),
        # ── SG&A sub-groups ───────────────────────────────────────────────────
        sga_rows("Marketing"),              df_row(sga_mkt_t),
        sga_rows("Travel & Entertainment"), df_row(sga_trav_t),
        sga_rows("Subscriptions"),          df_row(sga_subs_t),
        sga_rows("Professional Services"),  df_row(sga_pro_t),
        sga_rows("Payment Processing"),     df_row(sga_pay_t),
        sga_rows("Shipping"),               df_row(sga_ship_t),
        sga_rows("Bank Charges"),           df_row(sga_bank_t),
        sga_rows("Equipment"),              df_row(sga_eq_t),
        sga_rows("Depreciation"),           df_row(sga_depr_t),
        sga_rows("Other SG&A"),             df_row(sga_oth_t),
        df_row(sga_t), df_row(blank),
        # ── EBIT ──────────────────────────────────────────────────────────────
        df_row({**{"section": "EBIT", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBIT"}, **ebit_v}),
        df_row({**{"section": "EBIT", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBIT MARGIN %"}, **ebit_pct_v}),
        df_row({**{"section": "EBITDA", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBITDA"}, **ebitda_v}),
        df_row({**{"section": "EBITDA", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBITDA MARGIN %"}, **ebitda_pct_v}),
        df_row(blank),
        # ── Matcha Company ────────────────────────────────────────────────────
        pl[pl["section"] == "Matcha Company"],
        df_row(matcha_co_t), df_row(blank),
        # ── EBT ───────────────────────────────────────────────────────────────
        df_row({**{"section": "EBT", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBT"}, **ebt_v}),
        df_row({**{"section": "EBT", "subsection": "— Derived",
                    "account_code": "", "line_item": "EBT MARGIN %"}, **ebt_pct_v}),
    ], ignore_index=True)

    pl_final["TOTAL"] = pl_final[mc].apply(
        lambda r: round(pd.to_numeric(r, errors="coerce").sum(skipna=True), 2), axis=1
    )
    pl_final.to_csv(OUT / "monthly_pl.csv", index=False)
    log.log(f"  monthly_pl.csv → {len(pl_final)} rows × {len(mc)} months")

    # Summary print
    kpi_rows = pl_final[pl_final["line_item"].isin(
        ["TOTAL REVENUE","TOTAL COGS","GROSS PROFIT","GROSS MARGIN %",
         "TOTAL LABOR","TOTAL OCCUPANCY","TOTAL SG&A","EBIT","EBIT MARGIN %","EBITDA",
         "TOTAL MATCHA COMPANY","EBT"]
    )]
    for _, row in kpi_rows.iterrows():
        vals = "  ".join(
            f"{row[m]:>8,.0f}" if isinstance(row[m], float) and "%" not in row["line_item"]
            else (f"{row[m]:>7.1f}%" if isinstance(row[m], float) else "     –  ")
            for m in mc
        )
        log.log(f"  {row['line_item']:<30} {vals}")


# ---------------------------------------------------------------------------
# STEP 6 – Monthly KPIs summary
# ---------------------------------------------------------------------------
def _safe_read(path, **kwargs):
    p = OUT / path
    if not p.exists() or p.stat().st_size < 5:
        return pd.DataFrame()
    return pd.read_csv(p, **kwargs)


def build_monthly_kpis():
    log.section("STEP 6 – Monthly KPIs")
    t = _safe_read("transactions.csv", low_memory=False)
    l = _safe_read("labor_hours.csv")
    k = _safe_read("kiosk_transactions.csv")
    c = pd.read_csv(OUT / "costs_ledger.csv")

    # Retail transactions (may be empty for Lera World)
    if not t.empty and "date" in t.columns:
        t["date"]  = pd.to_datetime(t["date"], errors="coerce")
        t["month"] = t["date"].dt.to_period("M")
        for col in ["Subtotal","revenue_net_eur","revenue_incl_vat_eur","Tips amount",
                    "Discount amount","Quantity of products"]:
            if col in t.columns:
                t[col] = pd.to_numeric(t[col], errors="coerce")
        tx_m = t.groupby("month").agg(
            num_transactions=("Invoice ID","count") if "Invoice ID" in t.columns
                else ("date","count"),
            revenue_gross_eur=("revenue_incl_vat_eur","sum"),
            revenue_net_eur=("revenue_net_eur","sum"),
        ).round(2)
        tx_m.index = tx_m.index.astype(str)
    else:
        tx_m = pd.DataFrame()

    # Kiosk transactions (primary channel)
    if not k.empty and "date" in k.columns:
        k["date"]       = pd.to_datetime(k["date"], errors="coerce")
        k["amount_eur"] = pd.to_numeric(k.get("amount_eur", 0), errors="coerce").fillna(0)
        k["month"]      = k["date"].dt.to_period("M").astype(str)
        kiosk_m = k.groupby("month").agg(
            kiosk_orders=("amount_eur","count"),
            kiosk_revenue_eur=("amount_eur","sum"),
        ).round(2)
        kiosk_m["kiosk_avg_ticket_eur"] = (
            kiosk_m["kiosk_revenue_eur"] / kiosk_m["kiosk_orders"]
        ).round(2)
    else:
        kiosk_m = pd.DataFrame()

    # Labor hours (may be empty)
    if not l.empty and "date" in l.columns:
        l["date"]  = pd.to_datetime(l["date"], errors="coerce")
        l          = l[l["date"].notna()]
        l["month"] = l["date"].dt.to_period("M").astype(str)
        for col in ["hours_decimal","estimated_cost_eur"]:
            if col in l.columns:
                l[col] = pd.to_numeric(l[col], errors="coerce")
        l_m = l.groupby("month").agg(
            total_shifts=("support_id","count") if "support_id" in l.columns else ("date","count"),
            total_hours=("hours_decimal","sum"),
            labor_cost_eur=("estimated_cost_eur","sum"),
            unique_staff=("employee","nunique"),
        ).round(2)
    else:
        l_m = pd.DataFrame()

    c["date"]  = pd.to_datetime(c["date"], errors="coerce")
    c["account_code"] = c["account_code"].astype(str)
    c["net_amount"] = pd.to_numeric(c["net_amount"], errors="coerce")
    c["month"] = c["date"].dt.to_period("M").astype(str)
    pl_c = c[c["is_pl"] == True]
    cost_pivot = pl_c.pivot_table(
        index="month", columns="dashboard_group", values="net_amount", aggfunc="sum", fill_value=0
    ).round(2)
    cost_pivot.columns = [
        "cost_" + col.lower().replace(" & ","_").replace(" ","_").replace("/","_")
        for col in cost_pivot.columns
    ]

    # Join all sources; cost_pivot is always present (from GL)
    bases = [df for df in [tx_m, kiosk_m, l_m] if not df.empty]
    if bases:
        monthly = bases[0]
        for other in bases[1:]:
            monthly = monthly.join(other, how="outer")
        monthly = monthly.join(cost_pivot, how="outer").reset_index()
    else:
        monthly = cost_pivot.reset_index()
        monthly.rename(columns={"index": "month"}, inplace=True)

    monthly.rename(columns={"month": "year_month"}, inplace=True)
    monthly = monthly[monthly["year_month"] != "NaT"].reset_index(drop=True)

    def _col(df, name):
        return df[name].fillna(0) if name in df.columns else pd.Series(0, index=df.index)

    if "cost_cogs" in monthly.columns:
        monthly["gross_profit_eur"] = (
            _col(monthly, "kiosk_revenue_eur") +
            _col(monthly, "revenue_gross_eur") -
            monthly["cost_cogs"].fillna(0)
        ).round(2)
    if "cost_labor" in monthly.columns:
        total_rev = (_col(monthly, "kiosk_revenue_eur") +
                     _col(monthly, "revenue_gross_eur"))
        monthly["labor_pct_of_revenue"] = (
            monthly["cost_labor"].fillna(0) / total_rev.replace(0, float("nan")) * 100
        ).round(1)

    monthly.to_csv(OUT / "monthly_kpis.csv", index=False)
    log.log(f"  monthly_kpis.csv → {len(monthly)} months × {len(monthly.columns)} KPIs")


# ---------------------------------------------------------------------------
# STEP 7 – Product Sales
# ---------------------------------------------------------------------------
def build_product_sales():
    log.section("STEP 7 – Product Sales")
    prod_dir = BASE / "Product sales"
    files = sorted(prod_dir.glob("ProductReport_*.csv"))
    if not files:
        log.log("  No ProductReport files found – skipping.")
        return

    frames = []
    for f in files:
        # Extract month from filename  e.g. ProductReport_2025-10-01_...
        m = re.search(r"(\d{4}-\d{2})-\d{2}_", f.name)
        month_str = m.group(1) if m else None
        df = pd.read_csv(f)
        df["month"] = month_str
        df["source_file"] = f.name
        frames.append(df)

    products = pd.concat(frames, ignore_index=True)

    # Normalise column names
    products.columns = [c.strip().replace(" ", "_").replace("/", "_") for c in products.columns]
    # Rename key columns to clean names
    rename = {
        "Grouped":              "category",
        "Name":                 "product_name",
        "GTIN":                 "gtin",
        "Items_sold":           "items_sold",
        "Net_AmountVAT_9%":     "net_revenue_9pct",
        "Gross_AmountVAT_9%":   "gross_revenue_9pct",
        "Net_AmountVAT_21%":    "net_revenue_21pct",
        "Gross_AmountVAT_21%":  "gross_revenue_21pct",
        "Net_AmountVAT_0%":     "net_revenue_0pct",
        "Total_buying_price":   "total_cost",
        "Margin":               "margin_eur",
        "Margin_%":             "margin_pct",
    }
    products.rename(columns={k: v for k, v in rename.items() if k in products.columns}, inplace=True)

    for col in ["items_sold", "net_revenue_9pct", "gross_revenue_9pct",
                "net_revenue_21pct", "gross_revenue_21pct", "net_revenue_0pct",
                "total_cost", "margin_eur", "margin_pct"]:
        if col in products.columns:
            products[col] = pd.to_numeric(products[col], errors="coerce").fillna(0)

    products["net_revenue_eur"] = (
        products.get("net_revenue_9pct", 0) +
        products.get("net_revenue_21pct", 0) +
        products.get("net_revenue_0pct", 0)
    ).round(2)
    products["gross_revenue_eur"] = (
        products.get("gross_revenue_9pct", 0) +
        products.get("gross_revenue_21pct", 0) +
        products.get("net_revenue_0pct", 0)
    ).round(2)

    products = products[products["category"].notna()].copy()
    products.to_csv(OUT / "product_sales.csv", index=False)
    log.log(f"  product_sales.csv → {len(products)} rows, "
            f"{products['category'].nunique()} categories, "
            f"{products['month'].nunique()} months")


# ---------------------------------------------------------------------------
# STEP 5b – Kiosk Sales (Onesix terminal)
# ---------------------------------------------------------------------------
def build_kiosk_sales():
    log.section("STEP 5b – Kiosk Sales (Onesix)")
    kiosk_file = BASE / "Sales Kiosk (Onesix)" / "lera_completed_orders.xlsx"
    if not kiosk_file.exists():
        log.log("  lera_completed_orders.xlsx not found – skipping kiosk data.")
        pd.DataFrame(columns=["date","order_number","amount_eur","payment_method",
                               "month","entity","source_file"]).to_csv(
            OUT / "kiosk_transactions.csv", index=False)
        return

    df = pd.read_excel(kiosk_file)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Normalise key columns (handle different export column names)
    rename_map = {
        "order_date": "date", "created_at": "date", "date_created": "date",
        "amount": "amount_eur", "total": "amount_eur", "order_total": "amount_eur", "grand_total": "amount_eur",
        "payment": "payment_method", "payment_type": "payment_method",
        "order_id": "order_number", "id": "order_number",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)

    if "date" not in df.columns:
        log.log("  ⚠ Could not identify date column – skipping kiosk data.")
        pd.DataFrame(columns=["date","order_number","amount_eur","payment_method",
                               "month","entity","source_file"]).to_csv(
            OUT / "kiosk_transactions.csv", index=False)
        return

    df["date"]       = pd.to_datetime(df["date"], errors="coerce")
    df["amount_eur"] = pd.to_numeric(df.get("amount_eur", pd.Series(dtype=float)), errors="coerce").fillna(0)
    df["month"]      = df["date"].dt.to_period("M").astype(str)
    df["entity"]     = "Lera World"
    df["source_file"]= kiosk_file.name
    if "order_number" not in df.columns:
        df["order_number"] = range(len(df))
    if "payment_method" not in df.columns:
        df["payment_method"] = "unknown"

    out_cols = ["date","order_number","amount_eur","payment_method","month","entity","source_file"]
    out_cols = [c for c in out_cols if c in df.columns]
    df = df[out_cols].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df.to_csv(OUT / "kiosk_transactions.csv", index=False)
    total = df["amount_eur"].sum()
    n_months = df["month"].nunique()
    log.log(f"  kiosk_transactions.csv → {len(df):,} orders, "
            f"{df['date'].min().date()} to {df['date'].max().date()}, "
            f"€{total:,.0f} total")
    log.log(f"  {n_months} months of kiosk data, "
            f"avg monthly revenue €{total/n_months:,.0f}")


# ---------------------------------------------------------------------------
# STEP 8 – E-commerce Sales (Shopify)
# ---------------------------------------------------------------------------
def build_ecom_sales():
    log.section("STEP 8 – E-commerce Sales (Shopify)")
    ecom_dir = BASE / "e-com data"
    files = sorted(f for f in ecom_dir.glob("*.csv") if not f.name.startswith("."))
    if not files:
        log.log("  No e-com CSV files found – skipping.")
        pd.DataFrame().to_csv(OUT / "ecom_sales.csv", index=False)
        return

    frames = [pd.read_csv(f) for f in files]
    raw = pd.concat(frames, ignore_index=True)

    raw.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]
    raw["month"] = pd.to_datetime(raw["month"], errors="coerce").dt.to_period("M").astype(str)

    num_cols = ["orders", "gross_sales", "discounts", "returns",
                "net_sales", "shipping_charges", "taxes", "total_sales"]
    for c in num_cols:
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0)

    raw.to_csv(OUT / "ecom_sales.csv", index=False)

    monthly = raw.groupby("month").agg(
        orders=("orders", "sum"),
        gross_sales=("gross_sales", "sum"),
        net_sales=("net_sales", "sum"),
        total_sales=("total_sales", "sum"),
        countries=("billing_country", "nunique"),
    ).reset_index()

    log.log(f"  ecom_sales.csv → {len(raw)} rows, {raw['month'].nunique()} months, "
            f"{raw['billing_country'].nunique()} countries")
    log.log(f"  Total net sales: €{raw['net_sales'].sum():,.0f}, "
            f"{int(raw['orders'].sum())} orders")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run(force: bool = False):
    log.log(f"Lera World Pipeline  –  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    new, modified, removed = changed_files(force=force)

    if not force and not new and not modified and not removed:
        log.log("No source file changes detected. Use --force to reprocess anyway.")
        return False

    if new:
        log.log(f"New files     ({len(new)}): " + ", ".join(Path(f).name for f in new))
    if modified:
        log.log(f"Modified files ({len(modified)}): " + ", ".join(Path(f).name for f in modified))
    if removed:
        log.log(f"Removed files  ({len(removed)}): " + ", ".join(Path(f).name for f in removed))

    try:
        build_daily_summary()
        build_transactions()
        build_kiosk_sales()
        build_labor_hours()
        build_ledger()
        build_monthly_pl()
        build_monthly_kpis()
        build_product_sales()
        build_ecom_sales()
    except Exception as e:
        log.log(f"\n ERROR: {e}")
        import traceback
        log.log(traceback.format_exc())
        log.flush()
        return False

    # Update manifest
    current_files = scan_source_files()
    manifest = {"last_run": datetime.now().isoformat(), "files": current_files}
    save_manifest(manifest)
    log.log(f"\nDone. Manifest updated with {len(current_files)} source files.")
    log.flush()
    return True


def status():
    new, modified, removed = changed_files()
    if not new and not modified and not removed:
        print("All outputs are up to date.")
        return
    if new:
        print(f"NEW ({len(new)}):")
        for f in new: print(f"  + {f}")
    if modified:
        print(f"MODIFIED ({len(modified)}):")
        for f in modified: print(f"  ~ {f}")
    if removed:
        print(f"REMOVED ({len(removed)}):")
        for f in removed: print(f"  - {f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Taal Cafe data pipeline")
    parser.add_argument("--force",  action="store_true", help="Reprocess all files")
    parser.add_argument("--status", action="store_true", help="Show changed files only")
    args = parser.parse_args()

    if args.status:
        status()
    else:
        success = run(force=args.force)
        sys.exit(0 if success else 1)
