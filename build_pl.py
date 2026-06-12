"""
Taal Cafe – Monthly P&L Builder
================================
Sources : Dashboard Data/costs_ledger.csv
Output  : Dashboard Data/monthly_pl.csv   (tidy, one row per account per month)
          Dashboard Data/monthly_pl_wide.csv  (wide format, accounts as rows, months as columns)

Reallocation rule
-----------------
Kosten uitbesteed werk (7100) and (Tips) (7101) entries falling in days 1–7
of month M are shifted to month M-1, because freelancers invoice for the
previous month in the first week of the following month.
"""

import pandas as pd
from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------------------------
# P&L line-item definitions  (account_code → label, section, subsection, sign)
# sign: +1  → value is a COST (debit-driven, show as positive spend)
#       -1  → value is REVENUE (credit-driven, net_amount is negative, show positive)
# ---------------------------------------------------------------------------
PL_LINES = [
    # REVENUE ----------------------------------------------------------------
    ("8110", "Revenue – Low VAT (Bar)", "Revenue", "Revenue", -1),
    ("8100", "Revenue – High VAT (Bar)", "Revenue", "Revenue", -1),
    ("8200", "Revenue – Services", "Revenue", "Revenue", -1),
    ("8240", "Revenue – Tips (Zero Rate)", "Revenue", "Revenue", -1),
    # COGS -------------------------------------------------------------------
    ("7001", "Drinks Purchases (Bar)", "COGS", "Purchases", +1),
    ("7004", "Pastry / Food Purchases (Bar)", "COGS", "Purchases", +1),
    ("7005", "Packaging – COGS", "COGS", "Purchases", +1),
    ("7006", "Drinks Purchases (Retail)", "COGS", "Purchases", +1),
    ("7007", "Food Purchases (Kitchen)", "COGS", "Purchases", +1),
    ("7010", "Imports EU – Low VAT", "COGS", "Imports", +1),
    ("7011", "Imports EU – High VAT", "COGS", "Imports", +1),
    ("7020", "Imports non-EU – Low VAT", "COGS", "Imports", +1),
    ("7021", "Imports non-EU – High VAT", "COGS", "Imports", +1),
    ("7022", "Imports non-EU – Other", "COGS", "Imports", +1),
    ("7002", "Retail Purchases – High VAT", "COGS", "Purchases", +1),
    # 7101 (Tips passed to outsourced staff) excluded: tips are netted out of revenue (8240)
    ("7980", "COGS Payment Differences", "COGS", "Other", +1),
    # LABOR – OUTSOURCED (kept separate so Total Wages & HR = direct payroll only)
    ("7100", "Outsourced Staff (reallocated)", "Labor", "Outsourced Work", +1),
    # WAGES & HR (direct payroll) ---------------------------------------------
    ("4003", "Wages (Lonen)", "Wages & HR", "Direct Wages", +1),
    ("4006", "Holiday Pay (Vakantiegeld)", "Wages & HR", "Direct Wages", +1),
    ("4020", "Social Security Premiums", "Wages & HR", "Employer Costs", +1),
    ("4030", "Pension Premiums", "Wages & HR", "Employer Costs", +1),
    # OCCUPANCY --------------------------------------------------------------
    ("4203", "Rent", "Occupancy", "Rent", +1),
    ("4207", "Building Maintenance", "Occupancy", "Maintenance", +1),
    ("4208", "Cleaning", "Occupancy", "Maintenance", +1),
    ("4210", "Gas / Water / Electricity", "Occupancy", "Utilities", +1),
    ("4290", "Other Occupancy Costs", "Occupancy", "Other", +1),
    # EQUIPMENT & SUPPLIES ---------------------------------------------------
    ("4303", "Small Equipment Purchases", "Equipment & Supplies", "Equipment", +1),
    ("4310", "Machine Repair & Maintenance", "Equipment & Supplies", "Equipment", +1),
    ("4327", "Packaging Materials", "Equipment & Supplies", "Supplies", +1),
    ("3090", "Packaging (Asset)", "Equipment & Supplies", "Supplies", +1),
    # MARKETING --------------------------------------------------------------
    ("4400", "Advertising", "Marketing", "Marketing", +1),
    ("4403", "Client Gifts", "Marketing", "Marketing", +1),
    ("4405", "Representation", "Marketing", "Marketing", +1),
    # TECHNOLOGY -------------------------------------------------------------
    ("4410", "POS & Payment System Costs", "Technology", "Technology", +1),
    # ADMIN ------------------------------------------------------------------
    ("4509", "Mileage Allowance", "Admin & Other", "Travel", +1),
    ("4518", "Parking", "Admin & Other", "Travel", +1),
    ("4601", "Postage & Courier", "Admin & Other", "Admin", +1),
    ("4605", "Office Equipment", "Admin & Other", "Admin", +1),
    ("4606", "Subscriptions", "Admin & Other", "Admin", +1),
    ("4650", "Insurance", "Admin & Other", "Insurance", +1),
    # PROFESSIONAL SERVICES --------------------------------------------------
    ("4700", "Accounting & Advisory", "Professional Services", "Professional", +1),
    ("4703", "Legal Costs", "Professional Services", "Professional", +1),
    ("4704", "Other Advisory", "Professional Services", "Professional", +1),
    # FINANCE ----------------------------------------------------------------
    ("4753", "Bank Charges", "Finance", "Finance", +1),
    ("4756", "Payment Differences", "Finance", "Finance", +1),
    # DEPRECIATION (below EBITDA) --------------------------------------------
    ("4103", "Goodwill Amortisation", "Depreciation", "Depreciation", +1),
    # INTEREST ---------------------------------------------------------------
    ("4957", "Interest Expense", "Interest", "Interest", +1),
]

