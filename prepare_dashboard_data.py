"""
Taal Cafe – Dashboard Data Preparation
=======================================
Reads raw Excel exports from Sales/ and Hours/, removes duplicates,
and writes three clean CSVs to Dashboard Data/ ready for dashboard use.

Outputs
-------
  Dashboard Data/daily_summary.csv      – Daily totals from DayReport files
  Dashboard Data/transactions.csv       – Individual sales from SalesReport files
  Dashboard Data/labor_hours.csv        – Employee shifts from Hours files
  Dashboard Data/data_notes.txt         – Notes on duplicates and gaps found
"""

import pandas as pd
import glob
import re
import os
import warnings as _warnings

_warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(BASE, "Dashboard Data")
os.makedirs(OUT, exist_ok=True)

notes = []

MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

# =============================================================================
# 1. DAILY SUMMARY  (DayReport_*.xlsx)
# =============================================================================
notes.append("=" * 60)
notes.append("DAILY SUMMARY (DayReport files)")
notes.append("=" * 60)

# Duplicate decision: two versions of July 2025 for Everybody Taal.
# The later-generated file (2025-10-09) has updated Day-31 figures
# (652.19 vs 628.63 total card) – using the more-complete version.
SKIP_DAY = {
    "DayReport_2025-07-01_2025-07-31__generated_at_2025-07-31_17.38.xlsx"
}
notes.append(
    "DUPLICATE REMOVED: DayReport July 2025 exists in two versions.\n"
    "  Kept   : DayReport_2025-07-01_2025-07-31__generated_at_2025-10-09_12.06.xlsx\n"
    "  Removed: DayReport_2025-07-01_2025-07-31__generated_at_2025-07-31_17.38.xlsx\n"
    "  Reason : Later-generated file contains updated Day-31 figures (+23.56 revenue)."
)


def parse_dayreport(filepath):
    df = pd.read_excel(filepath, sheet_name="Sheet0", header=None)
    entity   = str(df.iloc[0, 1]).strip()
    raw_end  = str(df.iloc[2, 1])
    gen_date = str(df.iloc[3, 1])

    # Month name is always in row 5, col 0
    month_name = str(df.iloc[5, 0]).strip()
    month_num  = MONTH_MAP.get(month_name)

    # Year from end-date cell
    year_m = re.search(r"\d{4}", raw_end)
    year   = int(year_m.group()) if year_m else None

    # Column headers at row 7
    col_names = [str(c).replace("\n", " ").strip() for c in df.iloc[7].tolist()]
    data = df.iloc[8:].copy()
    data.columns = col_names

    # Keep only numeric Day rows
    data = data[pd.to_numeric(data["Day"], errors="coerce").notna()].copy()
    data["Day"] = pd.to_numeric(data["Day"]).astype(int)

    # Build proper date column
    if month_num and year:
        data["date"] = pd.to_datetime(
            {"year": year, "month": month_num, "day": data["Day"]},
            errors="coerce",
        )
    else:
        data["date"] = pd.NaT

    data["entity"]      = entity
    data["source_file"] = os.path.basename(filepath)
    data["generated"]   = gen_date
    return data


day_frames = []
for f in sorted(glob.glob(os.path.join(BASE, "Sales", "DayReport_*.xlsx"))):
    fname = os.path.basename(f)
    if fname in SKIP_DAY:
        continue
    day_frames.append(parse_dayreport(f))

daily = pd.concat(day_frames, ignore_index=True)

# Move key columns to front
front = ["date", "entity", "Day"]
rest  = [c for c in daily.columns if c not in front + ["source_file", "generated"]]
daily = daily[front + rest + ["source_file", "generated"]]
daily = daily.sort_values(["entity", "date"]).reset_index(drop=True)

# Round all numeric columns to 2dp
num_cols = daily.select_dtypes(include="number").columns
daily[num_cols] = daily[num_cols].round(2)

notes.append(
    f"\nDaily summary: {len(daily)} rows across "
    f"{daily['entity'].nunique()} entities, "
    f"{daily['date'].min().date()} to {daily['date'].max().date()}"
)
notes.append("Entities: " + ", ".join(sorted(daily["entity"].unique())))
notes.append(
    "Note: 'Everybody Taal' and 'LERA_WORLD_BV' are different business\n"
    "  registrations; 'Taal Coffee' is the trading name used from Mar 2026.\n"
    "  Filter by 'entity' column to compare like-for-like periods."
)
# Coverage gaps
day_coverage = daily.groupby("entity")["date"].agg(["min", "max", "count"])
notes.append("\nCoverage per entity:\n" + day_coverage.to_string())

daily.to_csv(os.path.join(OUT, "daily_summary.csv"), index=False)
print(f"[1/3] daily_summary.csv  → {len(daily)} rows")


# =============================================================================
# 2. TRANSACTIONS  (SalesReport_*.xlsx)
# =============================================================================
notes.append("\n" + "=" * 60)
notes.append("TRANSACTIONS (SalesReport files)")
notes.append("=" * 60)

INVOICE_COL = "Invoice ID"

def parse_salesreport(filepath):
    """
    SalesReport structure:
      rows 0-3  : metadata (entity, start, end, generation date)
      row 4     : blank
      row 5,10,…: section headers "YYYY-MM-DD | StaffName"  (not data)
      row 6,11,…: column headers  "Invoice ID | Ledger | ..."
      row 7,12,…: data rows
      'Total' rows : skip
    The header row repeats for each date section – we detect it by checking
    whether col0 looks like an Invoice ID (contains '|').
    """
    df = pd.read_excel(filepath, sheet_name="Sheet0", header=None)
    entity   = str(df.iloc[0, 1]).strip()
    gen_date = str(df.iloc[3, 1])

    # The column-header row pattern: col0 == "Invoice ID"
    # Collect all data rows (skip header / section / total rows)
    col_names = None
    rows = []
    for i, row in df.iterrows():
        v0 = str(row[0]).strip()
        if v0 == INVOICE_COL:
            col_names = [str(c).replace("\n", " ").strip() for c in row.tolist()]
        elif col_names and v0 not in ["", "nan", "Total", "Grand Total"] \
                and "|" in v0:
            rows.append(row.tolist())

    if not rows or col_names is None:
        return pd.DataFrame()

    result = pd.DataFrame(rows, columns=col_names)
    result["entity"]      = entity
    result["source_file"] = os.path.basename(filepath)
    result["generated"]   = gen_date
    return result


tx_frames = []
for f in sorted(glob.glob(os.path.join(BASE, "Sales", "SalesReport_*.xlsx"))):
    tx_frames.append(parse_salesreport(f))

transactions = pd.concat(tx_frames, ignore_index=True)

# Normalise date columns
transactions["Date started"] = pd.to_datetime(
    transactions["Date started"], errors="coerce"
)
transactions["Date closed"] = pd.to_datetime(
    transactions["Date closed"], errors="coerce"
)
transactions["date"] = transactions["Date started"].dt.normalize()

# Deduplicate by Invoice ID (keep last occurrence = most recently generated file)
before = len(transactions)
transactions = transactions.drop_duplicates(
    subset=[INVOICE_COL], keep="last"
).reset_index(drop=True)
after = len(transactions)
dup_count = before - after
notes.append(
    f"Deduplication: removed {dup_count} duplicate invoice rows "
    f"(same Invoice ID appearing in overlapping monthly exports)."
)

# Numeric conversions
num_tx = ["Subtotal", "Discount amount", "Surcharge amount",
          "Net Amount VAT 9%", "Tax Amount VAT 9%",
          "Net Amount VAT 21%", "Tax Amount VAT 21%",
          "Net Amount VAT 0%",
          "Total Revenue excl. VAT", "Total Revenue incl. VAT",
          "Gift card sales", "Tips amount", "Quantity of products"]
for c in num_tx:
    if c in transactions.columns:
        transactions[c] = pd.to_numeric(transactions[c], errors="coerce").round(2)

# Front columns
front_tx = ["date", "Date started", "Date closed", "entity",
            INVOICE_COL, "Ledger", "Currency",
            "Subtotal", "Discount amount", "Surcharge amount",
            "Net Amount VAT 9%", "Tax Amount VAT 9%",
            "Net Amount VAT 21%", "Tax Amount VAT 21%",
            "Net Amount VAT 0%",
            "Total Revenue excl. VAT", "Total Revenue incl. VAT",
            "Tips amount", "Quantity of products",
            "Table", "Staff name", "Receipt"]
rest_tx = [c for c in transactions.columns
           if c not in front_tx + ["source_file", "generated"]]
existing_front = [c for c in front_tx if c in transactions.columns]
transactions = transactions[
    existing_front + rest_tx + ["source_file", "generated"]
].sort_values("Date started").reset_index(drop=True)

notes.append(
    f"\nTransactions: {len(transactions)} unique invoices, "
    f"{transactions['date'].min().date()} to {transactions['date'].max().date()}"
)
notes.append(
    "Staff column note: many rows have no Staff name – likely counter sales\n"
    "  or self-service tickets not assigned to a barista."
)