REALLOCATE_ACCOUNTS = {"7100", "7101"}

# ---------------------------------------------------------------------------
# Load ledger
# ---------------------------------------------------------------------------
ledger = pd.read_csv("Dashboard Data/costs_ledger.csv")
ledger["date"] = pd.to_datetime(ledger["date"], errors="coerce")
ledger["account_code"] = ledger["account_code"].astype(str)
ledger["net_amount"] = pd.to_numeric(ledger["net_amount"], errors="coerce").fillna(0)

# ---------------------------------------------------------------------------
# Reallocation: 7100/7101 entries in days 1–7 → shift to previous month
# ---------------------------------------------------------------------------
mask_realloc = (
    ledger["account_code"].isin(REALLOCATE_ACCOUNTS) &
    (ledger["date"].dt.day <= 7)
)
ledger["accounting_date"] = ledger["date"].copy()
ledger.loc[mask_realloc, "accounting_date"] = (
    ledger.loc[mask_realloc, "date"].apply(
        lambda d: d - relativedelta(months=1)
    )
)
ledger["accounting_month"] = ledger["accounting_date"].dt.to_period("M").astype(str)

reallocated_count = mask_realloc.sum()
reallocated_eur   = ledger.loc[mask_realloc, "net_amount"].sum()
print(f"Reallocated {reallocated_count} 7100/7101 entries ({reallocated_eur:,.2f} EUR) "
      f"from week-1 to prior month.")

# Show the shift
shifted = ledger[mask_realloc][["date", "account_code", "description",
                                 "net_amount", "accounting_month"]].copy()
shifted["original_month"] = shifted["date"].dt.to_period("M").astype(str)
print("\nSample reallocations (original month → accounting month):")
print(shifted[["date", "original_month", "accounting_month",
               "description", "net_amount"]].head(10).to_string(index=False))

# ---------------------------------------------------------------------------
# Build month list (chronological, from all data)
# ---------------------------------------------------------------------------
all_months = sorted(ledger["accounting_month"].dropna().unique())
# Filter to operating months only (from May 2025 onward)
all_months = [m for m in all_months if m >= "2025-04"]

# ---------------------------------------------------------------------------
# Pivot: for each PL line, sum net_amount * sign per accounting_month
# ---------------------------------------------------------------------------
code_to_info = {code: (label, section, subsection, sign)
                for code, label, section, subsection, sign in PL_LINES}

rows = []
sort_order = {code: i for i, (code, *_) in enumerate(PL_LINES)}

for code, label, section, subsection, sign in PL_LINES:
    sub = ledger[ledger["account_code"] == code].copy()
    monthly = sub.groupby("accounting_month")["net_amount"].sum()
    row = {
        "sort_order": sort_order[code],
        "section":    section,
        "subsection": subsection,
        "account_code": code,
        "line_item":  label,
    }
    for m in all_months:
        raw = monthly.get(m, 0.0)
        row[m] = round(raw * sign, 2)   # positive = revenue or cost (depends on sign)
    rows.append(row)

pl = pd.DataFrame(rows).sort_values("sort_order").drop(columns="sort_order")
month_cols = [c for c in pl.columns if c.startswith("20")]

# ---------------------------------------------------------------------------
# Subtotals & derived lines
# ---------------------------------------------------------------------------
def subtotal(df, section_filter, label, section, subsection="— Total"):
    mask = df["section"].isin(section_filter) if isinstance(section_filter, list) \
           else df["section"] == section_filter
    vals = df.loc[mask, month_cols].sum()
    row = {"section": section, "subsection": subsection,
           "account_code": "", "line_item": label}
    row.update(vals.round(2).to_dict())
    return row

def derived(row_a_vals, row_b_vals, label, section, subsection="— Derived"):
    """row_a - row_b"""
    vals = {m: round(row_a_vals.get(m, 0) - row_b_vals.get(m, 0), 2) for m in month_cols}
    row = {"section": section, "subsection": subsection,
           "account_code": "", "line_item": label}
    row.update(vals)
    return row

# Total Revenue
rev_total = subtotal(pl, "Revenue", "TOTAL REVENUE", "Revenue")
# Total COGS
cogs_total = subtotal(pl, "COGS", "TOTAL COGS", "COGS")
# Gross Profit = Total Revenue - Total COGS
gp_vals = {m: round(rev_total[m] - cogs_total[m], 2) for m in month_cols}
gp_row   = {"section": "Gross Profit", "subsection": "— Derived",
            "account_code": "", "line_item": "GROSS PROFIT",
            **gp_vals}
gm_vals  = {m: (round(gp_vals[m] / rev_total[m] * 100, 1)
                if rev_total.get(m, 0) else None)
            for m in month_cols}
gm_row   = {"section": "Gross Profit", "subsection": "— Derived",
            "account_code": "", "line_item": "GROSS MARGIN %",
            **gm_vals}

# OpEx sections
labor_sections = ["Labor", "Wages & HR"]
sga_sections   = ["Equipment & Supplies", "Marketing", "Technology",
                  "Admin & Other", "Professional Services", "Finance"]
opex_sections  = labor_sections + ["Occupancy"] + sga_sections
opex_total     = subtotal(pl, opex_sections, "TOTAL OPEX", "OpEx")

# EBITDA = Gross Profit - OpEx
ebitda_vals = {m: round(gp_vals[m] - opex_total[m], 2) for m in month_cols}
ebitda_row  = {"section": "EBITDA", "subsection": "— Derived",
               "account_code": "", "line_item": "EBITDA",
               **ebitda_vals}
ebitda_pct_vals = {m: (round(ebitda_vals[m] / rev_total[m] * 100, 1)
                       if rev_total.get(m, 0) else None)
                  for m in month_cols}
ebitda_pct_row = {"section": "EBITDA", "subsection": "— Derived",
                  "account_code": "", "line_item": "EBITDA MARGIN %",
                  **ebitda_pct_vals}

# Depreciation total
depr_total = subtotal(pl, "Depreciation", "TOTAL DEPRECIATION", "Depreciation")

# EBIT = EBITDA - Depreciation
ebit_vals = {m: round(ebitda_vals[m] - depr_total[m], 2) for m in month_cols}
ebit_row  = {"section": "EBIT", "subsection": "— Derived",
             "account_code": "", "line_item": "EBIT",
             **ebit_vals}

# Interest total
int_total = subtotal(pl, "Interest", "TOTAL INTEREST", "Interest")

# Net Profit = EBIT - Interest
np_vals = {m: round(ebit_vals[m] - int_total[m], 2) for m in month_cols}
np_row  = {"section": "Net Profit", "subsection": "— Derived",
           "account_code": "", "line_item": "NET PROFIT",
           **np_vals}
npm_vals = {m: (round(np_vals[m] / rev_total[m] * 100, 1)
                if rev_total.get(m, 0) else None)
            for m in month_cols}
npm_row  = {"section": "Net Profit", "subsection": "— Derived",
            "account_code": "", "line_item": "NET MARGIN %",
            **npm_vals}

# Labor subtotals
labor_total  = subtotal(pl, labor_sections,  "TOTAL LABOR COST", "Labor")
wages_total  = subtotal(pl, "Wages & HR",    "Total Wages & HR", "Wages & HR")
# Occupancy subtotal
occ_total    = subtotal(pl, "Occupancy",     "Total Occupancy",  "Occupancy")
# Equipment subtotal
eq_total     = subtotal(pl, "Equipment & Supplies", "Total Equipment & Supplies", "Equipment & Supplies")
# SG&A subtotal
sga_total    = subtotal(pl, sga_sections,    "TOTAL SG&A",       "SG&A")

# ---------------------------------------------------------------------------
# Assemble final P&L in order
# ---------------------------------------------------------------------------
def df_row(d):
    return pd.DataFrame([d])