transactions.to_csv(os.path.join(OUT, "transactions.csv"), index=False)
print(f"[2/3] transactions.csv   → {len(transactions)} rows")


# =============================================================================
# 3. LABOR HOURS  (gewerkte-uren*.xlsx)
# =============================================================================
notes.append("\n" + "=" * 60)
notes.append("LABOR HOURS (gewerkte-uren files)")
notes.append("=" * 60)

hours_files = sorted(glob.glob(
    os.path.join(BASE, "Hours", "gewerkte-uren*.xlsx")
))
notes.append(f"Files found: {len(hours_files)}")

# Both files are byte-for-byte identical (MD5: 647789d4c400fe8dabbd2ae30340772e)
# Use only the first one
notes.append(
    "DUPLICATE REMOVED: Both Hours files are byte-for-byte identical.\n"
    "  Kept   : " + os.path.basename(hours_files[0]) + "\n"
    "  Removed: " + os.path.basename(hours_files[1])
)

def parse_hours(filepath):
    df = pd.read_excel(filepath, sheet_name="table", header=None)
    # Header is at row 16 (0-indexed)
    col_names = [str(c).strip() for c in df.iloc[16].tolist()]
    data = df.iloc[17:].copy()
    data.columns = col_names
    data = data.dropna(how="all").reset_index(drop=True)
    data["source_file"] = os.path.basename(filepath)
    return data

labor = parse_hours(hours_files[0])

# Rename Dutch/ambiguous columns to English
labor.rename(columns={
    "first name":          "first_name",
    "last name":           "last_name",
    "▲ date":              "date",
    "team name":           "team",
    "type":                "shift_type",
    "hours":               "hours_worked",
    "start to end time":   "shift_time",
    "break time":          "break_time",
    "meals":               "meals",
    "hourly wage":         "hourly_wage",
    "amount team members": "team_size",
    "support ID":          "support_id",
}, inplace=True)

# Clean full name column
labor["employee"] = labor["first_name"].str.strip() + " " + labor["last_name"].str.strip()

# Parse date
labor["date"] = pd.to_datetime(labor["date"], format="%d/%m/%Y", errors="coerce")

# Parse hourly_wage (remove € symbol)
labor["hourly_wage_eur"] = (
    labor["hourly_wage"].astype(str).str.replace("€", "").str.strip()
)
labor["hourly_wage_eur"] = pd.to_numeric(labor["hourly_wage_eur"], errors="coerce")

# Parse hours_worked as decimal hours (format HH:MM)
def hhmm_to_decimal(v):
    try:
        parts = str(v).split(":")
        return int(parts[0]) + int(parts[1]) / 60
    except Exception:
        return None

labor["hours_decimal"] = labor["hours_worked"].apply(hhmm_to_decimal).round(2)

# Estimated labor cost
labor["estimated_cost_eur"] = (
    labor["hours_decimal"] * labor["hourly_wage_eur"]
).round(2)

# Reorder columns
front_hr = ["date", "employee", "first_name", "last_name",
            "team", "shift_type", "shift_time",
            "hours_worked", "hours_decimal", "break_time",
            "hourly_wage", "hourly_wage_eur", "estimated_cost_eur",
            "team_size", "meals", "support_id"]
existing_hr = [c for c in front_hr if c in labor.columns]
rest_hr = [c for c in labor.columns if c not in existing_hr]
labor = labor[existing_hr + rest_hr].sort_values("date").reset_index(drop=True)

notes.append(
    f"\nLabor hours: {len(labor)} approved shifts, "
    f"{labor['date'].min().date()} to {labor['date'].max().date()}"
)
notes.append(f"Employees: {labor['employee'].nunique()} unique staff members")
notes.append(
    "Coverage note: 21 shifts for Alex Fedorova were NOT approved at time\n"
    "  of export and are excluded from this file (per source file warning)."
)

labor.to_csv(os.path.join(OUT, "labor_hours.csv"), index=False)
print(f"[3/3] labor_hours.csv    → {len(labor)} rows")


# =============================================================================
# 4. WRITE NOTES FILE
# =============================================================================
with open(os.path.join(OUT, "data_notes.txt"), "w") as fh:
    fh.write("Taal Cafe – Data Preparation Notes\n")
    fh.write("Generated: 2026-04-01\n\n")
    fh.write("\n".join(notes))

print("\nAll done. Files written to 'Dashboard Data/'")
print("  daily_summary.csv  – daily revenue & KPIs per entity")
print("  transactions.csv   – individual sales (deduplicated by Invoice ID)")
print("  labor_hours.csv    – employee shifts with estimated costs")
print("  data_notes.txt     – duplicate removal log & data gaps")