# Sections with their subtotals inserted at the right places
rev_rows   = pl[pl["section"] == "Revenue"]
cogs_rows  = pl[pl["section"] == "COGS"]
lab_rows   = pl[pl["section"] == "Labor"]
whr_rows   = pl[pl["section"] == "Wages & HR"]
occ_rows   = pl[pl["section"] == "Occupancy"]
eq_rows    = pl[pl["section"] == "Equipment & Supplies"]
mkt_rows   = pl[pl["section"] == "Marketing"]
tech_rows  = pl[pl["section"] == "Technology"]
adm_rows   = pl[pl["section"] == "Admin & Other"]
pro_rows   = pl[pl["section"] == "Professional Services"]
fin_rows   = pl[pl["section"] == "Finance"]
dep_rows   = pl[pl["section"] == "Depreciation"]
int_rows   = pl[pl["section"] == "Interest"]

BLANK = {"section": "—", "subsection": "", "account_code": "", "line_item": "",
         **{m: None for m in month_cols}}

pl_final = pd.concat([
    rev_rows,
    df_row(rev_total),
    df_row(BLANK),
    cogs_rows,
    df_row(cogs_total),
    df_row(BLANK),
    df_row(gp_row),
    df_row(gm_row),
    df_row(BLANK),
    # ── LABOR ──
    lab_rows,
    whr_rows,
    df_row(wages_total),
    df_row(labor_total),
    df_row(BLANK),
    # ── OCCUPANCY ──
    occ_rows,
    df_row(occ_total),
    df_row(BLANK),
    # ── SG&A ──
    eq_rows,
    df_row(eq_total),
    mkt_rows,
    tech_rows,
    adm_rows,
    pro_rows,
    fin_rows,
    df_row(sga_total),
    df_row(BLANK),
    df_row(opex_total),
    df_row({"section": "—", "subsection": "", "account_code": "", "line_item": "",
             **{m: None for m in month_cols}}),
    df_row(ebitda_row),
    df_row(ebitda_pct_row),
    df_row({"section": "—", "subsection": "", "account_code": "", "line_item": "",
             **{m: None for m in month_cols}}),
    dep_rows,
    df_row(depr_total),
    df_row(ebit_row),
    df_row({"section": "—", "subsection": "", "account_code": "", "line_item": "",
             **{m: None for m in month_cols}}),
    int_rows,
    df_row(int_total),
    df_row({"section": "—", "subsection": "", "account_code": "", "line_item": "",
             **{m: None for m in month_cols}}),
    df_row(np_row),
    df_row(npm_row),
], ignore_index=True)

# Add a TOTAL column
pl_final["TOTAL"] = pl_final[month_cols].apply(
    lambda r: round(r.sum(skipna=True), 2) if r.dtype != object else None,
    axis=1
)

# Save wide format
out_wide = "Dashboard Data/monthly_pl.csv"
pl_final.to_csv(out_wide, index=False)
print(f"\nSaved: {out_wide}  ({len(pl_final)} rows × {len(pl_final.columns)} columns)")

# ---------------------------------------------------------------------------
# Print P&L summary to console
# ---------------------------------------------------------------------------
print("\n" + "=" * 100)
print("TAAL CAFE — MONTHLY P&L (NET amounts; costs positive, revenue positive)")
print("=" * 100)

show_months = [m for m in month_cols if m >= "2025-06"]
display_cols = ["section", "line_item"] + show_months + ["TOTAL"]

highlight_lines = {"TOTAL REVENUE", "TOTAL COGS", "GROSS PROFIT", "GROSS MARGIN %",
                   "TOTAL OPEX", "EBITDA", "EBITDA MARGIN %", "EBIT",
                   "NET PROFIT", "NET MARGIN %"}

for _, row in pl_final.iterrows():
    label = str(row.get("line_item", ""))
    if label == "":
        print("")
        continue
    vals = []
    for m in show_months:
        v = row.get(m)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            vals.append("     –  ")
        elif "%" in label:
            vals.append(f"{v:>7.1f}%")
        else:
            vals.append(f"{v:>8,.0f}")
    total_v = row.get("TOTAL")
    if total_v is None or (isinstance(total_v, float) and pd.isna(total_v)):
        total_str = "       –"
    elif "%" in label:
        total_str = f"{total_v:>7.1f}%"
    else:
        total_str = f"{total_v:>9,.0f}"

    prefix = "  " if label not in highlight_lines else ""
    line = f"{prefix}{label:<45}" + "  ".join(vals) + "  " + total_str
    if label in highlight_lines:
        print("─" * len(line))
        print(line)
        if "MARGIN" not in label:
            print("─" * len(line))
    else:
        print(line)
