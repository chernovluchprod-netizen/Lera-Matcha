"""
Lera World – Interactive Dashboard
====================================
Run:  python3 dashboard.py
Then open: http://127.0.0.1:8050
"""

import json
import os
import warnings
warnings.filterwarnings("ignore")

import re
import subprocess
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import dash
import dash_auth
from dash import dcc, html, Input, Output, State, ALL, ctx
import dash_bootstrap_components as dbc

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
DATA = BASE / "Dashboard Data"

MATCHA_DRINKS_PER_KG = 333  # 1 kg / 3g per matcha drink ≈ 333 drinks

_MATCHA_FILE = DATA / "matcha_inputs.json"

def _load_matcha_inputs():
    try:
        with open(_MATCHA_FILE, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {"qty": {}, "cost_per_kg": {}}

def _save_matcha_inputs(data):
    try:
        with open(_MATCHA_FILE, "w", encoding="utf-8") as _f:
            json.dump(data, _f)
    except Exception:
        pass

def _safe_csv(path, **kwargs):
    try:
        df = pd.read_csv(path, **kwargs)
        return df if len(df.columns) > 0 else pd.DataFrame()
    except Exception:
        return pd.DataFrame()

pl_raw   = pd.read_csv(DATA / "monthly_pl.csv")
kpis     = _safe_csv(DATA / "monthly_kpis.csv")
labor    = _safe_csv(DATA / "labor_hours.csv")
tx       = _safe_csv(DATA / "transactions.csv", low_memory=False)
ledger   = pd.read_csv(DATA / "costs_ledger.csv")
products = _safe_csv(DATA / "product_sales.csv")
ecom_raw = _safe_csv(DATA / "ecom_sales.csv")

# Daily net revenue (excl. VAT) from POS reports
_ds_raw = _safe_csv(DATA / "daily_summary.csv")
if not _ds_raw.empty and "date" in _ds_raw.columns:
    _ds_raw["date"] = pd.to_datetime(_ds_raw["date"], errors="coerce").dt.date
    _ds_raw["_net_rev"] = (
        pd.to_numeric(_ds_raw.get("Total Revenue excl. VAT"), errors="coerce")
        .fillna(
            pd.to_numeric(_ds_raw.get("Net Amount VAT 9%"),  errors="coerce").fillna(0)
            + pd.to_numeric(_ds_raw.get("Net Amount VAT 21%"), errors="coerce").fillna(0)
            + pd.to_numeric(_ds_raw.get("Net Amount VAT 0%"),  errors="coerce").fillna(0)
        )
    )
    DAILY_REVENUE = {
        row["date"]: float(row["_net_rev"])
        for _, row in _ds_raw.dropna(subset=["date"]).iterrows()
        if pd.notna(row["_net_rev"])
    }
else:
    DAILY_REVENUE = {}

# Month columns
MONTHS = [c for c in pl_raw.columns if c.startswith("20")]
MONTH_LABELS = {m: pd.Period(m, "M").strftime("%b %Y") for m in MONTHS}
# Only operating months (first month with GL revenue: 2024-04)
OPERATING_MONTHS = [m for m in MONTHS if m >= "2024-04"]

# Months using POS-estimated revenue (GL not yet booked by accountant)
_pos_row = pl_raw[pl_raw["line_item"] == "POS Revenue (estimated)"]
ESTIMATED_MONTHS = set()
if len(_pos_row):
    for _m in MONTHS:
        _v = pd.to_numeric(_pos_row.iloc[0].get(_m, 0), errors="coerce")
        if pd.notna(_v) and _v > 0:
            ESTIMATED_MONTHS.add(_m)
ESTIMATED_MONTHS = sorted(ESTIMATED_MONTHS)

# Months using payroll-journal estimated wages (GL wages not yet booked)
_payroll_row = pl_raw[pl_raw["line_item"] == "Payroll Estimated"]
PAYROLL_EST_MONTHS = set()
if len(_payroll_row):
    for _m in MONTHS:
        _v = pd.to_numeric(_payroll_row.iloc[0].get(_m, 0), errors="coerce")
        if pd.notna(_v) and _v > 0:
            PAYROLL_EST_MONTHS.add(_m)
PAYROLL_EST_MONTHS = sorted(PAYROLL_EST_MONTHS)

# Months where revenue sits in payment-clearing GLs (accounting period not yet closed)
try:
    with open(DATA / "transit_months.json") as _f:
        TRANSIT_MONTHS = set(json.load(_f))
except Exception:
    TRANSIT_MONTHS = set()

# E-com monthly aggregates for P&L table display
_ECOM_MONTHLY = {}
if not ecom_raw.empty:
    _ec = ecom_raw.copy()
    _ec["month"] = _ec["month"].astype(str)
    _ECOM_MONTHLY = _ec.groupby("month").agg(
        net_sales=("net_sales", "sum"),
        shipping=("shipping_charges", "sum"),
        orders=("orders", "sum"),
    ).to_dict("index")

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR PALETTE
# ─────────────────────────────────────────────────────────────────────────────
TAAL_SAND    = "#F5EFE6"
TAAL_CLAY    = "#C4A882"
TAAL_DARK    = "#2C2416"
TAAL_GREEN   = "#4A6741"
TAAL_RED     = "#8B3A2F"
TAAL_BLUE    = "#3A5A7C"
TAAL_MUTED   = "#8A7968"
TAAL_ACCENT  = "#E8C88A"

CARD_STYLE = {
    "background": "white",
    "borderRadius": "10px",
    "padding": "20px 24px",
    "boxShadow": "0 2px 8px rgba(0,0,0,0.06)",
    "border": "1px solid #E8E0D5",
}

CHART_LAYOUT = dict(
    plot_bgcolor="white",
    paper_bgcolor="white",
    font=dict(family="Inter, -apple-system, sans-serif", color=TAAL_DARK, size=12),
    hoverlabel=dict(bgcolor="white", bordercolor="#CCC", font_size=12),
)

LEGEND_H = dict(orientation="h", y=-0.18, x=0.5, xanchor="center",
                bgcolor="rgba(0,0,0,0)", font=dict(size=11))
MARGIN   = dict(l=10, r=10, t=40, b=10)
AXIS_STYLE = dict(gridcolor="#F0EBE3", linecolor="#E8E0D5")


def apply_layout(fig, **kwargs):
    """Apply base CHART_LAYOUT then chart-specific overrides."""
    fig.update_layout(**CHART_LAYOUT, **kwargs)
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_row(line_item: str) -> pd.Series:
    rows = pl_raw[pl_raw["line_item"] == line_item]
    return rows.iloc[0] if len(rows) else pd.Series(dtype=float)

def pl_series(line_item: str, months=None) -> pd.Series:
    row = get_row(line_item)
    m = months or OPERATING_MONTHS
    if row.empty:
        return pd.Series(0.0, index=m)
    return pd.to_numeric(row[m], errors="coerce").fillna(0)

def month_x(months=None):
    m = months or OPERATING_MONTHS
    return [MONTH_LABELS[x] for x in m]

def fmt_eur(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if abs(v) >= 1000:
        return f"€{v:,.0f}"
    return f"€{v:.0f}"

def fmt_pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:.1f}%"

def kpi_card(title, value, subtitle=None, color=None, icon=None):
    val_color = color or TAAL_DARK
    return html.Div([
        html.Div(title, style={"fontSize": "11px", "color": TAAL_MUTED,
                                "textTransform": "uppercase", "letterSpacing": "0.05em",
                                "marginBottom": "6px", "fontWeight": "600"}),
        html.Div(value, style={"fontSize": "26px", "fontWeight": "700",
                                "color": val_color, "lineHeight": "1.1"}),
        html.Div(subtitle or "", style={"fontSize": "11px", "color": TAAL_MUTED,
                                         "marginTop": "4px"}),
    ], style={**CARD_STYLE, "minWidth": "140px"})

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 – P&L
# ─────────────────────────────────────────────────────────────────────────────
COGS_LINES = [
    "Food & Bev Kiosk (Low VAT)",
    "Food & Bev Kiosk (High VAT)",
    "Matcha Tools Import",
    "Retail Products NL",
    "Exempt Purchases",
    "Imports EU – Other",
    "Retail Products EU (HV)",
    "Imports EU – Overig",
    "Retail Products Outside EU",
    "Imports Outside EU – Other",
    "Small Equipment EU (COGS)",
    "Packaging Materials",
    "Packaging Materials EU",
    "Packaging Import (Ext EU)",
    "Inventory Adjustment",
]

OPEX_GROUPS = {
    "Labor":              ["Freelancers / Outsourced Work", "Payroll"],
    "Occupancy":          ["Rent", "Utilities (GWE)", "Maintenance", "Cleaning",
                           "Depreciation Fit-out", "Other Occupancy"],
    "Marketing":          ["Marketing & Advertising", "Client Gifts", "Advertising EU"],
    "Travel & Ent.":      ["Representation", "Travel & Accommodation"],
    "Subscriptions":      ["Subscriptions", "Subscriptions EU"],
    "Professional Svcs":  ["Accounting & Advisory", "Legal Costs", "Other Advisory",
                           "Advisory Services EU"],
    "Payment Processing": ["Adyen Costs", "PayPal Costs", "Tebi / Other Payments"],
    "Equipment":          ["Equipment Leasing", "Small Equipment Purchases", "Machine Repair"],
    "Other SG&A":         ["Postage & Shipping", "Bank Charges", "Depreciation Inventaris",
                           "Canteen Costs", "Uniforms", "Other Sales Costs", "Car Rental",
                           "Fines & Penalties", "Other Admin Costs", "Liability Insurance",
                           "Other Insurance", "Payment Differences", "Tax Fines",
                           "Other Costs", "COGS Payment Differences"],
}

COGS_COLORS = [
    "#4A6741", "#7A9F74", "#B8D4B3",
    "#C4A882", "#DCC8A8", "#E8DCC8",
    "#3A5A7C", "#6A8AAC", "#9AB0C4", "#C8D8E8",
    "#5A3A7C", "#9A7AB8", "#E8A878", "#D0C8BC",
    "#8B3A2F", "#C4704A", "#E8C88A",
]
OPEX_COLORS = {
    "Labor":              "#8B3A2F",
    "Occupancy":          "#C4704A",
    "Marketing":          "#3A5A7C",
    "Travel & Ent.":      "#6A8AAC",
    "Subscriptions":      "#9AB0C4",
    "Professional Svcs":  "#C8D8E8",
    "Payment Processing": "#4A6741",
    "Equipment":          "#7A9F74",
    "Other SG&A":         "#D0C8BC",
}

# ─────────────────────────────────────────────────────────────────────────────
# P&L TABLE CONFIG  (module-level so callbacks can access it)
# ─────────────────────────────────────────────────────────────────────────────
# Tuple: (label, is_total, is_sub, is_header)
PL_TABLE_LINES = [
    ("Gross Revenue",           False, True,  False),
    ("Payment Fees",            False, True,  False),
    ("TOTAL REVENUE",           True,  False, False),
    ("TOTAL COGS",              True,  False, False),
    ("Matcha Drinks Qty",       False, True,  False),
    ("Matcha Cost/kg (€)",      False, True,  False),
    ("Matcha Calculated Cost",  False, True,  False),
    ("GROSS PROFIT",            True,  False, False),
    ("GROSS MARGIN %",          True,  False, False),
    # ── Labor ──────────────────────────────────────────────────────────────────
    ("LABOR",                   False, False, True),
    ("Freelancers / Outsourced Work", False, True, False),
    ("Payroll",                 False, True,  False),
    ("TOTAL LABOR",             True,  False, False),
    # ── Occupancy ──────────────────────────────────────────────────────────────
    ("OCCUPANCY",               False, False, True),
    ("Rent",                    False, True,  False),
    ("Utilities (GWE)",         False, True,  False),
    ("Maintenance + Cleaning",  False, True,  False),
    ("Depreciation Fit-out",    False, True,  False),
    ("TOTAL OCCUPANCY",         True,  False, False),
    # ── SG&A ───────────────────────────────────────────────────────────────────
    ("SG&A",                    False, False, True),
    ("Marketing & Advertising", False, True,  False),
    ("Travel & Entertainment",  False, True,  False),
    ("Subscriptions",           False, True,  False),
    ("Professional Services",   False, True,  False),
    ("Payment Processing",      False, True,  False),
    ("Shipping / Postage",      False, True,  False),
    ("Bank Charges",            False, True,  False),
    ("Equipment & Repairs",     False, True,  False),
    ("Depreciation Inventaris", False, True,  False),
    ("Other SG&A",              False, True,  False),
    ("TOTAL SG&A",              True,  False, False),
    # ── Profitability ──────────────────────────────────────────────────────────
    ("EBIT",                    True,  False, False),
    ("EBIT MARGIN %",           True,  False, False),
    ("EBITDA",                  True,  False, False),
    ("EBITDA MARGIN %",         True,  False, False),
    # ── Matcha Company ─────────────────────────────────────────────────────────
    ("MATCHA COMPANY",          False, False, True),
    ("Matcha Purchases (KG)",   False, True,  False),
    ("Matcha Retail Import",    False, True,  False),
    ("Matcha Calc. Cost (–)",   False, True,  False),
    ("Packaging",               False, True,  False),
    ("Management Fee",          False, True,  False),
    ("Interest Expense",        False, True,  False),
    ("Interest Income",         False, True,  False),
    ("TOTAL MATCHA COMPANY",    True,  False, False),
    # ── EBT ────────────────────────────────────────────────────────────────────
    ("EBT",                     True,  False, False),
    ("EBT MARGIN %",            True,  False, False),
    # ── Informational: E-com & Wholesale (already included in TOTAL REVENUE) ─
    ("E-COM & WHOLESALE (incl. in Revenue)", False, False, True),
    ("E-com Revenue",           False, True,  False),
    ("E-com Net Sales",         False, True,  False),
    ("E-com Shipping",          False, True,  False),
    ("E-com Orders",            False, True,  False),
    ("Wholesale Revenue",       False, True,  False),
]

# Groups that can expand to show GL-level detail lines
PL_DETAIL_LINES = {
    "TOTAL COGS": COGS_LINES,
    "Freelancers / Outsourced Work": ["Freelancers / Outsourced Work"],
    "Payroll":                       ["Payroll"],
    "Maintenance + Cleaning":        ["Maintenance", "Cleaning"],
    "Marketing & Advertising": [
        "Marketing & Advertising", "Client Gifts", "Advertising EU",
    ],
    "Travel & Entertainment": [
        "Representation", "Travel & Accommodation",
    ],
    "Subscriptions": [
        "Subscriptions", "Subscriptions EU",
    ],
    "Professional Services": [
        "Accounting & Advisory", "Legal Costs", "Other Advisory", "Advisory Services EU",
    ],
    "Payment Processing": [
        "Adyen Costs", "PayPal Costs", "Tebi / Other Payments",
    ],
    "Equipment & Repairs": [
        "Equipment Leasing", "Small Equipment Purchases", "Machine Repair",
    ],
    "Other SG&A": [
        "Postage & Shipping", "Bank Charges", "Depreciation Inventaris",
        "Canteen Costs", "Uniforms", "Other Sales Costs", "Car Rental",
        "Fines & Penalties", "Other Admin Costs", "Liability Insurance",
        "Other Insurance", "Payment Differences", "Tax Fines",
        "Other Costs", "COGS Payment Differences",
    ],
}

_OUTSOURCED_LINES = ["Freelancers / Outsourced Work"]
_WAGES_LINES      = ["Payroll"]
_MKT_LINES        = ["Marketing & Advertising", "Client Gifts", "Advertising EU"]
_ADMIN_LINES      = ["Postage & Shipping", "Bank Charges", "Canteen Costs", "Uniforms",
                     "Other Sales Costs", "Car Rental", "Fines & Penalties",
                     "Other Admin Costs", "Liability Insurance", "Other Insurance",
                     "Payment Differences", "Tax Fines", "Other Costs",
                     "COGS Payment Differences"]
_PRO_LINES        = ["Accounting & Advisory", "Legal Costs", "Other Advisory",
                     "Advisory Services EU"]
_OTHER_COGS_LINES = COGS_LINES

# ─── Vendor-level data from costs ledger ──────────────────────────────────────
# Maps GL P&L line label → ledger category values
_GL_TO_CATEGORIES = {
    # COGS
    "Food & Bev Kiosk (Low VAT)":      ["COGS – Food & Bev Kiosk"],
    "Food & Bev Kiosk (High VAT)":     ["COGS – Food & Bev Kiosk"],
    "Matcha Tools Import":             ["COGS – Matcha"],
    "Retail Products NL":              ["COGS – Retail & Other"],
    "Exempt Purchases":                ["COGS – Retail & Other"],
    "Imports EU – Other":              ["COGS – Retail & Other"],
    "Retail Products EU (HV)":         ["COGS – Retail & Other"],
    "Imports EU – Overig":             ["COGS – Retail & Other"],
    "Retail Products Outside EU":      ["COGS – Retail & Other"],
    "Imports Outside EU – Other":      ["COGS – Retail & Other"],
    "Small Equipment EU (COGS)":       ["COGS – Retail & Other"],
    "Packaging Materials":             ["COGS – Packaging"],
    "Packaging Materials EU":          ["COGS – Packaging"],
    "Packaging Import (Ext EU)":       ["COGS – Packaging"],
    "Inventory Adjustment":            ["COGS – Inventory Adj."],
    # Labor
    "Freelancers / Outsourced Work":   ["Labor – Freelancers"],
    "Payroll":                         ["Labor – Payroll"],
    # Occupancy
    "Rent":                            ["Occupancy – Rent"],
    "Utilities (GWE)":                 ["Occupancy – Utilities"],
    "Maintenance":                     ["Occupancy – Maintenance"],
    "Cleaning":                        ["Occupancy – Maintenance"],
    "Depreciation Fit-out":            ["Occupancy – Depreciation"],
    "Other Occupancy":                 ["Occupancy – Other"],
    # SG&A – Marketing
    "Marketing & Advertising":         ["SG&A – Marketing"],
    "Client Gifts":                    ["SG&A – Marketing"],
    "Advertising EU":                  ["SG&A – Marketing"],
    # SG&A – Travel
    "Representation":                  ["SG&A – Travel & Entertainment"],
    "Travel & Accommodation":          ["SG&A – Travel & Entertainment"],
    # SG&A – Subscriptions
    "Subscriptions":                   ["SG&A – Subscriptions"],
    "Subscriptions EU":                ["SG&A – Subscriptions"],
    # SG&A – Professional
    "Accounting & Advisory":           ["SG&A – Professional Services"],
    "Legal Costs":                     ["SG&A – Professional Services"],
    "Other Advisory":                  ["SG&A – Professional Services"],
    "Advisory Services EU":            ["SG&A – Professional Services"],
    # SG&A – Payment Processing
    "Adyen Costs":                     ["SG&A – Payment Processing"],
    "PayPal Costs":                    ["SG&A – Payment Processing"],
    "Tebi / Other Payments":           ["SG&A – Payment Processing"],
    # SG&A – Other
    "Postage & Shipping":              ["SG&A – Shipping"],
    "Bank Charges":                    ["SG&A – Bank Charges"],
    "Equipment Leasing":               ["SG&A – Equipment & Repairs"],
    "Small Equipment Purchases":       ["SG&A – Equipment & Repairs"],
    "Machine Repair":                  ["SG&A – Equipment & Repairs"],
    "Depreciation Inventaris":         ["SG&A – Depreciation Inventaris"],
    "Canteen Costs":                   ["SG&A – Other"],
    "Uniforms":                        ["SG&A – Other"],
    "Other Sales Costs":               ["SG&A – Other"],
    "Car Rental":                      ["SG&A – Other"],
    "Fines & Penalties":               ["SG&A – Other"],
    "Other Admin Costs":               ["SG&A – Other"],
    "Liability Insurance":             ["SG&A – Other"],
    "Other Insurance":                 ["SG&A – Other"],
    "Payment Differences":             ["SG&A – Other"],
    "Tax Fines":                       ["SG&A – Other"],
    "Other Costs":                     ["SG&A – Other"],
    "COGS Payment Differences":        ["SG&A – Other"],
    # Below the Line
    "E-com Revenue":                   ["Matcha Company – E-com"],
    "Matcha Purchases (KG)":           ["Matcha Company – Matcha"],
    "Matcha Retail Import":            ["Matcha Company – Matcha"],
    "Packaging":                       ["Matcha Company – Packaging"],
    "Management Fee":                  ["Matcha Company – Management Fee"],
    "Interest Expense":                ["Matcha Company – Interest"],
    "Interest Income":                 ["Matcha Company – Interest"],
}

# Strip trailing legal-entity suffixes
_LEGAL_SUFFIX_RE = re.compile(
    r'[\s,]+(B\.?V\.?|N\.?V\.?|NV|Inc\.?|Ltd\.?|GmbH|AS|S\.A\.?|SARL|Sp\.\s*z\s*o\.o\.?)\s*$',
    re.IGNORECASE,
)
# Strip bank "Naam: ..." noise appended after vendor name
_NAAM_RE = re.compile(r'\s+Naam:.*$', re.IGNORECASE)
# Strip date suffixes like "(januari 2026, 1/1)"
_PAREN_DATE_RE = re.compile(r'\s*\([^)]*\d{4}[^)]*\)\s*$')

# Canonical overrides – keys are lowercase post-normalization
_VENDOR_CANONICAL = {
    "asr schadeverzekering":        "ASR Schadeverzekering",
    "asr schadeverzekering n":      "ASR Schadeverzekering",   # N.V stripped to N
    "baux pastry":                  "Baux Pastry",
    "t. wolters huur jj cremerplein 34-hs 05-06 2025": "T. Wolters",
    "nan":                          "Unknown (Inv. 27108)",
}

def normalize_vendor(raw: str) -> str:
    """Canonical vendor key: clean bank noise, strip legal suffixes, apply aliases."""
    name = str(raw).split(";")[0].strip()       # drop product detail after ";"
    name = _NAAM_RE.sub("", name).strip()       # drop " Naam: ..." bank suffix
    name = _PAREN_DATE_RE.sub("", name).strip() # drop "(januari 2026, 1/1)"
    name = _LEGAL_SUFFIX_RE.sub("", name).strip()
    # Payroll journal lines → single label
    if re.match(r'Ljp\s', name, re.IGNORECASE):
        return "Payroll Journal"
    return _VENDOR_CANONICAL.get(name.lower(), name)[:48]


def _build_vendor_data():
    """Build {gl_label: {accounting_month: {vendor: amount}}} using P&L accounting months.

    Account 7100 (Outsourced Staff) entries dated in days 1-7 are shifted to the
    previous accounting month, matching the reallocation in build_pl.py.
    """
    _lpl = ledger[ledger["is_pl"] == True].copy()
    _lpl["date"] = pd.to_datetime(_lpl["date"], errors="coerce")
    _lpl["acc_month"] = _lpl["month"].copy()
    # Replicate 7100 reallocation: first-week invoices → previous month
    _realloc_mask = (
        _lpl["account_code"].astype(str) == "7100"
    ) & (_lpl["date"].dt.day <= 7)
    _lpl.loc[_realloc_mask, "acc_month"] = _lpl.loc[_realloc_mask, "date"].apply(
        lambda d: (d - relativedelta(months=1)).strftime("%Y-%m")
    )

    result = {}
    for gl, cats in _GL_TO_CATEGORIES.items():
        if gl == "Packaging":
            # Only Shanghai Senqi entries from account 7023
            subset = _lpl[
                (_lpl["account_code"].astype(str) == "7023") &
                _lpl["description"].str.contains("Shanghai Senqi", case=False, na=False)
            ]
        else:
            subset = _lpl[_lpl["category"].isin(cats)]
        if subset.empty:
            continue
        month_vendor = {}
        for m in MONTHS:
            m_data = subset[subset["acc_month"] == m]
            if m_data.empty:
                continue
            vt = m_data.groupby("description")["net_amount"].sum()
            vt = vt[vt.abs() > 0.005]
            if not vt.empty:
                month_vendor[m] = vt.to_dict()
        if month_vendor:
            result[gl] = month_vendor
    return result

GL_VENDOR_DATA = _build_vendor_data()


def _pl_val(label, months):
    """Return a pd.Series of P&L values for label over months."""
    m = months or OPERATING_MONTHS
    if label == "Payment Fees":
        return -(pl_series("Adyen Costs", m) + pl_series("PayPal Costs", m) +
                 pl_series("Tebi / Other Payments", m))
    if label == "Gross Revenue":
        rev = _pl_val("TOTAL REVENUE", m)
        fees = pl_series("Adyen Costs", m) + pl_series("PayPal Costs", m) + pl_series("Tebi / Other Payments", m)
        return rev + fees
    if label == "Maintenance + Cleaning":
        return pl_series("Maintenance", m) + pl_series("Cleaning", m)
    if label == "Marketing & Advertising":
        return sum(pl_series(l, m) for l in _MKT_LINES)
    if label == "Travel & Entertainment":
        return pl_series("Representation", m) + pl_series("Travel & Accommodation", m)
    if label == "Subscriptions":
        return pl_series("Subscriptions", m) + pl_series("Subscriptions EU", m)
    if label == "Professional Services":
        return sum(pl_series(l, m) for l in _PRO_LINES)
    if label == "Payment Processing":
        return (pl_series("Adyen Costs", m) + pl_series("PayPal Costs", m) +
                pl_series("Tebi / Other Payments", m))
    if label == "Shipping / Postage":
        return pl_series("Postage & Shipping", m)
    if label == "Equipment & Repairs":
        return (pl_series("Equipment Leasing", m) + pl_series("Small Equipment Purchases", m) +
                pl_series("Machine Repair", m))
    if label == "Depreciation Inventaris":
        return pl_series("Depreciation Inventaris", m)
    if label == "Other SG&A":
        return sum(pl_series(l, m) for l in _ADMIN_LINES)
    if label == "E-com Net Sales":
        return pd.Series({mo: _ECOM_MONTHLY.get(mo, {}).get("net_sales", 0) for mo in m})
    if label == "E-com Shipping":
        return pd.Series({mo: _ECOM_MONTHLY.get(mo, {}).get("shipping", 0) for mo in m})
    if label == "E-com Orders":
        return pd.Series({mo: _ECOM_MONTHLY.get(mo, {}).get("orders", 0) for mo in m})
    if label == "Wholesale Revenue":
        return (pl_series("Export (Outside EU)", m) + pl_series("EU B2B Sales", m) +
                pl_series("Service Export / EU", m) + pl_series("Other Export / EU", m) +
                pl_series("Services – High VAT", m) + pl_series("Services – Low VAT", m))
    # Computed totals — look up directly from monthly_pl.csv (pipeline pre-computes these)
    for computed in ("TOTAL REVENUE", "TOTAL COGS", "GROSS PROFIT", "GROSS MARGIN %",
                     "TOTAL LABOR", "TOTAL OCCUPANCY", "TOTAL SG&A",
                     "EBIT", "EBIT MARGIN %", "EBITDA", "EBITDA MARGIN %",
                     "TOTAL MATCHA COMPANY", "EBT", "EBT MARGIN %"):
        if label == computed:
            row = get_row(computed)
            if not row.empty:
                return pd.to_numeric(row[m], errors="coerce").fillna(0)
    return pl_series(label, m)


def make_pl_tab():
    # ── KPI summary row ──────────────────────────────────────────────────────
    rev_total   = pl_series("TOTAL REVENUE").sum()
    cogs_total  = pl_series("TOTAL COGS").sum()
    gp_total    = pl_series("GROSS PROFIT").sum()
    ebitda_sum  = pl_series("EBITDA").sum()
    # Best single month margins (operating months with revenue)
    gm_series   = pd.to_numeric(get_row("GROSS MARGIN %")[OPERATING_MONTHS], errors="coerce")
    best_gm     = gm_series.max()
    ebitda_series = pl_series("EBITDA")
    best_ebitda   = ebitda_series.max()

    kpi_row = dbc.Row([
        dbc.Col(kpi_card(
            "Total Revenue",
            fmt_eur(rev_total),
            ("GL + POS est. " + ", ".join(MONTH_LABELS[m] for m in ESTIMATED_MONTHS))
            if ESTIMATED_MONTHS else "GL — Apr 2024 onwards",
        ), width="auto"),
        dbc.Col(kpi_card("Total COGS",          fmt_eur(cogs_total), "All periods"),         width="auto"),
        dbc.Col(kpi_card("Gross Profit",        fmt_eur(gp_total),   "All periods",
                         color=TAAL_GREEN if gp_total >= 0 else TAAL_RED),                   width="auto"),
        dbc.Col(kpi_card("Best Gross Margin",   fmt_pct(best_gm),    "Single month peak",
                         color=TAAL_GREEN),                                                  width="auto"),
        dbc.Col(kpi_card("Best EBITDA Month",   fmt_eur(best_ebitda), "Single month peak",
                         color=TAAL_GREEN),                                                  width="auto"),
    ], className="g-3 mb-4")

    # ── Chart 1 – Revenue vs COGS vs Gross Profit waterfall-style ────────────
    x = month_x()
    rev  = pl_series("TOTAL REVENUE").tolist()
    cogs = pl_series("TOTAL COGS").tolist()
    gp   = pl_series("GROSS PROFIT").tolist()
    gm   = pd.to_numeric(get_row("GROSS MARGIN %")[OPERATING_MONTHS], errors="coerce").tolist()
    ebitda = pl_series("EBITDA").tolist()

    fig_overview = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.06,
    )
    # Revenue bars: different shade for POS-estimated months
    rev_colors = [
        TAAL_ACCENT if m in ESTIMATED_MONTHS else TAAL_GREEN
        for m in OPERATING_MONTHS
    ]
    rev_labels = [
        "Revenue (POS est.)" if m in ESTIMATED_MONTHS else "Revenue (GL)"
        for m in OPERATING_MONTHS
    ]
    fig_overview.add_trace(go.Bar(
        name="Revenue", x=x, y=rev,
        marker_color=rev_colors, opacity=0.9,
        customdata=rev_labels,
        hovertemplate="<b>%{x}</b><br>%{customdata}: €%{y:,.0f}<extra></extra>",
    ), row=1, col=1)
    fig_overview.add_trace(go.Bar(
        name="COGS", x=x, y=cogs,
        marker_color=TAAL_CLAY, opacity=0.9,
    ), row=1, col=1)
    fig_overview.add_trace(go.Scatter(
        name="Gross Profit", x=x, y=gp,
        mode="lines+markers",
        line=dict(color=TAAL_DARK, width=2),
        marker=dict(size=6, color=[TAAL_GREEN if v >= 0 else TAAL_RED for v in gp]),
    ), row=1, col=1)
    fig_overview.add_trace(go.Scatter(
        name="EBITDA", x=x, y=ebitda,
        mode="lines+markers",
        line=dict(color=TAAL_BLUE, width=2, dash="dot"),
        marker=dict(size=5, color=[TAAL_BLUE if v >= 0 else TAAL_RED for v in ebitda]),
    ), row=1, col=1)
    # Gross margin % as area
    fig_overview.add_trace(go.Scatter(
        name="Gross Margin %", x=x, y=gm,
        mode="lines+markers",
        fill="tozeroy", fillcolor="rgba(74,103,65,0.10)",
        line=dict(color=TAAL_GREEN, width=1.5),
        marker=dict(size=5),
        yaxis="y3",
    ), row=2, col=1)
    fig_overview.add_hline(y=0, line_color="#DDD", row=2, col=1)

    apply_layout(fig_overview,
        barmode="group", height=430,
        margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Revenue · COGS · Gross Profit · EBITDA", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
        yaxis2=dict(title="Gross Margin %", ticksuffix="%", tickformat=".0f", **AXIS_STYLE),
    )
    # Shade POS-estimated months (revenue not yet booked in GL)
    for m in ESTIMATED_MONTHS:
        if m in OPERATING_MONTHS:
            fig_overview.add_vrect(
                x0=MONTH_LABELS[m], x1=MONTH_LABELS[m],
                fillcolor="rgba(232,200,138,0.18)",
                layer="below", line_width=1.5,
                line_color="rgba(196,168,130,0.5)",
                line_dash="dot",
            )
    if ESTIMATED_MONTHS:
        fig_overview.add_annotation(
            text="◆ shaded = POS estimated revenue (GL not yet booked)",
            x=1, y=1.04, xref="paper", yref="paper",
            showarrow=False, xanchor="right",
            font=dict(size=10, color=TAAL_MUTED),
        )
    # Shade wage-estimated months (payroll PDF not yet available)
    for m in PAYROLL_EST_MONTHS:
        if m in OPERATING_MONTHS and m not in ESTIMATED_MONTHS:
            fig_overview.add_vrect(
                x0=MONTH_LABELS[m], x1=MONTH_LABELS[m],
                fillcolor="rgba(180,180,220,0.15)",
                layer="below", line_width=1.5,
                line_color="rgba(150,150,200,0.5)",
                line_dash="dash",
            )
    if PAYROLL_EST_MONTHS:
        annot_y = 1.08 if ESTIMATED_MONTHS else 1.04
        fig_overview.add_annotation(
            text="† dashed border = wages estimated (payroll PDF pending)",
            x=1, y=annot_y, xref="paper", yref="paper",
            showarrow=False, xanchor="right",
            font=dict(size=10, color=TAAL_MUTED),
        )
    fig_overview.update_traces(
        hovertemplate="<b>%{x}</b><br>%{y:,.0f}<extra>%{fullData.name}</extra>",
        selector=dict(type="bar", customdata=None),
    )

    # ── Chart 2 – COGS breakdown stacked bar ─────────────────────────────────
    fig_cogs = go.Figure()
    cogs_data = {}
    for line in COGS_LINES:
        s = pl_series(line)
        if s.sum() > 0:
            cogs_data[line] = s

    for i, (label, series) in enumerate(cogs_data.items()):
        fig_cogs.add_trace(go.Bar(
            name=label, x=x, y=series.tolist(),
            marker_color=COGS_COLORS[i % len(COGS_COLORS)],
            hovertemplate="<b>%{x}</b><br>" + label + ": €%{y:,.0f}<extra></extra>",
        ))

    apply_layout(fig_cogs,
        barmode="stack", height=360,
        margin=MARGIN, legend=LEGEND_H,
        title=dict(text="COGS by Type", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
    )

    # ── Chart 3 – OpEx breakdown stacked bar ─────────────────────────────────
    fig_opex = go.Figure()
    for group, lines in OPEX_GROUPS.items():
        group_total = sum(pl_series(line) for line in lines)
        if group_total.sum() > 1:
            fig_opex.add_trace(go.Bar(
                name=group, x=x, y=group_total.tolist(),
                marker_color=OPEX_COLORS.get(group, TAAL_MUTED),
                hovertemplate="<b>%{x}</b><br>" + group + ": €%{y:,.0f}<extra></extra>",
            ))

    apply_layout(fig_opex,
        barmode="stack", height=360,
        margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Operating Costs by Category", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
    )

    # ── Chart 4 – Cost structure donut (full period) ─────────────────────────
    donut_labels, donut_vals, donut_colors = [], [], []
    cogs_total_v = sum(cogs_data[k].sum() for k in cogs_data)
    for label, series in cogs_data.items():
        v = series.sum()
        if v > 100:
            donut_labels.append(label)
            donut_vals.append(v)
    for i, group in enumerate(OPEX_GROUPS):
        group_total = sum(pl_series(line) for line in OPEX_GROUPS[group])
        v = group_total.sum()
        if v > 100:
            donut_labels.append(group)
            donut_vals.append(v)

    fig_donut = go.Figure(go.Pie(
        labels=donut_labels,
        values=donut_vals,
        hole=0.55,
        textinfo="label+percent",
        textposition="outside",
        pull=[0.03] * len(donut_labels),
        marker=dict(colors=COGS_COLORS[:len(donut_labels)]),
        hovertemplate="<b>%{label}</b><br>€%{value:,.0f} (%{percent})<extra></extra>",
    ))
    apply_layout(fig_donut,
        height=420, showlegend=False,
        margin=dict(l=20, r=20, t=50, b=20),
        title=dict(text="Total Cost Structure (All Periods)", font=dict(size=14)),
        annotations=[dict(text=f"€{sum(donut_vals):,.0f}<br><span style='font-size:11px'>Total Costs</span>",
                          x=0.5, y=0.5, font_size=16, showarrow=False,
                          font=dict(color=TAAL_DARK))],
    )

    # ── Chart 5 – MoM changes & rolling trends ───────────────────────────────
    ebit   = pl_series("EBIT").tolist()

    rev_s    = pd.Series(rev,    index=OPERATING_MONTHS)
    gp_s     = pd.Series(gp,     index=OPERATING_MONTHS)
    ebitda_s = pd.Series(ebitda, index=OPERATING_MONTHS)
    ebit_s   = pd.Series(ebit,   index=OPERATING_MONTHS)

    # Month-over-month absolute change
    rev_mom    = rev_s.diff().tolist()
    gp_mom     = gp_s.diff().tolist()
    ebitda_mom = ebitda_s.diff().tolist()
    ebit_mom   = ebit_s.diff().tolist()

    # 3-month rolling average
    def rolling3(s):
        return pd.Series(s).rolling(3, min_periods=2).mean().tolist()

    rev_roll    = rolling3(rev)
    gp_roll     = rolling3(gp)
    ebitda_roll = rolling3(ebitda)
    ebit_roll   = rolling3(ebit)

    fig_trend = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.45],
        vertical_spacing=0.08,
        subplot_titles=("3-Month Rolling Average", "Month-over-Month Change"),
    )

    # Row 1 – rolling averages
    fig_trend.add_trace(go.Scatter(
        name="Revenue (3m avg)", x=x, y=rev_roll,
        mode="lines", line=dict(color=TAAL_GREEN, width=2.5),
        hovertemplate="<b>%{x}</b><br>Rev 3m avg: €%{y:,.0f}<extra></extra>",
    ), row=1, col=1)
    fig_trend.add_trace(go.Scatter(
        name="Gross Profit (3m avg)", x=x, y=gp_roll,
        mode="lines", line=dict(color=TAAL_DARK, width=2),
        hovertemplate="<b>%{x}</b><br>GP 3m avg: €%{y:,.0f}<extra></extra>",
    ), row=1, col=1)
    fig_trend.add_trace(go.Scatter(
        name="EBITDA (3m avg)", x=x, y=ebitda_roll,
        mode="lines", line=dict(color=TAAL_BLUE, width=2, dash="dot"),
        hovertemplate="<b>%{x}</b><br>EBITDA 3m avg: €%{y:,.0f}<extra></extra>",
    ), row=1, col=1)
    fig_trend.add_trace(go.Scatter(
        name="EBIT (3m avg)", x=x, y=ebit_roll,
        mode="lines+markers", line=dict(color=TAAL_RED, width=2, dash="dash"),
        marker=dict(size=5, color=TAAL_RED),
        hovertemplate="<b>%{x}</b><br>EBIT 3m avg: €%{y:,.0f}<extra></extra>",
    ), row=1, col=1)

    # Row 2 – MoM bars
    mom_colors_rev  = [TAAL_GREEN if (v or 0) >= 0 else TAAL_RED for v in rev_mom]
    mom_colors_gp   = [TAAL_GREEN if (v or 0) >= 0 else TAAL_RED for v in gp_mom]
    mom_colors_ebit = [TAAL_GREEN if (v or 0) >= 0 else TAAL_RED for v in ebit_mom]

    fig_trend.add_trace(go.Bar(
        name="Revenue MoM Δ", x=x, y=rev_mom,
        marker_color=mom_colors_rev, opacity=0.75,
        hovertemplate="<b>%{x}</b><br>Rev MoM: €%{y:+,.0f}<extra></extra>",
    ), row=2, col=1)
    fig_trend.add_trace(go.Scatter(
        name="Gross Profit MoM Δ", x=x, y=gp_mom,
        mode="lines+markers",
        line=dict(color=TAAL_DARK, width=1.5),
        marker=dict(size=5, color=mom_colors_gp),
        hovertemplate="<b>%{x}</b><br>GP MoM: €%{y:+,.0f}<extra></extra>",
    ), row=2, col=1)
    fig_trend.add_trace(go.Scatter(
        name="EBIT MoM Δ", x=x, y=ebit_mom,
        mode="lines+markers",
        line=dict(color=TAAL_RED, width=1.5, dash="dash"),
        marker=dict(size=5, color=mom_colors_ebit),
        hovertemplate="<b>%{x}</b><br>EBIT MoM: €%{y:+,.0f}<extra></extra>",
    ), row=2, col=1)
    fig_trend.add_hline(y=0, line_color="#DDD", row=2, col=1)

    apply_layout(fig_trend,
        barmode="group", height=480,
        margin=MARGIN, legend=LEGEND_H,
        title=dict(text="P&L Trends & Month-over-Month Changes", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
        yaxis2=dict(title="MoM Δ (EUR)", tickformat=",.0f", **AXIS_STYLE),
    )

    # ── Layout assembly (charts only) ────────────────────────────────────────
    return html.Div([
        kpi_row,
        # Row 1: overview chart full width
        html.Div([
            html.Div([dcc.Graph(figure=fig_overview, config={"displayModeBar": False})],
                     style=CARD_STYLE)
        ], className="mb-4"),
        # Row 2: COGS + OpEx side by side
        dbc.Row([
            dbc.Col(html.Div([dcc.Graph(figure=fig_cogs,  config={"displayModeBar": False})],
                             style=CARD_STYLE), md=6),
            dbc.Col(html.Div([dcc.Graph(figure=fig_opex,  config={"displayModeBar": False})],
                             style=CARD_STYLE), md=6),
        ], className="g-3 mb-4"),
        # Row 3: trends full width
        html.Div([
            html.Div([dcc.Graph(figure=fig_trend, config={"displayModeBar": False})],
                     style=CARD_STYLE)
        ], className="mb-4"),
        # Row 4: donut
        html.Div([
            html.Div([dcc.Graph(figure=fig_donut, config={"displayModeBar": False})],
                     style={**CARD_STYLE, "maxWidth": "520px"}),
        ], className="mb-4"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 – MONTHLY P&L TABLE
# ─────────────────────────────────────────────────────────────────────────────
def make_pl_table_tab():
    # Determine available years for the selector
    avail_years = sorted({m[:4] for m in OPERATING_MONTHS})
    year_options = [{"label": "All", "value": "all"}] + [
        {"label": y, "value": y} for y in avail_years
    ]

    year_selector = html.Div([
        html.Span("Year: ", style={"fontSize": "12px", "color": TAAL_MUTED,
                                   "fontWeight": "600", "marginRight": "8px",
                                   "verticalAlign": "middle"}),
        dcc.RadioItems(
            id="pl-kpi-year",
            options=year_options,
            value="all",
            inline=True,
            inputStyle={"marginRight": "4px"},
            labelStyle={
                "fontSize": "12px", "fontWeight": "600", "cursor": "pointer",
                "marginRight": "14px", "color": TAAL_DARK,
            },
        ),
    ], style={"marginBottom": "16px"})

    pl_table_section = html.Div([
        dcc.Store(id="pl-expanded", data=[]),
        html.Div(id="pl-table-container"),
    ])

    export_btn = html.A(
        "Export to Excel",
        href="/download/pl-export",
        download="Lera_PL.xlsx",
        style={
            "backgroundColor": TAAL_GREEN, "color": "white", "border": "none",
            "borderRadius": "6px", "padding": "7px 18px", "fontSize": "12px",
            "fontWeight": "600", "cursor": "pointer", "fontFamily": "Inter, sans-serif",
            "textDecoration": "none", "display": "inline-block",
        },
    )

    return html.Div([
        year_selector,
        html.Div(id="pl-kpi-row", className="mb-4"),
        html.Div([
            html.Div([
                html.Div([
                    html.Div("Monthly P&L", style={"fontWeight": "700", "fontSize": "14px",
                                                   "color": TAAL_DARK}),
                    export_btn,
                ], style={"display": "flex", "justifyContent": "space-between",
                          "alignItems": "center", "marginBottom": "14px"}),
                pl_table_section,
                html.Div(
                    "* Revenue from POS system (GL not yet booked by accountant). "
                    "Will update automatically once GL entries are added.",
                    style={"fontSize": "10px", "color": TAAL_MUTED, "marginTop": "10px",
                           "fontStyle": "italic"}
                ) if ESTIMATED_MONTHS else None,
                html.Div(
                    f"† Wages estimated from Jan–Mar avg for {', '.join(PAYROLL_EST_MONTHS)} "
                    "(payroll PDF not yet available). Will update automatically when R4+ PDF is added.",
                    style={"fontSize": "10px", "color": TAAL_MUTED, "marginTop": "4px",
                           "fontStyle": "italic"}
                ) if PAYROLL_EST_MONTHS else None,
                html.Div([
                    html.Span("* ", style={"color": "#B8860B", "fontWeight": "700"}),
                    html.Span(
                        "Revenue includes payment-clearing GL balances (accounts 1204/1205/1206/1207/1208) "
                        "for months where the accounting period is not yet closed. "
                        "These will be replaced by formal 8xxx entries once the period is closed.",
                        style={"color": TAAL_MUTED}
                    ),
                ], style={"fontSize": "10px", "marginTop": "4px", "fontStyle": "italic"}
                ) if TRANSIT_MONTHS else None,
            ], style=CARD_STYLE),
        ], className="mb-4"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 – SALES PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────
def make_sales_tab():
    # Kiosk (Onesix) sales analytics from kiosk_transactions.csv
    kiosk_path = DATA / "kiosk_transactions.csv"
    try:
        kiosk = pd.read_csv(kiosk_path, parse_dates=["date"])
        kiosk = kiosk[kiosk["date"].notna()].copy()
        kiosk["amount_eur"] = pd.to_numeric(kiosk["amount_eur"], errors="coerce").fillna(0)
        kiosk["month"] = kiosk["date"].dt.to_period("M").astype(str)
        has_kiosk = len(kiosk) > 0
    except Exception:
        kiosk = pd.DataFrame()
        has_kiosk = False

    if not has_kiosk:
        return html.Div([
            html.Div("No kiosk sales data available.", style={"color": TAAL_MUTED, "padding": "40px"})
        ], style={"padding": "0 4px"})

    monthly_k = (kiosk.groupby("month")
                 .agg(orders=("amount_eur", "count"), revenue=("amount_eur", "sum"))
                 .reset_index()
                 .sort_values("month"))
    monthly_k["avg_ticket"] = monthly_k["revenue"] / monthly_k["orders"]
    monthly_k = monthly_k[monthly_k["month"] >= "2024-04"]
    x = [pd.Period(m, "M").strftime("%b %Y") for m in monthly_k["month"]]

    total_orders  = monthly_k["orders"].sum()
    total_revenue = monthly_k["revenue"].sum()
    avg_ticket    = (total_revenue / total_orders) if total_orders else 0
    best_idx      = monthly_k["revenue"].idxmax()
    best_m_label  = pd.Period(monthly_k.loc[best_idx, "month"], "M").strftime("%b %Y")
    best_m_rev    = monthly_k.loc[best_idx, "revenue"]

    kpi_row = dbc.Row([
        dbc.Col(kpi_card("Total Kiosk Revenue", fmt_eur(total_revenue), "Apr 2024 – Mar 2026"), width="auto"),
        dbc.Col(kpi_card("Total Orders",        f"{total_orders:,.0f}", "Completed orders"),     width="auto"),
        dbc.Col(kpi_card("Avg Ticket",          fmt_eur(avg_ticket),    "Per order"),             width="auto"),
        dbc.Col(kpi_card("Best Month",          best_m_label,           fmt_eur(best_m_rev)),     width="auto"),
    ], className="g-3 mb-4")

    # Chart: revenue + orders dual-axis
    fig_rev = make_subplots(specs=[[{"secondary_y": True}]])
    fig_rev.add_trace(go.Bar(
        name="Revenue (€)", x=x, y=monthly_k["revenue"].tolist(),
        marker_color=TAAL_GREEN, opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.0f}<extra></extra>",
    ), secondary_y=False)
    fig_rev.add_trace(go.Scatter(
        name="# Orders", x=x, y=monthly_k["orders"].tolist(),
        mode="lines+markers", line=dict(color=TAAL_BLUE, width=2),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>Orders: %{y:,.0f}<extra></extra>",
    ), secondary_y=True)
    apply_layout(fig_rev,
        height=340, barmode="group", margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Kiosk Monthly Revenue & Orders (Onesix)", font=dict(size=14)),
        yaxis=dict(title="Revenue (EUR)", tickformat=",.0f", **AXIS_STYLE),
        yaxis2=dict(title="# Orders", showgrid=False, linecolor="#E8E0D5"),
    )

    # Chart: average ticket
    fig_ticket = go.Figure()
    fig_ticket.add_trace(go.Scatter(
        name="Avg Ticket (€)", x=x, y=monthly_k["avg_ticket"].tolist(),
        mode="lines+markers+text",
        text=[f"€{v:.2f}" for v in monthly_k["avg_ticket"]],
        textposition="top center", textfont=dict(size=10),
        line=dict(color=TAAL_GREEN, width=2.5),
        marker=dict(size=8),
        hovertemplate="<b>%{x}</b><br>Avg ticket: €%{y:.2f}<extra></extra>",
    ))
    apply_layout(fig_ticket,
        height=300, showlegend=False, margin=MARGIN,
        title=dict(text="Average Ticket per Order", font=dict(size=14)),
        yaxis=dict(title="EUR", **AXIS_STYLE),
    )

    # Heatmap: orders by hour and weekday
    kiosk["hour"]    = kiosk["date"].dt.hour
    kiosk["weekday"] = kiosk["date"].dt.day_name()
    wday_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    heat_data  = kiosk.groupby(["weekday","hour"])["amount_eur"].sum().reset_index()
    heat_pivot = heat_data.pivot(index="weekday", columns="hour", values="amount_eur").fillna(0)
    heat_pivot = heat_pivot.reindex([d for d in wday_order if d in heat_pivot.index])

    fig_heat = go.Figure(go.Heatmap(
        z=heat_pivot.values,
        x=[f"{h:02d}:00" for h in heat_pivot.columns],
        y=heat_pivot.index.tolist(),
        colorscale=[[0,"white"],[0.3,TAAL_ACCENT],[0.7,TAAL_CLAY],[1,TAAL_DARK]],
        hovertemplate="<b>%{y} %{x}</b><br>Revenue: €%{z:,.0f}<extra></extra>",
        showscale=True,
        colorbar=dict(title="€", thickness=12, len=0.8),
    ))
    apply_layout(fig_heat,
        height=300, margin=dict(l=10, r=60, t=40, b=10),
        title=dict(text="Kiosk Revenue by Day & Hour", font=dict(size=14)),
        xaxis=dict(title="", **AXIS_STYLE),
        yaxis=dict(title="", **AXIS_STYLE),
    )

    # Payment method split
    pay_split = (kiosk.groupby("payment_method")["amount_eur"].sum()
                 .sort_values(ascending=False).reset_index())
    fig_pay = go.Figure(go.Pie(
        labels=pay_split["payment_method"],
        values=pay_split["amount_eur"],
        hole=0.5,
        hovertemplate="<b>%{label}</b><br>€%{value:,.0f} (%{percent})<extra></extra>",
        textinfo="label+percent",
    ))
    apply_layout(fig_pay,
        height=300, showlegend=False, margin=dict(l=10, r=10, t=40, b=10),
        title=dict(text="Payment Method Split", font=dict(size=14)),
    )

    return html.Div([
        kpi_row,
        html.Div([html.Div([dcc.Graph(figure=fig_rev, config={"displayModeBar": False})],
                           style=CARD_STYLE)], className="mb-4"),
        dbc.Row([
            dbc.Col(html.Div([dcc.Graph(figure=fig_ticket, config={"displayModeBar": False})],
                             style=CARD_STYLE), md=8),
            dbc.Col(html.Div([dcc.Graph(figure=fig_pay,    config={"displayModeBar": False})],
                             style=CARD_STYLE), md=4),
        ], className="g-3 mb-4"),
        html.Div([html.Div([dcc.Graph(figure=fig_heat, config={"displayModeBar": False})],
                           style=CARD_STYLE)], className="mb-4"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 – LABOR & COSTS
# ─────────────────────────────────────────────────────────────────────────────
def make_labor_tab():
    if labor.empty or "date" not in labor.columns:
        # Show GL-based labor summary instead
        om = OPERATING_MONTHS
        mx_om = month_x(om)
        rev_vals      = _pl_val("TOTAL REVENUE", om)
        labor_vals    = _pl_val("TOTAL LABOR", om)
        labor_pct     = (labor_vals / rev_vals.replace(0, np.nan) * 100).fillna(0)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(name="Revenue", x=mx_om, y=rev_vals.tolist(),
            marker_color=TAAL_CLAY, opacity=0.7,
            hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.0f}<extra></extra>"),
            secondary_y=False)
        fig.add_trace(go.Bar(name="Total Labor (GL)", x=mx_om, y=labor_vals.tolist(),
            marker_color=TAAL_RED, opacity=0.85,
            hovertemplate="<b>%{x}</b><br>Labor: €%{y:,.0f}<extra></extra>"),
            secondary_y=False)
        fig.add_trace(go.Scatter(name="Labor % of Revenue", x=mx_om, y=labor_pct.tolist(),
            mode="lines+markers", line=dict(color=TAAL_BLUE, width=2),
            hovertemplate="<b>%{x}</b><br>Labor %: %{y:.1f}%<extra></extra>"),
            secondary_y=True)
        apply_layout(fig, barmode="group", height=400, margin=MARGIN, legend=LEGEND_H,
            title=dict(text="Labor Cost from GL (no hours data)", font=dict(size=14)),
            yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
            yaxis2=dict(title="% of Revenue", ticksuffix="%", showgrid=False))
        return html.Div([
            html.Div([html.Div([dcc.Graph(figure=fig, config={"displayModeBar": False})],
                               style=CARD_STYLE)], className="mb-4"),
        ], style={"padding": "0 4px"})

    labor_clean = labor.copy()
    labor_clean["date"] = pd.to_datetime(labor_clean["date"], errors="coerce")
    labor_clean = labor_clean[labor_clean["date"].notna()].copy()
    labor_clean["month"] = labor_clean["date"].dt.to_period("M").astype(str)
    labor_clean["hours_decimal"] = pd.to_numeric(labor_clean["hours_decimal"], errors="coerce")
    labor_clean["estimated_cost_eur"] = pd.to_numeric(labor_clean["estimated_cost_eur"], errors="coerce")
    labor_clean["hourly_wage_eur"] = pd.to_numeric(labor_clean["hourly_wage_eur"], errors="coerce")

    # Monthly totals
    monthly_labor = labor_clean.groupby("month").agg(
        hours=("hours_decimal","sum"),
        cost=("estimated_cost_eur","sum"),
        shifts=("support_id","count"),
        staff=("employee","nunique"),
    ).reset_index()
    monthly_labor = monthly_labor.sort_values("month")
    mx = [pd.Period(m, "M").strftime("%b %Y") for m in monthly_labor["month"]]

    # KPIs
    total_hours = labor_clean["hours_decimal"].sum()
    total_cost  = labor_clean["estimated_cost_eur"].sum()
    avg_wage    = labor_clean["hourly_wage_eur"].mean()
    num_staff   = labor_clean["employee"].nunique()

    kpi_row = dbc.Row([
        dbc.Col(kpi_card("Total Hours", f"{total_hours:,.0f}h", "All periods"), width="auto"),
        dbc.Col(kpi_card("Est. Labor Cost", fmt_eur(total_cost), "Freelance contracts"), width="auto"),
        dbc.Col(kpi_card("Avg Hourly Rate", f"€{avg_wage:.0f}/h", "Weighted avg"), width="auto"),
        dbc.Col(kpi_card("Staff Members", str(num_staff), "Unique employees"), width="auto"),
    ], className="g-3 mb-4")

    # Chart: Hours by employee per month
    emp_month = labor_clean.groupby(["employee","month"])["hours_decimal"].sum().reset_index()
    employees = labor_clean.groupby("employee")["hours_decimal"].sum().sort_values(ascending=False).index.tolist()
    emp_colors = px.colors.qualitative.Set2

    fig_emp = go.Figure()
    for i, emp in enumerate(employees):
        sub = emp_month[emp_month["employee"] == emp].sort_values("month")
        sub_x = [pd.Period(m, "M").strftime("%b %Y") for m in sub["month"]]
        fig_emp.add_trace(go.Bar(
            name=emp.split()[0], x=sub_x, y=sub["hours_decimal"].tolist(),
            marker_color=emp_colors[i % len(emp_colors)],
            hovertemplate="<b>%{x}</b><br>" + emp + ": %{y:.1f}h<extra></extra>",
        ))
    apply_layout(fig_emp,
        barmode="stack", height=320, margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Hours Worked by Employee", font=dict(size=14)),
        yaxis=dict(title="Hours", **AXIS_STYLE),
    )

    # Chart: Labor cost vs GL outsourced cost side by side
    gl_out = ledger.copy()
    gl_out["account_code"] = gl_out["account_code"].astype(str)
    gl_out["net_amount"] = pd.to_numeric(gl_out["net_amount"], errors="coerce")
    gl_out["date"] = pd.to_datetime(gl_out["date"], errors="coerce")
    gl_out["month"] = gl_out["date"].dt.to_period("M").astype(str)
    outsourced_monthly = (gl_out[gl_out["account_code"].isin(["7100","7101"])]
                          .groupby("month")["net_amount"].sum().reset_index())
    outsourced_monthly = outsourced_monthly[outsourced_monthly["month"] >= "2024-04"]
    ox = [pd.Period(m, "M").strftime("%b %Y") for m in outsourced_monthly["month"]]

    fig_cost = go.Figure()
    fig_cost.add_trace(go.Bar(
        name="GL: Outsourced Staff (7100/7101)", x=ox, y=outsourced_monthly["net_amount"].tolist(),
        marker_color=TAAL_RED, opacity=0.85,
        hovertemplate="<b>%{x}</b><br>GL Cost: €%{y:,.0f}<extra></extra>",
    ))
    fig_cost.add_trace(go.Bar(
        name="Hours-based Estimate", x=mx, y=monthly_labor["cost"].tolist(),
        marker_color=TAAL_CLAY, opacity=0.75,
        hovertemplate="<b>%{x}</b><br>Hours Estimate: €%{y:,.0f}<extra></extra>",
    ))
    apply_layout(fig_cost,
        barmode="group", height=300, margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Freelance Cost: GL Invoiced vs Hours-based Estimate", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
    )

    # Donut: hours by employee
    emp_total = labor_clean.groupby("employee")["hours_decimal"].sum().reset_index()
    fig_emp_donut = go.Figure(go.Pie(
        labels=[e.split()[0] for e in emp_total["employee"]],
        values=emp_total["hours_decimal"].tolist(),
        hole=0.5,
        marker=dict(colors=emp_colors),
        hovertemplate="<b>%{label}</b><br>%{value:.0f}h (%{percent})<extra></extra>",
        textinfo="label+percent",
    ))
    apply_layout(fig_emp_donut,
        height=320, showlegend=False, margin=dict(l=10, r=10, t=40, b=10),
        title=dict(text="Hours Share by Employee", font=dict(size=14)),
    )

    # Hours by weekday
    labor_clean["weekday"] = labor_clean["date"].dt.day_name()
    wday_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    wday_h = labor_clean.groupby("weekday")["hours_decimal"].sum().reindex(wday_order).fillna(0)
    fig_wday = go.Figure(go.Bar(
        x=wday_order, y=wday_h.tolist(),
        marker_color=[TAAL_GREEN if d in ("Saturday","Sunday") else TAAL_CLAY for d in wday_order],
        hovertemplate="<b>%{x}</b><br>%{y:.0f}h<extra></extra>",
    ))
    apply_layout(fig_wday,
        height=260, showlegend=False, margin=MARGIN,
        title=dict(text="Total Hours by Weekday", font=dict(size=14)),
        yaxis=dict(title="Hours", **AXIS_STYLE),
    )

    # ── Main overview chart: Revenue + Margin % + Wages % + Freelancers % ──────
    om = OPERATING_MONTHS
    mx_om = month_x(om)

    rev_vals      = pl_series("TOTAL REVENUE", om)
    gp_pct_vals   = pl_series("GROSS MARGIN %", om)
    wages_vals    = sum(pl_series(l, om) for l in _WAGES_LINES)
    freelance_vals = sum(pl_series(l, om) for l in _OUTSOURCED_LINES)

    wages_pct      = (wages_vals / rev_vals.replace(0, np.nan) * 100).fillna(0)
    freelance_pct  = (freelance_vals / rev_vals.replace(0, np.nan) * 100).fillna(0)

    fig_overview = make_subplots(specs=[[{"secondary_y": True}]])

    fig_overview.add_trace(go.Bar(
        name="Revenue", x=mx_om, y=rev_vals.tolist(),
        marker_color=TAAL_CLAY, opacity=0.75,
        hovertemplate="<b>%{x}</b><br>Revenue: €%{y:,.0f}<extra></extra>",
    ), secondary_y=False)

    fig_overview.add_trace(go.Scatter(
        name="Gross Margin %", x=mx_om, y=gp_pct_vals.tolist(),
        mode="lines+markers", line=dict(color=TAAL_GREEN, width=2.5),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>Gross Margin: %{y:.1f}%<extra></extra>",
    ), secondary_y=True)

    fig_overview.add_trace(go.Scatter(
        name="Wages % of Revenue", x=mx_om, y=wages_pct.tolist(),
        mode="lines+markers", line=dict(color=TAAL_BLUE, width=2, dash="dot"),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>Wages: %{y:.1f}% of revenue<extra></extra>",
    ), secondary_y=True)

    fig_overview.add_trace(go.Scatter(
        name="Freelancers % of Revenue", x=mx_om, y=freelance_pct.tolist(),
        mode="lines+markers", line=dict(color=TAAL_RED, width=2, dash="dash"),
        marker=dict(size=7),
        hovertemplate="<b>%{x}</b><br>Freelancers: %{y:.1f}% of revenue<extra></extra>",
    ), secondary_y=True)

    apply_layout(fig_overview,
        height=380, margin=MARGIN, legend=LEGEND_H,
        title=dict(text="Revenue, Margin & Labor Cost Weight by Month", font=dict(size=15)),
        yaxis=dict(title="Revenue (EUR)", tickformat=",.0f", **AXIS_STYLE),
        yaxis2=dict(title="% of Revenue / Margin", ticksuffix="%", **AXIS_STYLE,
                    showgrid=False),
    )

    return html.Div([
        kpi_row,
        dbc.Row([
            dbc.Col(html.Div([dcc.Graph(figure=fig_overview, config={"displayModeBar": False})], style=CARD_STYLE), md=12),
        ], className="g-3 mb-4"),
        dbc.Row([
            dbc.Col(html.Div([dcc.Graph(figure=fig_emp,   config={"displayModeBar": False})], style=CARD_STYLE), md=8),
            dbc.Col(html.Div([dcc.Graph(figure=fig_emp_donut, config={"displayModeBar": False})], style=CARD_STYLE), md=4),
        ], className="g-3 mb-4"),
        dbc.Row([
            dbc.Col(html.Div([dcc.Graph(figure=fig_cost,  config={"displayModeBar": False})], style=CARD_STYLE), md=7),
            dbc.Col(html.Div([dcc.Graph(figure=fig_wday,  config={"displayModeBar": False})], style=CARD_STYLE), md=5),
        ], className="g-3 mb-4"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 – PRODUCT SALES
# ─────────────────────────────────────────────────────────────────────────────
# Consolidate small / misc categories into cleaner groups
CATEGORY_MAP = {
    "Black Coffee":     "Black Coffee",
    "Coffee with milk": "Coffee with Milk",
    "Iced Coffee":      "Iced Coffee",
    "Iced Black Coffee":"Iced Coffee",
    "Iced Milk Coffee": "Iced Coffee",
    "Matcha":           "Matcha",
    "Matcha Specials":  "Matcha Specials",
    "Retail Matcha":    "Retail Matcha",
    "Bakery":           "Bakery",
    "Food":             "Food",
    "Beverage":         "Beverage",
    "Tea":              "Tea",
    "Milk":             "Milk",
    "Specials":         "Specials",
    "Affogato":         "Specials",
    "Coffee beans":     "Retail",
    "Retail ":          "Retail",
    "Syrups":           "Other",
    "Misc":             "Other",
    "Other":            "Other",
}

CAT_COLORS = [
    "#4A6741","#7A9F74","#B8D4B3","#C4A882","#DCC8A8",
    "#3A5A7C","#6A8AAC","#9AB0C4","#8B3A2F","#C4704A",
    "#E8C88A","#5A3A7C","#9A7AB8","#E8A878","#D0C8BC",
]


def _build_top5_section(prod, periods_sorted):
    """Top 5 products grouped bar chart + period breakdown table."""
    top5_names = (prod.groupby("product_name")["gross_revenue_eur"].sum()
                  .sort_values(ascending=False).head(5).index.tolist())
    t5 = prod[prod["product_name"].isin(top5_names)].copy()

    # Short labels for periods
    def _period_label(p):
        parts = str(p).split(" to ")
        if len(parts) == 2:
            return f"{pd.Period(parts[0], 'M').strftime('%b')}–{pd.Period(parts[1], 'M').strftime('%b %y')}"
        return p
    period_labels = [_period_label(p) for p in periods_sorted]

    # Grouped bar chart: one group per product, one bar per period
    fig = go.Figure()
    colors = [TAAL_GREEN, TAAL_BLUE, "#E8C88A", TAAL_RED, "#5A3A7C"]
    for i, period in enumerate(periods_sorted):
        sub = t5[t5["period"] == period].set_index("product_name")
        vals = [float(sub.loc[n, "gross_revenue_eur"]) if n in sub.index else 0 for n in top5_names]
        fig.add_trace(go.Bar(
            name=period_labels[i], x=top5_names, y=vals,
            marker_color=colors[i % len(colors)],
            text=[f"€{v:,.0f}" if v else "" for v in vals], textposition="outside",
        ))
    apply_layout(fig, barmode="group", height=400, margin=MARGIN,
        title=dict(text="Top 5 Products — Revenue by Period", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    # Table: product × period with qty + revenue
    _hdr_s = {"padding": "8px 12px", "fontSize": "11px", "fontWeight": "600",
              "backgroundColor": TAAL_DARK, "color": "white"}
    _cell = {"padding": "6px 12px", "fontSize": "12px", "borderBottom": "1px solid #F0EBE3"}

    header_cells = [html.Th("Product", style={**_hdr_s, "textAlign": "left"})]
    for pl in period_labels:
        header_cells.append(html.Th(f"{pl} Qty", style={**_hdr_s, "textAlign": "right"}))
        header_cells.append(html.Th(f"{pl} Rev", style={**_hdr_s, "textAlign": "right"}))
    header_cells.append(html.Th("Total Qty", style={**_hdr_s, "textAlign": "right"}))
    header_cells.append(html.Th("Total Rev", style={**_hdr_s, "textAlign": "right"}))

    rows = []
    for name in top5_names:
        cells = [html.Td(name, style={**_cell, "fontWeight": "500"})]
        total_qty, total_rev = 0, 0.0
        for period in periods_sorted:
            sub = t5[(t5["product_name"] == name) & (t5["period"] == period)]
            qty = int(sub["items_sold"].sum()) if len(sub) else 0
            rev = float(sub["gross_revenue_eur"].sum()) if len(sub) else 0
            total_qty += qty
            total_rev += rev
            cells.append(html.Td(f"{qty:,}" if qty else "—",
                         style={**_cell, "textAlign": "right", "color": TAAL_DARK if qty else TAAL_MUTED}))
            cells.append(html.Td(f"€{rev:,.0f}" if rev else "—",
                         style={**_cell, "textAlign": "right", "color": TAAL_DARK if rev else TAAL_MUTED}))
        cells.append(html.Td(f"{total_qty:,}", style={**_cell, "textAlign": "right", "fontWeight": "700"}))
        cells.append(html.Td(f"€{total_rev:,.0f}", style={**_cell, "textAlign": "right",
                     "fontWeight": "700", "color": TAAL_GREEN}))
        rows.append(html.Tr(cells))

    table = html.Table([html.Thead(html.Tr(header_cells)), html.Tbody(rows)],
                       style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([
        html.Div([dcc.Graph(figure=fig, config={"displayModeBar": False})],
                 style={**CARD_STYLE, "marginBottom": "16px"}),
        html.Div([
            html.Div("Top 5 Products — Period Breakdown", style={
                "fontWeight": "700", "fontSize": "14px", "marginBottom": "12px", "color": TAAL_DARK}),
            html.Div(table, style={"overflowX": "auto"}),
        ], style=CARD_STYLE),
    ], className="mb-4")


def make_product_tab():
    if products.empty or "gross_revenue_eur" not in products.columns:
        return html.Div([
            html.Div("No product sales data available.", style={"color": TAAL_MUTED, "padding": "40px"})
        ], style={"padding": "0 4px"})
    prod = products.copy()
    prod["category"] = prod["category"].astype(str)

    for col in ["items_sold", "net_revenue_eur", "gross_revenue_eur", "margin_eur", "margin_pct"]:
        if col in prod.columns:
            prod[col] = pd.to_numeric(prod[col], errors="coerce").fillna(0)

    periods_sorted = sorted(prod["period"].unique()) if "period" in prod.columns else []

    # ── KPI row ───────────────────────────────────────────────────────────────
    total_rev    = prod["gross_revenue_eur"].sum()
    total_items  = prod["items_sold"].sum()
    n_products   = prod["product_name"].nunique() if "product_name" in prod.columns else 0

    kpi_row = html.Div([
        kpi_card("Retail Revenue", fmt_eur(total_rev), "Tebi POS"),
        kpi_card("Items Sold", f"{total_items:,.0f}", "All periods"),
        kpi_card("Products", f"{n_products}", "Unique SKUs"),
        kpi_card("Periods", f"{len(periods_sorted)}", "Tebi reports"),
    ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"})

    # ── MATCHA TINS/CANS combined section ────────────────────────────────────
    _MATCHA_TIN_KW = ["LERA Matcha, Kyushu", "LERA Matcha, Uji"]
    matcha_tins = prod[prod["product_name"].str.contains("|".join(_MATCHA_TIN_KW), case=False, na=False)]

    # Onesix estimated matcha tin sales (€42 orders → likely single tin purchase)
    kiosk_tx = _safe_csv(DATA / "kiosk_transactions.csv")
    onesix_matcha = pd.DataFrame()
    if not kiosk_tx.empty and "amount_eur" in kiosk_tx.columns:
        kiosk_tx["amount_eur"] = pd.to_numeric(kiosk_tx["amount_eur"], errors="coerce")
        kiosk_tx["date"] = pd.to_datetime(kiosk_tx["date"], errors="coerce")
        kiosk_tx["month"] = kiosk_tx["date"].dt.to_period("M").astype(str)
        tin_orders = kiosk_tx[kiosk_tx["amount_eur"] == 42.0]
        if not tin_orders.empty:
            onesix_matcha = tin_orders.groupby("month").agg(
                items=("amount_eur", "count"),
                revenue=("amount_eur", "sum"),
            ).reset_index()
            onesix_matcha["source"] = "Onesix (Kiosk)"

    # Tebi matcha tins by period
    tebi_tins = matcha_tins.groupby(["period", "product_name"]).agg(
        items=("items_sold", "sum"),
        revenue=("gross_revenue_eur", "sum"),
    ).reset_index()

    # Matcha tins summary table
    def _build_matcha_section():
        rows = []
        _hdr_s = {"padding": "8px 12px", "fontSize": "11px", "fontWeight": "600",
                  "backgroundColor": TAAL_DARK, "color": "white"}

        header = html.Tr([
            html.Th("Source / Product", style={**_hdr_s, "textAlign": "left"}),
            html.Th("Period", style={**_hdr_s, "textAlign": "left"}),
            html.Th("Qty", style={**_hdr_s, "textAlign": "right"}),
            html.Th("Revenue", style={**_hdr_s, "textAlign": "right"}),
        ])

        _cell = {"padding": "6px 12px", "fontSize": "12px", "borderBottom": "1px solid #F0EBE3"}
        total_qty, total_rev_t = 0, 0.0

        for _, r in tebi_tins.iterrows():
            total_qty += int(r["items"])
            total_rev_t += r["revenue"]
            rows.append(html.Tr([
                html.Td(f"Tebi · {r['product_name']}", style={**_cell, "color": TAAL_DARK}),
                html.Td(r["period"], style={**_cell, "color": TAAL_MUTED}),
                html.Td(f"{int(r['items']):,}", style={**_cell, "textAlign": "right"}),
                html.Td(f"€{r['revenue']:,.0f}", style={**_cell, "textAlign": "right"}),
            ]))

        if not onesix_matcha.empty:
            for _, r in onesix_matcha.iterrows():
                total_qty += int(r["items"])
                total_rev_t += r["revenue"]
                rows.append(html.Tr([
                    html.Td(f"Onesix · Matcha Tin (€42)", style={**_cell, "color": TAAL_DARK}),
                    html.Td(r["month"], style={**_cell, "color": TAAL_MUTED}),
                    html.Td(f"{int(r['items']):,}", style={**_cell, "textAlign": "right"}),
                    html.Td(f"€{r['revenue']:,.0f}", style={**_cell, "textAlign": "right"}),
                ]))

        # Total row
        rows.append(html.Tr([
            html.Td("TOTAL", style={**_cell, "fontWeight": "700", "color": TAAL_GREEN}),
            html.Td("", style=_cell),
            html.Td(f"{total_qty:,}", style={**_cell, "textAlign": "right", "fontWeight": "700"}),
            html.Td(f"€{total_rev_t:,.0f}", style={**_cell, "textAlign": "right", "fontWeight": "700",
                     "color": TAAL_GREEN}),
        ], style={"borderTop": "2px solid #D0C8B8"}))

        return html.Table([html.Thead(header), html.Tbody(rows)],
                          style={"width": "100%", "borderCollapse": "collapse"})

    # ── Category revenue bar chart ───────────────────────────────────────────
    cat_totals = prod.groupby("category").agg(
        items_sold=("items_sold", "sum"),
        revenue=("gross_revenue_eur", "sum"),
    ).reset_index().sort_values("revenue", ascending=False)

    fig_cat = go.Figure(go.Bar(
        x=cat_totals["category"], y=cat_totals["revenue"],
        marker_color=[CAT_COLORS[i % len(CAT_COLORS)] for i in range(len(cat_totals))],
        text=[f"€{v:,.0f}" for v in cat_totals["revenue"]],
        textposition="outside",
    ))
    apply_layout(fig_cat, height=380, margin=MARGIN, showlegend=False,
        title=dict(text="Revenue by Category (Tebi Retail)", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
    )

    # ── Category donut ───────────────────────────────────────────────────────
    fig_donut = go.Figure(go.Pie(
        labels=cat_totals["category"].tolist(),
        values=cat_totals["revenue"].tolist(),
        hole=0.5, textinfo="label+percent",
        marker=dict(colors=CAT_COLORS[:len(cat_totals)]),
    ))
    apply_layout(fig_donut, height=360, showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
        title=dict(text="Category Mix (Tebi)", font=dict(size=14)),
    )

    # ── Top products table ────────────────────────────────────────────────────
    top = prod.groupby(["category", "product_name"]).agg(
        items_sold=("items_sold", "sum"),
        revenue=("gross_revenue_eur", "sum"),
    ).reset_index().sort_values("revenue", ascending=False).head(25)

    _hdr_s = {"backgroundColor": TAAL_DARK, "color": "white", "padding": "8px 12px",
              "fontSize": "11px", "fontWeight": "600"}
    _cell = {"padding": "6px 12px", "fontSize": "12px", "borderBottom": "1px solid #F0EBE3"}

    top_table = html.Table([
        html.Thead(html.Tr([
            html.Th("Category", style={**_hdr_s, "textAlign": "left"}),
            html.Th("Product", style={**_hdr_s, "textAlign": "left"}),
            html.Th("Qty", style={**_hdr_s, "textAlign": "right"}),
            html.Th("Revenue", style={**_hdr_s, "textAlign": "right"}),
        ])),
        html.Tbody([
            html.Tr([
                html.Td(r["category"], style={**_cell, "color": TAAL_MUTED}),
                html.Td(r["product_name"], style=_cell),
                html.Td(f"{int(r['items_sold']):,}", style={**_cell, "textAlign": "right"}),
                html.Td(f"€{r['revenue']:,.0f}", style={**_cell, "textAlign": "right"}),
            ]) for _, r in top.iterrows()
        ]),
    ], style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([
        kpi_row,
        # ── Matcha Tins section ──────────────────────────────────────────────
        html.Div([
            html.Div("Matcha Tins / Cans — Combined (Tebi + Onesix)", style={
                "fontWeight": "700", "fontSize": "14px", "marginBottom": "14px", "color": TAAL_GREEN}),
            _build_matcha_section(),
        ], style=CARD_STYLE, className="mb-4"),
        # ── Charts ───────────────────────────────────────────────────────────
        html.Div([
            html.Div([dcc.Graph(figure=fig_cat, config={"displayModeBar": False})],
                     style={**CARD_STYLE, "flex": "1", "minWidth": "420px"}),
            html.Div([dcc.Graph(figure=fig_donut, config={"displayModeBar": False})],
                     style={**CARD_STYLE, "flex": "1", "minWidth": "340px"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"}),
        # ── Top 5 products period breakdown ──────────────────────────────────
        _build_top5_section(prod, periods_sorted),
        # ── Top products ─────────────────────────────────────────────────────
        html.Div([
            html.Div("Top 25 Products by Revenue (Tebi Retail)",
                     style={"fontWeight": "700", "fontSize": "14px", "marginBottom": "14px", "color": TAAL_DARK}),
            html.Div(top_table, style={"overflowX": "auto"}),
        ], style=CARD_STYLE, className="mb-4"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# TAB – SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
_SOURCE_FOLDERS = {
    "Costs (GL ledger)":   BASE / "Costs",
    "Sales (POS reports)": BASE / "Sales",
    "Hours":               BASE / "Hours",
    "Labor":               BASE / "Labor",
    "Product sales":       BASE / "Product sales",
}
_DASHBOARD_FILES = {
    "monthly_pl.csv":      DATA / "monthly_pl.csv",
    "monthly_kpis.csv":    DATA / "monthly_kpis.csv",
    "costs_ledger.csv":    DATA / "costs_ledger.csv",
    "labor_hours.csv":     DATA / "labor_hours.csv",
    "product_sales.csv":   DATA / "product_sales.csv",
    "transactions.csv":    DATA / "transactions.csv",
}


def _file_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return pd.Timestamp(ts, unit="s").strftime("%d %b %Y  %H:%M")
    except Exception:
        return "—"


def _folder_file_count(path: Path) -> int:
    try:
        return len([f for f in path.iterdir()
                    if f.is_file() and not f.name.startswith(".")])
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# TAB – E-COMMERCE (Shopify)
# ─────────────────────────────────────────────────────────────────────────────
def make_ecom_tab():
    if ecom_raw.empty:
        return html.Div("No e-commerce data available.", style={"padding": "40px", "color": TAAL_MUTED})

    ec = ecom_raw.copy()
    ec["month"] = ec["month"].astype(str)
    ecom_months = sorted(ec["month"].unique())

    # ── Monthly summary ──────────────────────────────────────────────────────
    monthly = ec.groupby("month").agg(
        orders=("orders", "sum"),
        gross_sales=("gross_sales", "sum"),
        discounts=("discounts", "sum"),
        returns=("returns", "sum"),
        net_sales=("net_sales", "sum"),
        shipping=("shipping_charges", "sum"),
        taxes=("taxes", "sum"),
        total_sales=("total_sales", "sum"),
        countries=("billing_country", "nunique"),
    ).reset_index()

    # Monthly revenue bar chart
    fig_rev = go.Figure()
    fig_rev.add_trace(go.Bar(
        x=monthly["month"], y=monthly["net_sales"],
        name="Net Sales", marker_color=TAAL_GREEN,
    ))
    fig_rev.add_trace(go.Bar(
        x=monthly["month"], y=monthly["shipping"],
        name="Shipping", marker_color=TAAL_CLAY,
    ))
    fig_rev.update_layout(
        **CHART_LAYOUT, barmode="stack",
        title=dict(text="E-com Monthly Revenue", font=dict(size=14)),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
        xaxis=dict(title="", **AXIS_STYLE),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    # Orders bar chart
    fig_orders = go.Figure()
    fig_orders.add_trace(go.Bar(
        x=monthly["month"], y=monthly["orders"],
        marker_color=TAAL_BLUE,
    ))
    fig_orders.update_layout(
        **CHART_LAYOUT,
        title=dict(text="E-com Monthly Orders", font=dict(size=14)),
        yaxis=dict(title="Orders", tickformat=",d", **AXIS_STYLE),
        xaxis=dict(title="", **AXIS_STYLE),
        showlegend=False,
    )

    # ── Country breakdown ────────────────────────────────────────────────────
    by_country = ec.groupby("billing_country").agg(
        orders=("orders", "sum"),
        net_sales=("net_sales", "sum"),
        total_sales=("total_sales", "sum"),
    ).reset_index().sort_values("net_sales", ascending=False)

    fig_geo = px.pie(
        by_country[by_country["net_sales"] > 0],
        values="net_sales", names="billing_country",
        color_discrete_sequence=px.colors.qualitative.Set3,
    )
    fig_geo.update_layout(
        **CHART_LAYOUT,
        title=dict(text="Net Sales by Country", font=dict(size=14)),
    )
    fig_geo.update_traces(textposition="inside", textinfo="percent+label")

    # ── KPI cards ────────────────────────────────────────────────────────────
    total_net     = ec["net_sales"].sum()
    total_orders  = int(ec["orders"].sum())
    total_gross   = ec["gross_sales"].sum()
    total_returns = ec["returns"].sum()
    n_countries   = ec["billing_country"].nunique()
    avg_order     = total_net / total_orders if total_orders else 0

    def kpi(label, value):
        return html.Div([
            html.Div(label, style={"fontSize": "10px", "color": TAAL_MUTED, "textTransform": "uppercase",
                                   "letterSpacing": "0.05em", "marginBottom": "4px"}),
            html.Div(value, style={"fontSize": "22px", "fontWeight": "700", "color": TAAL_DARK}),
        ], style={**CARD_STYLE, "minWidth": "140px", "textAlign": "center"})

    kpis_row = html.Div([
        kpi("Total Net Sales", f"€{total_net:,.0f}"),
        kpi("Total Orders", f"{total_orders:,}"),
        kpi("Avg Order", f"€{avg_order:,.1f}"),
        kpi("Countries", f"{n_countries}"),
        kpi("Returns", f"€{abs(total_returns):,.0f}"),
    ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"})

    # ── Country monthly table ────────────────────────────────────────────────
    recent_months = ecom_months[-6:]
    pivot = ec.groupby(["billing_country", "month"])["net_sales"].sum().reset_index()
    pivot = pivot.pivot(index="billing_country", columns="month", values="net_sales").fillna(0)
    pivot["TOTAL"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("TOTAL", ascending=False)

    header = [html.Th("Country", style={"textAlign": "left", "padding": "6px 12px",
              "fontSize": "10px", "color": TAAL_MUTED})]
    for m in recent_months:
        header.append(html.Th(pd.Period(m, "M").strftime("%b %y"),
                      style={"textAlign": "right", "padding": "6px 12px",
                             "fontSize": "10px", "color": TAAL_MUTED}))
    header.append(html.Th("Total", style={"textAlign": "right", "padding": "6px 12px",
                  "fontSize": "10px", "color": TAAL_MUTED, "fontWeight": "700"}))

    rows = []
    for country in pivot.index[:15]:
        cells = [html.Td(country, style={"padding": "5px 12px", "fontSize": "12px",
                 "color": TAAL_DARK})]
        for m in recent_months:
            v = pivot.loc[country].get(m, 0)
            cells.append(html.Td(f"€{v:,.0f}" if v else "—",
                         style={"textAlign": "right", "padding": "5px 12px",
                                "fontSize": "12px", "color": TAAL_DARK if v else TAAL_MUTED}))
        t = pivot.loc[country, "TOTAL"]
        cells.append(html.Td(f"€{t:,.0f}", style={"textAlign": "right", "padding": "5px 12px",
                     "fontSize": "12px", "fontWeight": "600", "color": TAAL_DARK}))
        rows.append(html.Tr(cells, style={"borderBottom": "1px solid #E8E0D4"}))

    country_table = html.Table([
        html.Thead(html.Tr(header), style={"borderBottom": "2px solid #D0C8B8"}),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([
        kpis_row,
        html.Div([
            html.Div([
                html.Div(dcc.Graph(figure=fig_rev, config={"displayModeBar": False}),
                         style={**CARD_STYLE}),
            ], style={"flex": "1", "minWidth": "420px"}),
            html.Div([
                html.Div(dcc.Graph(figure=fig_orders, config={"displayModeBar": False}),
                         style={**CARD_STYLE}),
            ], style={"flex": "1", "minWidth": "420px"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"}),
        html.Div([
            html.Div([
                html.Div(dcc.Graph(figure=fig_geo, config={"displayModeBar": False}),
                         style={**CARD_STYLE}),
            ], style={"flex": "1", "minWidth": "340px"}),
            html.Div([
                html.Div("Sales by Country (last 6 months)", style={
                    "fontWeight": "700", "fontSize": "14px", "marginBottom": "12px", "color": TAAL_DARK}),
                country_table,
            ], style={**CARD_STYLE, "flex": "2", "minWidth": "480px"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}),
    ], style={"padding": "0 4px"})


def make_settings_tab(creds=None):
    creds = creds or {}
    S = {"fontFamily": "Inter, sans-serif"}

    # ── Section: Source folders ───────────────────────────────────────────────
    folder_rows = []
    for label, folder in _SOURCE_FOLDERS.items():
        count = _folder_file_count(folder)
        try:
            latest = max((f.stat().st_mtime for f in folder.iterdir()
                          if f.is_file() and not f.name.startswith(".")),
                         default=0)
            latest_str = pd.Timestamp(latest, unit="s").strftime("%d %b %Y  %H:%M") if latest else "—"
        except Exception:
            latest_str = "—"
        folder_rows.append(html.Tr([
            html.Td(label,       style={"padding": "8px 16px", "fontSize": "13px", "fontWeight": "500"}),
            html.Td(str(folder.name), style={"padding": "8px 16px", "fontSize": "12px", "color": TAAL_MUTED, "fontFamily": "monospace"}),
            html.Td(f"{count} file{'s' if count != 1 else ''}",
                    style={"padding": "8px 16px", "fontSize": "12px", "textAlign": "right"}),
            html.Td(latest_str,  style={"padding": "8px 16px", "fontSize": "12px", "color": TAAL_MUTED, "textAlign": "right"}),
        ]))

    source_table = html.Table(
        [html.Thead(html.Tr([
            html.Th(h, style={"padding": "8px 16px", "fontSize": "11px", "fontWeight": "600",
                              "textTransform": "uppercase", "letterSpacing": "0.05em",
                              "color": TAAL_MUTED, "borderBottom": "2px solid #E8E0D5"})
            for h in ["Folder", "Path", "Files", "Latest file"]
        ])),
         html.Tbody(folder_rows)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )

    # ── Section: Dashboard data files ─────────────────────────────────────────
    data_rows = []
    for fname, fpath in _DASHBOARD_FILES.items():
        mtime = _file_mtime(fpath)
        try:
            size_kb = fpath.stat().st_size / 1024
            size_str = f"{size_kb:.0f} KB"
        except Exception:
            size_str = "—"
        data_rows.append(html.Tr([
            html.Td(fname,    style={"padding": "8px 16px", "fontSize": "12px",
                                     "fontFamily": "monospace", "fontWeight": "500"}),
            html.Td(size_str, style={"padding": "8px 16px", "fontSize": "12px",
                                     "textAlign": "right", "color": TAAL_MUTED}),
            html.Td(mtime,    style={"padding": "8px 16px", "fontSize": "12px",
                                     "textAlign": "right", "color": TAAL_MUTED}),
        ]))

    data_table = html.Table(
        [html.Thead(html.Tr([
            html.Th(h, style={"padding": "8px 16px", "fontSize": "11px", "fontWeight": "600",
                              "textTransform": "uppercase", "letterSpacing": "0.05em",
                              "color": TAAL_MUTED, "borderBottom": "2px solid #E8E0D5"})
            for h in ["File", "Size", "Last modified"]
        ])),
         html.Tbody(data_rows)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )

    # ── Section: Refresh button ───────────────────────────────────────────────
    refresh_section = html.Div([
        html.Div("Data Refresh", style={"fontWeight": "700", "fontSize": "15px",
                                         "color": TAAL_DARK, "marginBottom": "6px"}),
        html.Div(
            "Runs the full pipeline to process any new files added to the source folders "
            "and rebuild all dashboard CSV files. The page will reload automatically when done.",
            style={"fontSize": "12px", "color": TAAL_MUTED, "marginBottom": "18px"},
        ),
        dbc.Row([
            dbc.Col([
                html.Button(
                    "Refresh Data",
                    id="refresh-btn",
                    style={
                        "backgroundColor": TAAL_DARK,
                        "color": "white",
                        "border": "none",
                        "borderRadius": "6px",
                        "padding": "10px 24px",
                        "fontSize": "13px",
                        "fontWeight": "600",
                        "cursor": "pointer",
                        "letterSpacing": "0.02em",
                    },
                ),
            ], width="auto"),
            dbc.Col([
                html.Button(
                    "Reload Dashboard",
                    id="reload-btn",
                    style={
                        "backgroundColor": "white",
                        "color": TAAL_DARK,
                        "border": f"1px solid {TAAL_CLAY}",
                        "borderRadius": "6px",
                        "padding": "10px 24px",
                        "fontSize": "13px",
                        "fontWeight": "600",
                        "cursor": "pointer",
                        "letterSpacing": "0.02em",
                    },
                ),
            ], width="auto"),
            dbc.Col([
                html.Button(
                    "Stop & Free Port 8050",
                    id="stop-btn",
                    title="Kills this dashboard process and releases port 8050 for another dashboard",
                    style={
                        "backgroundColor": "white",
                        "color": TAAL_RED,
                        "border": f"1px solid {TAAL_RED}",
                        "borderRadius": "6px",
                        "padding": "10px 24px",
                        "fontSize": "13px",
                        "fontWeight": "600",
                        "cursor": "pointer",
                        "letterSpacing": "0.02em",
                    },
                ),
            ], width="auto"),
            dbc.Col([
                html.Div(id="refresh-status", style={"fontSize": "13px", "paddingTop": "10px"}),
            ]),
        ], align="center"),
        dcc.Loading(
            id="refresh-loading",
            type="circle",
            color=TAAL_GREEN,
            children=html.Div(id="refresh-loading-output"),
        ),
    ], style={**CARD_STYLE, "marginBottom": "20px"})

    source_section = html.Div([
        html.Div("Source Folders", style={"fontWeight": "700", "fontSize": "15px",
                                           "color": TAAL_DARK, "marginBottom": "14px"}),
        html.Div(source_table, style={"overflowX": "auto"}),
    ], style={**CARD_STYLE, "marginBottom": "20px"})

    data_section = html.Div([
        html.Div("Dashboard Data Files", style={"fontWeight": "700", "fontSize": "15px",
                                                  "color": TAAL_DARK, "marginBottom": "14px"}),
        html.Div(data_table, style={"overflowX": "auto"}),
    ], style={**CARD_STYLE, "marginBottom": "20px"})

    # ── Section: Traffic-light thresholds ─────────────────────────────────────
    def color_pill(color, label):
        return html.Span([
            html.Span(style={"display": "inline-block", "width": "12px", "height": "12px",
                             "borderRadius": "50%", "backgroundColor": color,
                             "marginRight": "6px", "verticalAlign": "middle"}),
            html.Span(label, style={"fontSize": "12px", "verticalAlign": "middle"}),
        ], style={"marginRight": "16px"})

    threshold_section = html.Div([
        html.Div("% of Revenue — Traffic-light Thresholds",
                 style={"fontWeight": "700", "fontSize": "15px",
                        "color": TAAL_DARK, "marginBottom": "6px"}),
        html.Div(
            "Controls the colour coding of the '% Rev' column in the Monthly P&L table. "
            "Values below the green threshold show in green; between the two thresholds in amber; above in red.",
            style={"fontSize": "12px", "color": TAAL_MUTED, "marginBottom": "20px"},
        ),

        # Live legend
        html.Div(id="threshold-legend", style={"marginBottom": "20px"}),

        dbc.Row([
            # Green / Amber boundary
            dbc.Col([
                html.Div([
                    html.Label("Green  →  Amber boundary",
                               style={"fontSize": "12px", "fontWeight": "600",
                                      "color": TAAL_DARK, "marginBottom": "8px",
                                      "display": "block"}),
                    dbc.Row([
                        dbc.Col(
                            dcc.Slider(
                                id="thresh-low-slider",
                                min=1, max=40, step=0.5,
                                value=8,
                                marks={v: f"{v}%" for v in [1, 5, 10, 15, 20, 25, 30, 35, 40]},
                                tooltip={"placement": "bottom", "always_visible": False},
                                className="mb-2",
                            ), width=9,
                        ),
                        dbc.Col(
                            dbc.InputGroup([
                                dbc.Input(id="thresh-low-input", type="number",
                                          min=1, max=40, step=0.5, value=8,
                                          style={"fontSize": "13px", "textAlign": "right"}),
                                dbc.InputGroupText("%", style={"fontSize": "12px"}),
                            ], size="sm"),
                            width=3,
                        ),
                    ], align="center"),
                ]),
            ], md=6),

            # Amber / Red boundary
            dbc.Col([
                html.Div([
                    html.Label("Amber  →  Red boundary",
                               style={"fontSize": "12px", "fontWeight": "600",
                                      "color": TAAL_DARK, "marginBottom": "8px",
                                      "display": "block"}),
                    dbc.Row([
                        dbc.Col(
                            dcc.Slider(
                                id="thresh-high-slider",
                                min=1, max=60, step=0.5,
                                value=18,
                                marks={v: f"{v}%" for v in [5, 10, 15, 20, 25, 30, 40, 50, 60]},
                                tooltip={"placement": "bottom", "always_visible": False},
                                className="mb-2",
                            ), width=9,
                        ),
                        dbc.Col(
                            dbc.InputGroup([
                                dbc.Input(id="thresh-high-input", type="number",
                                          min=1, max=60, step=0.5, value=18,
                                          style={"fontSize": "13px", "textAlign": "right"}),
                                dbc.InputGroupText("%", style={"fontSize": "12px"}),
                            ], size="sm"),
                            width=3,
                        ),
                    ], align="center"),
                ]),
            ], md=6),
        ], className="mb-4"),

        # Visual gradient bar
        html.Div(id="threshold-bar"),

    ], style={**CARD_STYLE, "marginBottom": "20px"})

    # ── Section: Eitje API credentials ───────────────────────────────────────
    def cred_input(label, id_, placeholder, stored_value="", is_password=False):
        return html.Div([
            html.Label(label, style={"fontSize": "12px", "fontWeight": "600",
                                     "color": TAAL_DARK, "marginBottom": "4px",
                                     "display": "block"}),
            dcc.Input(
                id=id_, type="password" if is_password else "text",
                placeholder=placeholder,
                value=stored_value or "",
                debounce=True,
                style={"width": "100%", "padding": "8px 12px",
                       "border": f"1px solid {TAAL_CLAY}", "borderRadius": "6px",
                       "fontSize": "13px", "fontFamily": "Inter, sans-serif",
                       "backgroundColor": "white"},
            ),
        ], style={"marginBottom": "14px"})

    btn_style = {"backgroundColor": "white", "color": TAAL_DARK,
                 "border": f"1px solid {TAAL_CLAY}", "borderRadius": "6px",
                 "padding": "8px 20px", "fontSize": "12px",
                 "fontWeight": "600", "cursor": "pointer", "marginRight": "8px"}

    eitje_section = html.Div([
        html.Div("Eitje API", style={"fontWeight": "700", "fontSize": "15px",
                                     "color": TAAL_DARK, "marginBottom": "6px"}),
        html.Div([
            "Used to fetch scheduled shifts for the Scheduling tab. ",
            html.Br(),
            html.Span("API Username/Password", style={"fontWeight": "600"}),
            " — from Eitje Settings → Integrations → Open API.",
            html.Br(),
            html.Span("Partner Username/Password", style={"fontWeight": "600"}),
            " — separate partner-account credentials from Eitje (contact Eitje support to get these).",
        ], style={"fontSize": "12px", "color": TAAL_MUTED, "marginBottom": "18px",
                  "lineHeight": "1.7"}),
        dbc.Row([
            dbc.Col(cred_input("API Username", "eitje-username", "e.g. TY7kDY",
                               stored_value=creds.get("username", "")), md=4),
            dbc.Col(cred_input("API Password", "eitje-password", "API password",
                               stored_value=creds.get("password", ""), is_password=True), md=8),
        ]),
        dbc.Row([
            dbc.Col(cred_input("Partner Username", "eitje-partner-user",
                               "From Eitje partner account",
                               stored_value=creds.get("partner_user", "")), md=4),
            dbc.Col(cred_input("Partner Password", "eitje-partner-pass",
                               "From Eitje partner account",
                               stored_value=creds.get("partner_pass", ""), is_password=True), md=8),
        ]),
        dbc.Row([
            dbc.Col([
                html.Button("Save Credentials", id="eitje-save-btn", style=btn_style),
                html.Button("Test Connection", id="eitje-test-btn", style=btn_style),
            ], width="auto"),
            dbc.Col([
                html.Div(id="eitje-test-status",
                         style={"fontSize": "12px", "paddingTop": "8px"}),
            ]),
        ], align="center"),
    ], style={**CARD_STYLE, "marginBottom": "20px"})

    return html.Div([
        refresh_section,
        eitje_section,
        dbc.Row([
            dbc.Col(source_section, md=6),
            dbc.Col(data_section,   md=6),
        ], className="g-3 mb-3"),
        threshold_section,
        dcc.Location(id="refresh-redirect", refresh=True),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# APP LAYOUT
# ─────────────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    ],
    title="LERA",
    suppress_callback_exceptions=True,
)
_dash_user = os.environ.get("DASH_USERNAME", "lera")
_dash_pass = os.environ.get("DASH_PASSWORD", "")
if _dash_pass:
    dash_auth.BasicAuth(app, {_dash_user: _dash_pass})

TAB_STYLE = {
    "padding": "10px 24px",
    "fontFamily": "Inter, sans-serif",
    "fontSize": "13px",
    "fontWeight": "500",
    "color": TAAL_MUTED,
    "border": "none",
    "borderBottom": "2px solid transparent",
    "background": "transparent",
    "cursor": "pointer",
}
TAB_SELECTED_STYLE = {
    **TAB_STYLE,
    "color": TAAL_DARK,
    "fontWeight": "700",
    "borderBottom": f"2px solid {TAAL_GREEN}",
}

app.layout = html.Div([
    # ── Persistent stores ─────────────────────────────────────────────────────
    dcc.Store(id="thresholds-store", storage_type="local",
              data={"low": 8, "high": 18}),
    dcc.Store(id="matcha-inputs-store", storage_type="local",
              data=_load_matcha_inputs()),
    dcc.Store(id="eitje-creds-store", storage_type="local", data={}),

    # ── Scheduling stores (session = survives tab navigation, reset on hard reload) ──
    dcc.Store(id="sched-shifts-store",  storage_type="session", data={}),
    dcc.Store(id="sched-revenue-store", storage_type="session", data={}),
    dcc.Store(id="sched-week-store",    storage_type="session", data=None),

    # ── Header ───────────────────────────────────────────────────────────────
    html.Div([
        dbc.Container([
            dbc.Row([
                dbc.Col([
                    html.Div("LERA", style={"fontSize": "22px", "fontWeight": "800",
                                           "color": TAAL_DARK, "letterSpacing": "0.12em"}),
                    html.Div("Performance Dashboard", style={"fontSize": "11px",
                                                              "color": TAAL_MUTED,
                                                              "letterSpacing": "0.05em"}),
                ], width="auto"),
                dbc.Col([
                    html.Div(
                        f"Data through Mar 2026  ·  Updated {pd.Timestamp.now().strftime('%d %b %Y')}",
                        style={"fontSize": "11px", "color": TAAL_MUTED, "textAlign": "right",
                               "paddingTop": "6px"}
                    )
                ], style={"textAlign": "right"}),
            ], align="center"),
        ], fluid=True),
    ], style={"background": "white", "padding": "16px 0 0 0",
              "borderBottom": "1px solid #E8E0D5",
              "boxShadow": "0 1px 4px rgba(0,0,0,0.04)"}),

    # ── Tabs ─────────────────────────────────────────────────────────────────
    html.Div([
        dbc.Container([
            dcc.Tabs(
                id="main-tabs",
                value="pl_table",
                children=[
                    dcc.Tab(label="Monthly P&L",     value="pl_table",  style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="P&L Charts",      value="pl",        style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="Sales",           value="sales",     style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="Product Sales",   value="products",  style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="E-com",           value="ecom",      style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="Labor & Costs",   value="labor",     style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="Scheduling",      value="scheduling",style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                    dcc.Tab(label="⚙ Settings",      value="settings",  style=TAB_STYLE, selected_style=TAB_SELECTED_STYLE),
                ],
                style={"border": "none", "background": "transparent"},
            ),
        ], fluid=True),
    ], style={"background": "white", "borderBottom": "1px solid #E8E0D5"}),

    # ── Tab content ───────────────────────────────────────────────────────────
    html.Div(id="tab-content", style={"background": TAAL_SAND, "minHeight": "100vh"}),

], style={"fontFamily": "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
           "background": TAAL_SAND})


@app.callback(
    Output("sched-revenue-store", "data", allow_duplicate=True),
    Input("main-tabs", "value"),
    State("sched-revenue-store", "data"),
    prevent_initial_call="initial_duplicate",
)
def seed_revenue_store(_, existing):
    """Seed revenue store from CSV on first load (if empty)."""
    if existing:
        return dash.no_update
    return _load_daily_revenue()


@app.callback(Output("tab-content", "children"),
              Input("main-tabs", "value"),
              State("eitje-creds-store", "data"))
def render_tab(tab, stored_creds):
    wrapper = {"padding": "24px 0"}
    if tab == "pl_table":
        return html.Div([dbc.Container(make_pl_table_tab(), fluid=True)], style=wrapper)
    if tab == "pl":
        return html.Div([dbc.Container(make_pl_tab(), fluid=True)], style=wrapper)
    if tab == "sales":
        return html.Div([dbc.Container(make_sales_tab(), fluid=True)], style=wrapper)
    if tab == "products":
        return html.Div([dbc.Container(make_product_tab(), fluid=True)], style=wrapper)
    if tab == "ecom":
        return html.Div([dbc.Container(make_ecom_tab(), fluid=True)], style=wrapper)
    if tab == "labor":
        return html.Div([dbc.Container(make_labor_tab(), fluid=True)], style=wrapper)
    if tab == "scheduling":
        return html.Div([dbc.Container(make_scheduling_tab(), fluid=True)], style=wrapper)
    if tab == "settings":
        return html.Div([dbc.Container(make_settings_tab(stored_creds), fluid=True)], style=wrapper)
    return html.Div("Tab not found")


# ─────────────────────────────────────────────────────────────────────────────
# P&L EXCEL EXPORT (Flask route — works reliably behind gunicorn)
# ─────────────────────────────────────────────────────────────────────────────
import io as _io
from flask import make_response as _make_response
from datetime import datetime as _dt_export

@app.server.route("/download/pl-export")
def download_pl_excel():
    try:
        show_months = list(OPERATING_MONTHS)
        rows = []
        for label, is_total, is_sub, is_header in PL_TABLE_LINES:
            if is_header:
                rows.append({"Line Item": label, **{m: "" for m in show_months}})
                continue
            vals = _pl_val(label, show_months)
            row = {"Line Item": label}
            for m in show_months:
                v = float(vals[m]) if m in vals.index else 0.0
                row[m] = v if v != 0 else ""
            rows.append(row)

        df = pd.DataFrame(rows)
        buf = _io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Monthly P&L")
            ws = writer.sheets["Monthly P&L"]
            ws.column_dimensions["A"].width = 32
            for i, _ in enumerate(show_months):
                col_letter = chr(ord("B") + i) if i < 25 else "A" + chr(ord("A") + i - 25)
                ws.column_dimensions[col_letter].width = 14

        fname = f"Lera_PL_{_dt_export.now().strftime('%Y%m%d')}.xlsx"
        resp = _make_response(buf.getvalue())
        resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
        return resp
    except Exception as e:
        return _make_response(f"Export error: {e}", 500)


# ─────────────────────────────────────────────────────────────────────────────
# P&L TABLE CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("pl-expanded", "data"),
    Input({"type": "pl-toggle", "index": ALL}, "n_clicks"),
    State("pl-expanded", "data"),
    prevent_initial_call=True,
)
def toggle_pl_group(n_clicks_list, expanded):
    if not ctx.triggered_id:
        return expanded
    group = ctx.triggered_id["index"]
    expanded = list(expanded or [])
    if group in expanded:
        expanded.remove(group)
    else:
        expanded.append(group)
    return expanded


@app.callback(
    Output("pl-table-container", "children"),
    Input("pl-expanded", "data"),
    Input("thresholds-store", "data"),
    Input("matcha-inputs-store", "data"),
)
def render_pl_table(expanded, thresholds, matcha_data):
    expanded = set(expanded or [])
    show_months = list(OPERATING_MONTHS)
    months_2025 = [m for m in show_months if m.startswith("2025")]
    months_2026 = [m for m in show_months if m.startswith("2026")]
    est_m     = set(ESTIMATED_MONTHS)
    transit_m = set(TRANSIT_MONTHS)

    thresh_low  = float((thresholds or {}).get("low",  8))
    thresh_high = float((thresholds or {}).get("high", 18))

    # Revenue totals for per-cell % of revenue calculation
    rev_series = _pl_val("TOTAL REVENUE", show_months)
    rev_by_month = {m: float(rev_series[m]) for m in show_months}

    # Matcha calculated cost per month (from manual inputs)
    def _matcha_calc_series(months):
        qty_d  = (matcha_data or {}).get("qty", {})
        cost_d = (matcha_data or {}).get("cost_per_kg", {})
        vals = []
        for m in months:
            try:
                vals.append(round(float(qty_d[m]) * float(cost_d[m]) / MATCHA_DRINKS_PER_KG, 2))
            except Exception:
                vals.append(0.0)
        return pd.Series(vals, index=months)

    matcha_calc = _matcha_calc_series(show_months)

    # Adjusted _pl_val: matcha_calc added to COGS, deducted from MATCHA COMPANY
    # EBT is unaffected (EBIT↓ and TOTAL_MC↓ by same amount → cancel out)
    _PROFIT_LINES = {"EBIT", "EBITDA"}
    _MARGIN_LINES = {"GROSS MARGIN %", "EBIT MARGIN %", "EBITDA MARGIN %"}

    def _adj_pl_val(label, months):
        base = _pl_val(label, months)
        adj  = matcha_calc.reindex(months).fillna(0)
        if label == "TOTAL COGS":
            return base + adj
        if label == "TOTAL MATCHA COMPANY":
            return base - adj
        if label == "GROSS PROFIT" or label in _PROFIT_LINES:
            return base - adj
        if label in _MARGIN_LINES:
            base_label = label.replace(" MARGIN %", "")
            if base_label == "GROSS":
                base_label = "GROSS PROFIT"
            adj_vals  = _pl_val(base_label, months) - adj
            rev_vals  = _pl_val("TOTAL REVENUE", months)
            result = []
            for m in months:
                r = float(rev_vals[m]) if m in rev_vals.index else 0.0
                v = float(adj_vals[m]) if m in adj_vals.index else 0.0
                result.append(round(v / r * 100, 1) if r else float("nan"))
            return pd.Series(result, index=months)
        return base

    # Total costs per year for % cost weight columns (includes matcha calc)
    def _year_total_costs(months):
        if not months:
            return 0.0
        mc_sum = float(matcha_calc.reindex(months).fillna(0).sum())
        return float(_pl_val("TOTAL COGS", months).sum() + mc_sum +
                     _pl_val("TOTAL LABOR", months).sum() +
                     _pl_val("TOTAL OCCUPANCY", months).sum() +
                     _pl_val("TOTAL SG&A", months).sum())

    total_costs_2025 = _year_total_costs(months_2025)
    total_costs_2026 = _year_total_costs(months_2026)

    # ── styles ────────────────────────────────────────────────────────────────
    BASE_CELL = {
        "fontFamily": "Inter, sans-serif", "fontSize": "12px",
        "padding": "7px 14px", "borderBottom": "1px solid #F0EBE3",
        "textAlign": "right", "color": TAAL_DARK, "whiteSpace": "nowrap",
    }
    LABEL_CELL = {**BASE_CELL, "textAlign": "left", "minWidth": "230px",
                  "fontWeight": "500"}

    def cell_bg(m, row_bg, is_transit_rev=False):
        if is_transit_rev and m in transit_m:
            return "#FFF3CD"  # amber — revenue from unclosed period clearing accounts
        if row_bg not in ("white", None):
            return "#FDFAF3" if m in est_m else row_bg
        return "#FDFAF3" if m in est_m else "white"

    def fmt_val(v, is_pct, is_count=False):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        if is_pct:
            return f"{v:.1f}%" if v != 0 else "—"
        if is_count:
            return f"{int(v):,}" if v != 0 else "—"
        return f"€{v:,.0f}" if v != 0 else "—"

    def pct_rev_color(pct):
        """Traffic-light color for % of revenue indicator (uses dynamic thresholds)."""
        if pct <= thresh_low:
            return TAAL_GREEN
        elif pct <= thresh_high:
            return "#B8860B"   # dark amber
        else:
            return TAAL_RED

    def clean_vendor(name):
        """Truncate long vendor description strings."""
        name = str(name).split(";")[0].strip()
        return name[:48] + "…" if len(name) > 48 else name

    # ── header row ────────────────────────────────────────────────────────────
    HDR_2025 = "#3A3020"   # slightly lighter dark for 2025 group
    HDR_2026 = "#1E2D3A"   # blue-tinted dark for 2026 group
    HDR_PCT  = "#2C2416"   # same as TAAL_DARK for % cost columns

    hdr_cells = [html.Th("Line Item", style={
        **LABEL_CELL, "backgroundColor": TAAL_DARK, "color": "white",
        "fontWeight": "600", "fontSize": "11px", "borderBottom": "none",
    })]
    # 2025 month columns
    for i, m in enumerate(months_2025):
        bg = "#5A4A30" if m in est_m else HDR_2025
        col_lbl = MONTH_LABELS[m] + (" *" if m in est_m else "")
        border_left = "3px solid #6A5A40" if i == 0 else "none"
        hdr_cells.append(html.Th(col_lbl, style={
            **BASE_CELL, "backgroundColor": bg,
            "color": TAAL_ACCENT if m in est_m else "white",
            "fontWeight": "600", "fontSize": "11px", "borderBottom": "none",
            "borderLeft": border_left,
        }))
    # % Cost 2025 column
    hdr_cells.append(html.Th("% Cost '25", style={
        **BASE_CELL, "backgroundColor": HDR_PCT, "color": TAAL_ACCENT,
        "fontWeight": "600", "fontSize": "11px", "borderBottom": "none",
        "minWidth": "74px", "borderLeft": "1px solid #6A5A40",
    }))
    # 2026 month columns
    for i, m in enumerate(months_2026):
        bg = "#5A4A30" if m in est_m else HDR_2026
        col_lbl = MONTH_LABELS[m] + (" *" if m in est_m else "")
        border_left = "3px solid #3A5A7C" if i == 0 else "none"
        hdr_cells.append(html.Th(col_lbl, style={
            **BASE_CELL, "backgroundColor": bg,
            "color": TAAL_ACCENT if m in est_m else "white",
            "fontWeight": "600", "fontSize": "11px", "borderBottom": "none",
            "borderLeft": border_left,
        }))
    # % Cost 2026 column (only if there are 2026 months)
    if months_2026:
        hdr_cells.append(html.Th("% Cost '26", style={
            **BASE_CELL, "backgroundColor": HDR_PCT, "color": "#A8D0F0",
            "fontWeight": "600", "fontSize": "11px", "borderBottom": "none",
            "minWidth": "74px", "borderLeft": "1px solid #3A5A7C",
        }))

    tbody_rows = []

    def make_row(label, is_total, is_sub, is_header, detail=False,
                 vendor=False, vendor_monthly=None):
        is_pct = "%" in label

        if vendor and vendor_monthly is not None:
            vals = None  # use vendor_monthly dict directly
        elif is_header:
            vals = pd.Series(dtype=float)
        else:
            vals = _adj_pl_val(label, show_months)

        # Row background
        if vendor:
            row_bg = "#F7F5F2"
        elif is_header:
            row_bg = "#F0EBE3"
        elif label in ("TOTAL REVENUE", "E-com Revenue",
                       "E-com Net Sales", "E-com Shipping", "E-com Orders",
                       "Wholesale Revenue", "Gross Revenue"):
            row_bg = "#EEF5EC"
        elif label == "Payment Fees":
            row_bg = "#FEF5EC"
        elif label == "TOTAL COGS":
            row_bg = "#FEF5EC"
        elif label in ("GROSS PROFIT", "EBIT", "EBITDA", "EBT"):
            row_bg = "#FAF6F0"
        elif label == "TOTAL LABOR":
            row_bg = "#F0EAF5"
        elif is_total:
            row_bg = "#FAF6F0"
        elif detail:
            row_bg = "#FAFAF8"
        else:
            row_bg = "white"

        # Label cell content
        has_detail = label in PL_DETAIL_LINES
        is_open = label in expanded
        vendor_key = f"vendor::{label}"
        has_vendor = label in GL_VENDOR_DATA
        is_vendor_open = vendor_key in expanded

        if is_header:
            label_content = html.Span(label, style={
                "fontSize": "10px", "fontWeight": "700",
                "textTransform": "uppercase", "letterSpacing": "0.06em",
                "color": TAAL_MUTED,
            })
        elif vendor:
            label_content = html.Span("        · " + clean_vendor(label), style={
                "color": "#A09080", "fontSize": "10px", "paddingLeft": "28px",
            })
        elif has_detail:
            icon = "▼" if is_open else "▶"
            label_content = html.Span([
                html.Button(icon, id={"type": "pl-toggle", "index": label},
                    n_clicks=0,
                    style={
                        "background": "none", "border": "none", "cursor": "pointer",
                        "fontSize": "9px", "color": TAAL_MUTED, "padding": "0 6px 0 0",
                        "verticalAlign": "middle",
                    }),
                ("▸ " if is_sub else "") + label,
            ])
        elif detail and has_vendor:
            v_icon = "▼" if is_vendor_open else "▶"
            label_content = html.Span([
                html.Button(v_icon, id={"type": "pl-toggle", "index": vendor_key},
                    n_clicks=0,
                    style={
                        "background": "none", "border": "none", "cursor": "pointer",
                        "fontSize": "9px", "color": TAAL_MUTED, "padding": "0 6px 0 0",
                        "verticalAlign": "middle",
                    }),
                html.Span("  · " + label, style={
                    "color": TAAL_MUTED, "fontSize": "11px",
                }),
            ], style={"paddingLeft": "12px", "display": "inline-flex", "alignItems": "center"})
        elif detail:
            label_content = html.Span("    · " + label, style={
                "color": TAAL_MUTED, "fontSize": "11px", "paddingLeft": "20px"
            })
        else:
            label_content = ("▸ " if is_sub else "") + label

        label_fw = "700" if (is_total or is_header) else ("500" if not (detail or vendor) else "400")
        label_color = (TAAL_GREEN if label in ("TOTAL REVENUE", "E-com Revenue",
                                                "E-com Net Sales", "E-com Shipping", "E-com Orders",
                                                "Wholesale Revenue", "Gross Revenue")
                       else ("#5A3A7C" if label == "TOTAL LABOR"
                             else (TAAL_MUTED if vendor else TAAL_DARK)))

        cells = [html.Td(label_content, style={
            **LABEL_CELL,
            "backgroundColor": row_bg,
            "fontWeight": label_fw,
            "color": label_color,
            "borderTop": "2px solid #E0D5C5" if is_header else "none",
        })]

        _NO_CELL_PCT = {
            "TOTAL REVENUE", "GROSS PROFIT", "GROSS MARGIN %",
            "EBIT", "EBIT MARGIN %", "EBITDA", "EBITDA MARGIN %",
            "EBT", "EBT MARGIN %",
            "E-com Net Sales", "E-com Shipping", "E-com Orders",
            "E-com Revenue", "Wholesale Revenue",
            "Gross Revenue", "Payment Fees",
        }
        # Whether to show per-cell % of revenue weight
        show_cell_pct = not is_header and not is_pct and label not in _NO_CELL_PCT

        def _get_val(m):
            if vendor and vendor_monthly is not None:
                return float(vendor_monthly.get(m, 0))
            elif isinstance(vals, pd.Series) and m in vals.index and not is_header:
                return float(vals[m])
            return 0.0

        is_transit_rev_row = (label == "TOTAL REVENUE")
        is_count = (label == "E-com Orders")

        def _build_month_cells(months_group, border_first_left):
            row_group_total = 0.0
            group_cells = []
            val_color = (TAAL_RED if label == "Payment Fees"
                         else TAAL_GREEN if label in ("GROSS PROFIT", "EBITDA", "EBT", "E-com Revenue",
                                                      "E-com Net Sales", "Wholesale Revenue", "Gross Revenue")
                         else ("#5A3A7C" if label == "TOTAL LABOR"
                               else (TAAL_MUTED if vendor else TAAL_DARK)))
            for i, m in enumerate(months_group):
                v = _get_val(m)
                row_group_total += v
                bg = cell_bg(m, row_bg, is_transit_rev=is_transit_rev_row)
                border_left = border_first_left if i == 0 else "none"
                is_unclosed = is_transit_rev_row and m in transit_m

                if show_cell_pct and rev_by_month.get(m, 0):
                    m_pct = v / rev_by_month[m] * 100
                    pct_str = f"{m_pct:.1f}%" if abs(m_pct) >= 0.05 else ""
                    pct_c = pct_rev_color(abs(m_pct)) if pct_str else TAAL_MUTED
                    cell_content = html.Div([
                        html.Div(fmt_val(v, is_pct, is_count),
                                 style={"fontSize": "10px" if vendor else "12px",
                                        "fontWeight": "700" if is_total else "400",
                                        "color": val_color}),
                        html.Div(pct_str, style={
                            "fontSize": "9px", "color": pct_c,
                            "lineHeight": "1.2", "marginTop": "1px",
                        }),
                    ], style={"textAlign": "right"})
                elif is_unclosed:
                    cell_content = html.Span([
                        fmt_val(v, is_pct, is_count),
                        html.Sup("*", style={"color": "#B8860B", "fontSize": "9px",
                                             "marginLeft": "2px", "fontWeight": "700"}),
                    ])
                else:
                    cell_content = fmt_val(v, is_pct, is_count)

                group_cells.append(html.Td(cell_content, style={
                    **BASE_CELL,
                    "backgroundColor": bg,
                    "fontWeight": "700" if is_total else "400",
                    "color": val_color,
                    "fontSize": "10px" if vendor else "12px",
                    "padding": "5px 14px" if show_cell_pct else "7px 14px",
                    "borderLeft": border_left,
                }))
            return group_cells, row_group_total

        _NO_PCT_LINES = {
            "TOTAL REVENUE", "GROSS PROFIT", "GROSS MARGIN %",
            "EBIT", "EBIT MARGIN %", "EBITDA", "EBITDA MARGIN %",
            "EBT", "EBT MARGIN %",
            "E-com Net Sales", "E-com Shipping", "E-com Orders",
            "E-com Revenue", "Wholesale Revenue",
            "Gross Revenue", "Payment Fees",
        }

        def _pct_cost_cell(row_group_total, total_costs_year, pct_accent_color, border_left):
            if is_header or is_pct or label in _NO_PCT_LINES:
                pct_content = ""
                pct_col = TAAL_MUTED
            elif total_costs_year:
                pct = row_group_total / total_costs_year * 100
                pct_content = f"{pct:.1f}%" if abs(pct) >= 0.05 else "—"
                pct_col = pct_rev_color(abs(pct)) if abs(pct) >= 0.05 else TAAL_MUTED
            else:
                pct_content = "—"
                pct_col = TAAL_MUTED
            return html.Td(pct_content, style={
                **BASE_CELL,
                "backgroundColor": row_bg if row_bg != "white" else "#FAF8F5",
                "fontWeight": "600" if not (is_header or is_pct or vendor) else "400",
                "color": pct_col,
                "fontSize": "11px",
                "borderLeft": border_left,
            })

        # ── 2025 months ──────────────────────────────────────────────────────
        cells_2025, total_2025 = _build_month_cells(months_2025, "3px solid #6A5A40")
        cells.extend(cells_2025)
        cells.append(_pct_cost_cell(total_2025, total_costs_2025, TAAL_ACCENT,
                                    "1px solid #D0C8B8"))

        # ── 2026 months ──────────────────────────────────────────────────────
        if months_2026:
            cells_2026, total_2026 = _build_month_cells(months_2026, "3px solid #3A5A7C")
            cells.extend(cells_2026)
            cells.append(_pct_cost_cell(total_2026, total_costs_2026, "#A8D0F0",
                                        "1px solid #C0D0E0"))

        return html.Tr(cells)

    # ── Matcha input rows ─────────────────────────────────────────────────────
    _matcha_input_style = {
        "width": "100%", "border": "1px solid #C8DCC8",
        "borderRadius": "3px", "padding": "2px 6px",
        "fontSize": "11px", "color": TAAL_DARK,
        "backgroundColor": "#F5FBF5", "textAlign": "right",
        "outline": "none",
    }

    def make_matcha_input_row(row_label, row_key):
        store_vals = (matcha_data or {}).get(row_key, {})
        row_bg = "#F0F7F0"
        step = 1 if row_key == "qty" else 0.01
        placeholder = "qty" if row_key == "qty" else "€/kg"
        cells = [html.Td(
            html.Span("▸ " + row_label, style={"color": "#4A7A4A", "fontSize": "11px"}),
            style={**LABEL_CELL, "backgroundColor": row_bg, "fontWeight": "500"},
        )]
        for i, m in enumerate(months_2025):
            border_left = "3px solid #6A5A40" if i == 0 else "none"
            val = store_vals.get(m)
            cells.append(html.Td(
                dcc.Input(
                    id={"type": f"matcha-{row_key}", "index": m},
                    type="number", min=0, step=step,
                    value=val, debounce=True, placeholder=placeholder,
                    style=_matcha_input_style,
                ),
                style={**BASE_CELL, "backgroundColor": row_bg,
                       "padding": "4px 6px", "borderLeft": border_left},
            ))
        cells.append(html.Td("", style={**BASE_CELL, "backgroundColor": row_bg,
                                        "borderLeft": "1px solid #D0C8B8"}))
        for i, m in enumerate(months_2026):
            border_left = "3px solid #3A5A7C" if i == 0 else "none"
            val = store_vals.get(m)
            cells.append(html.Td(
                dcc.Input(
                    id={"type": f"matcha-{row_key}", "index": m},
                    type="number", min=0, step=step,
                    value=val, debounce=True, placeholder=placeholder,
                    style=_matcha_input_style,
                ),
                style={**BASE_CELL, "backgroundColor": row_bg,
                       "padding": "4px 6px", "borderLeft": border_left},
            ))
        if months_2026:
            cells.append(html.Td("", style={**BASE_CELL, "backgroundColor": row_bg,
                                            "borderLeft": "1px solid #C0D0E0"}))
        return html.Tr(cells, style={"height": "30px"})

    def make_matcha_calc_row():
        qty_d  = (matcha_data or {}).get("qty", {})
        cost_d = (matcha_data or {}).get("cost_per_kg", {})
        row_bg = "#E8F4E8"

        def _calc(m):
            try:
                q = float(qty_d[m]); c = float(cost_d[m])
                return round(q * c / MATCHA_DRINKS_PER_KG, 2)
            except Exception:
                return None

        cells = [html.Td(
            html.Span("▸ Matcha Calculated Cost",
                      style={"color": "#2D5A2D", "fontSize": "11px", "fontWeight": "600"}),
            style={**LABEL_CELL, "backgroundColor": row_bg},
        )]
        tot25 = 0.0
        for i, m in enumerate(months_2025):
            border_left = "3px solid #6A5A40" if i == 0 else "none"
            v = _calc(m); tot25 += v or 0.0
            cells.append(html.Td(
                fmt_eur(v) if v is not None else "—",
                style={**BASE_CELL, "backgroundColor": cell_bg(m, row_bg),
                       "fontWeight": "600", "color": "#2D5A2D", "borderLeft": border_left},
            ))
        pct25 = (f"{tot25/total_costs_2025*100:.1f}%" if total_costs_2025 else "—")
        cells.append(html.Td(pct25, style={**BASE_CELL, "backgroundColor": row_bg,
                                           "color": TAAL_MUTED, "fontSize": "10px",
                                           "borderLeft": "1px solid #D0C8B8"}))
        if months_2026:
            tot26 = 0.0
            for i, m in enumerate(months_2026):
                border_left = "3px solid #3A5A7C" if i == 0 else "none"
                v = _calc(m); tot26 += v or 0.0
                cells.append(html.Td(
                    fmt_eur(v) if v is not None else "—",
                    style={**BASE_CELL, "backgroundColor": cell_bg(m, row_bg),
                           "fontWeight": "600", "color": "#2D5A2D", "borderLeft": border_left},
                ))
            pct26 = (f"{tot26/total_costs_2026*100:.1f}%" if total_costs_2026 else "—")
            cells.append(html.Td(pct26, style={**BASE_CELL, "backgroundColor": row_bg,
                                               "color": TAAL_MUTED, "fontSize": "10px",
                                               "borderLeft": "1px solid #C0D0E0"}))
        return html.Tr(cells)

    def make_matcha_deduction_row():
        """Same values as Matcha Calculated Cost but negated — deduction inside MATCHA COMPANY."""
        qty_d  = (matcha_data or {}).get("qty", {})
        cost_d = (matcha_data or {}).get("cost_per_kg", {})
        row_bg = "#FFF0F0"

        def _calc(m):
            try:
                q = float(qty_d[m]); c = float(cost_d[m])
                return round(q * c / MATCHA_DRINKS_PER_KG, 2)
            except Exception:
                return None

        cells = [html.Td(
            html.Span("▸ Matcha Calc. Cost (–)",
                      style={"color": "#8B2020", "fontSize": "11px", "fontWeight": "600"}),
            style={**LABEL_CELL, "backgroundColor": row_bg},
        )]
        tot25 = 0.0
        for i, m in enumerate(months_2025):
            border_left = "3px solid #6A5A40" if i == 0 else "none"
            v = _calc(m)
            display_v = -v if v is not None else None
            tot25 += v or 0.0
            cells.append(html.Td(
                fmt_eur(display_v) if display_v is not None else "—",
                style={**BASE_CELL, "backgroundColor": cell_bg(m, row_bg),
                       "fontWeight": "600", "color": "#8B2020", "borderLeft": border_left},
            ))
        pct25 = (f"{-tot25/total_costs_2025*100:.1f}%" if total_costs_2025 and tot25 else "—")
        cells.append(html.Td(pct25, style={**BASE_CELL, "backgroundColor": row_bg,
                                           "color": TAAL_MUTED, "fontSize": "10px",
                                           "borderLeft": "1px solid #D0C8B8"}))
        if months_2026:
            tot26 = 0.0
            for i, m in enumerate(months_2026):
                border_left = "3px solid #3A5A7C" if i == 0 else "none"
                v = _calc(m)
                display_v = -v if v is not None else None
                tot26 += v or 0.0
                cells.append(html.Td(
                    fmt_eur(display_v) if display_v is not None else "—",
                    style={**BASE_CELL, "backgroundColor": cell_bg(m, row_bg),
                           "fontWeight": "600", "color": "#8B2020", "borderLeft": border_left},
                ))
            pct26 = (f"{-tot26/total_costs_2026*100:.1f}%" if total_costs_2026 and tot26 else "—")
            cells.append(html.Td(pct26, style={**BASE_CELL, "backgroundColor": row_bg,
                                               "color": TAAL_MUTED, "fontSize": "10px",
                                               "borderLeft": "1px solid #C0D0E0"}))
        return html.Tr(cells)

    def make_vendor_rows(detail_label):
        """Return vendor sub-rows for a GL detail line, collapsed by normalize_vendor."""
        vd = GL_VENDOR_DATA[detail_label]
        # Aggregate by normalized key
        agg: dict = {}
        for m in show_months:
            for raw_name, amount in vd.get(m, {}).items():
                key = normalize_vendor(raw_name)
                if key not in agg:
                    agg[key] = {mm: 0.0 for mm in show_months}
                agg[key][m] = agg[key].get(m, 0.0) + amount
        sorted_vendors = sorted(agg.items(), key=lambda x: -sum(x[1].values()))
        rows = []
        for v_name, v_monthly in sorted_vendors:
            total = sum(v_monthly.values())
            if abs(total) < 1.0:   # hide near-zero (covers Baux Pastry double-entry)
                continue
            rows.append(make_row(v_name, False, False, False,
                                 vendor=True, vendor_monthly=v_monthly))
        return rows

    _MATCHA_INPUT_ROWS = {
        "Matcha Drinks Qty":   "qty",
        "Matcha Cost/kg (€)":  "cost_per_kg",
    }

    for label, is_total, is_sub, is_header in PL_TABLE_LINES:
        if is_header:
            tbody_rows.append(make_row(label, False, False, True))
            continue
        if label in _MATCHA_INPUT_ROWS:
            tbody_rows.append(make_matcha_input_row(label, _MATCHA_INPUT_ROWS[label]))
            continue
        if label == "Matcha Calculated Cost":
            tbody_rows.append(make_matcha_calc_row())
            continue
        if label == "Matcha Calc. Cost (\u2013)":
            tbody_rows.append(make_matcha_deduction_row())
            continue
        tbody_rows.append(make_row(label, is_total, is_sub, False))
        # inject GL detail rows if this group is expanded
        if label in PL_DETAIL_LINES and label in expanded:
            for detail_label in PL_DETAIL_LINES[label]:
                tbody_rows.append(make_row(detail_label, False, False, False, detail=True))
                # inject vendor rows if this GL detail is expanded
                if f"vendor::{detail_label}" in expanded and detail_label in GL_VENDOR_DATA:
                    tbody_rows.extend(make_vendor_rows(detail_label))

    return html.Table(
        [html.Thead(html.Tr(hdr_cells)),
         html.Tbody(tbody_rows)],
        style={"width": "100%", "borderCollapse": "collapse",
               "fontFamily": "Inter, sans-serif"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# MATCHA INPUTS – save to store + disk
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("matcha-inputs-store", "data"),
    Input({"type": "matcha-qty",         "index": ALL}, "value"),
    Input({"type": "matcha-cost_per_kg", "index": ALL}, "value"),
    State({"type": "matcha-qty",         "index": ALL}, "id"),
    State({"type": "matcha-cost_per_kg", "index": ALL}, "id"),
    State("matcha-inputs-store", "data"),
    prevent_initial_call=True,
)
def save_matcha_inputs(qty_vals, cost_vals, qty_ids, cost_ids, current):
    data = {
        "qty":         dict((current or {}).get("qty",         {})),
        "cost_per_kg": dict((current or {}).get("cost_per_kg", {})),
    }
    for v, id_obj in zip(qty_vals,  qty_ids):
        if v is not None:
            data["qty"][id_obj["index"]] = v
    for v, id_obj in zip(cost_vals, cost_ids):
        if v is not None:
            data["cost_per_kg"][id_obj["index"]] = v
    _save_matcha_inputs(data)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# P&L TABLE – KPI SUMMARY ROW (year-filtered)
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("pl-kpi-row", "children"),
    Input("pl-kpi-year", "value"),
)
def render_pl_kpis(year_filter):
    om = list(OPERATING_MONTHS)
    if year_filter and year_filter != "all":
        om = [m for m in om if m.startswith(year_filter)]

    rev_total  = pl_series("TOTAL REVENUE", om).sum()
    cogs_total = pl_series("TOTAL COGS",    om).sum()
    gp_total   = pl_series("GROSS PROFIT",  om).sum()
    ebitda_sum = pl_series("EBITDA",        om).sum()
    gm_series  = pd.to_numeric(get_row("GROSS MARGIN %")[om], errors="coerce")
    best_gm    = gm_series.max()

    if year_filter == "all":
        rev_sub = ("GL + POS est. " + ", ".join(MONTH_LABELS[m] for m in ESTIMATED_MONTHS)
                   if ESTIMATED_MONTHS else "GL — all periods")
        period_label = "All periods"
    else:
        rev_sub = f"GL — {year_filter}"
        period_label = year_filter

    return dbc.Row([
        dbc.Col(kpi_card("Total Revenue",     fmt_eur(rev_total),  rev_sub),          width="auto"),
        dbc.Col(kpi_card("Total COGS",        fmt_eur(cogs_total), period_label),     width="auto"),
        dbc.Col(kpi_card("Gross Profit",      fmt_eur(gp_total),   period_label,
                         color=TAAL_GREEN if gp_total >= 0 else TAAL_RED),            width="auto"),
        dbc.Col(kpi_card("Best Gross Margin", fmt_pct(best_gm),    "Single month peak",
                         color=TAAL_GREEN),                                           width="auto"),
        dbc.Col(kpi_card("Total EBITDA",      fmt_eur(ebitda_sum), period_label,
                         color=TAAL_GREEN if ebitda_sum >= 0 else TAAL_RED),          width="auto"),
    ], className="g-3")


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────

# ── Threshold store: sliders ↔ inputs stay in sync, store always current ──────
@app.callback(
    Output("thresholds-store",   "data"),
    Output("thresh-low-slider",  "value"),
    Output("thresh-high-slider", "value"),
    Output("thresh-low-input",   "value"),
    Output("thresh-high-input",  "value"),
    Input("thresh-low-slider",   "value"),
    Input("thresh-high-slider",  "value"),
    Input("thresh-low-input",    "value"),
    Input("thresh-high-input",   "value"),
    State("thresholds-store",    "data"),
    prevent_initial_call=True,
)
def sync_thresholds(sl_low, sl_high, in_low, in_high, store):
    tid = ctx.triggered_id
    store = dict(store or {"low": 8, "high": 18})

    if tid == "thresh-low-slider":
        low  = float(sl_low  or store["low"])
        high = float(store["high"])
    elif tid == "thresh-high-slider":
        low  = float(store["low"])
        high = float(sl_high or store["high"])
    elif tid == "thresh-low-input":
        low  = float(in_low  or store["low"])
        high = float(store["high"])
    elif tid == "thresh-high-input":
        low  = float(store["low"])
        high = float(in_high or store["high"])
    else:
        low, high = store["low"], store["high"]

    # keep low < high
    if low >= high:
        high = low + 1

    store = {"low": low, "high": high}
    return store, low, high, low, high


# ── Threshold legend + gradient bar ──────────────────────────────────────────
@app.callback(
    Output("threshold-legend", "children"),
    Output("threshold-bar",    "children"),
    Input("thresholds-store",  "data"),
)
def update_threshold_visuals(thresholds):
    low  = float((thresholds or {}).get("low",  8))
    high = float((thresholds or {}).get("high", 18))

    def pill(color, text):
        return html.Span([
            html.Span(style={
                "display": "inline-block", "width": "11px", "height": "11px",
                "borderRadius": "50%", "backgroundColor": color,
                "marginRight": "5px", "verticalAlign": "middle",
            }),
            html.Span(text, style={"fontSize": "12px", "verticalAlign": "middle",
                                   "color": TAAL_DARK}),
        ], style={"marginRight": "20px"})

    legend = html.Div([
        pill(TAAL_GREEN, f"Green  ≤ {low:.1f}%"),
        pill("#B8860B",  f"Amber  {low:.1f}% – {high:.1f}%"),
        pill(TAAL_RED,   f"Red  > {high:.1f}%"),
    ])

    # Proportional gradient bar (max scale = high * 1.5 or 40, whichever bigger)
    scale = max(high * 1.5, 40)
    green_w = min(low  / scale * 100, 100)
    amber_w = min((high - low) / scale * 100, 100 - green_w)
    red_w   = max(100 - green_w - amber_w, 0)

    bar = html.Div([
        html.Div(style={
            "display": "flex", "height": "18px", "borderRadius": "6px",
            "overflow": "hidden", "marginBottom": "6px",
        }, children=[
            html.Div(style={"flex": green_w, "backgroundColor": TAAL_GREEN,  "opacity": "0.8"}),
            html.Div(style={"flex": amber_w, "backgroundColor": "#B8860B",   "opacity": "0.8"}),
            html.Div(style={"flex": red_w,   "backgroundColor": TAAL_RED,    "opacity": "0.8"}),
        ]),
        html.Div([
            html.Span("0%",          style={"fontSize": "10px", "color": TAAL_MUTED}),
            html.Span(f"{low:.1f}%", style={"fontSize": "10px", "color": TAAL_MUTED,
                                             "marginLeft": f"{green_w:.0f}%"}),
            html.Span(f"{high:.1f}%",style={"fontSize": "10px", "color": TAAL_MUTED,
                                             "marginLeft": f"{amber_w:.0f}%"}),
        ], style={"display": "flex", "gap": "0"}),
    ])

    return legend, bar


# ── Refresh / Reload buttons ──────────────────────────────────────────────────
@app.callback(
    Output("refresh-status",         "children"),
    Output("refresh-status",         "style"),
    Output("refresh-redirect",       "href"),
    Output("refresh-loading-output", "children"),
    Input("refresh-btn",             "n_clicks"),
    Input("reload-btn",              "n_clicks"),
    prevent_initial_call=True,
)
def run_pipeline_refresh(n_clicks, n_reload):
    if ctx.triggered_id == "reload-btn":
        # Redirect browser first, then restart the Python process so all
        # module-level data (CSVs, etc.) is re-read from disk.
        def _restart():
            import time, os
            time.sleep(1.2)          # give browser time to receive the redirect
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=_restart, daemon=False).start()
        return "", {}, "/", ""
    pipeline_script = BASE / "pipeline.py"
    base_style = {"fontSize": "13px", "paddingTop": "2px", "fontWeight": "500"}

    try:
        result = subprocess.run(
            [sys.executable, str(pipeline_script)],
            capture_output=True, text=True, cwd=str(BASE), timeout=120,
        )
        if result.returncode == 0:
            def _restart():
                import time, os
                time.sleep(1.2)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=_restart, daemon=False).start()
            return (
                "✓ Pipeline finished — restarting…",
                {**base_style, "color": TAAL_GREEN},
                "/",
                "",
            )
        else:
            err = (result.stderr or result.stdout or "Unknown error")[:300]
            return (
                f"Pipeline error: {err}",
                {**base_style, "color": TAAL_RED},
                dash.no_update,
                "",
            )
    except subprocess.TimeoutExpired:
        return (
            "Timed out after 120 s. Check the terminal for details.",
            {**base_style, "color": TAAL_RED},
            dash.no_update,
            "",
        )
    except Exception as exc:
        return (
            f"Error: {exc}",
            {**base_style, "color": TAAL_RED},
            dash.no_update,
            "",
        )


# ── Stop button: kill the dashboard process and free port 8050 ────────────────
@app.callback(
    Output("stop-btn", "children"),
    Output("stop-btn", "disabled"),
    Output("stop-btn", "style"),
    Input("stop-btn", "n_clicks"),
    prevent_initial_call=True,
)
def stop_dashboard(n_clicks):
    import signal, threading
    def _kill():
        import time
        time.sleep(0.8)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_kill, daemon=False).start()
    stopped_style = {
        "backgroundColor": TAAL_RED,
        "color": "white",
        "border": f"1px solid {TAAL_RED}",
        "borderRadius": "6px",
        "padding": "10px 24px",
        "fontSize": "13px",
        "fontWeight": "600",
        "cursor": "not-allowed",
        "letterSpacing": "0.02em",
        "opacity": "0.7",
    }
    return "Stopped — port 8050 is free", True, stopped_style


# ─────────────────────────────────────────────────────────────────────────────
# EITJE API HELPERS
# ─────────────────────────────────────────────────────────────────────────────
import urllib.request, urllib.error

EITJE_BASE = "https://open-api.eitje.app/open_api"

def _eitje_headers(username, password, partner_user="", partner_pass=""):
    h = {"Api-Username": username, "Api-Password": password,
         "Content-Type": "application/json"}
    if partner_user:
        h["Partner-Username"] = partner_user
        h["Partner-Password"] = partner_pass
    return h

def eitje_get(endpoint, body, username, password, partner_user="", partner_pass=""):
    """Call Eitje Open API. body=None → plain GET; body=dict → GET with JSON body."""
    import json as _json
    url = f"{EITJE_BASE}/{endpoint}"
    headers = _eitje_headers(username, password, partner_user, partner_pass)
    if body is None:
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        req = urllib.request.Request(
            url, data=_json.dumps(body).encode(), headers=headers, method="GET"
        )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()[:300]
        return None, f"HTTP {e.code}: {body_txt}"
    except Exception as e:
        return None, str(e)


def fetch_planning_shifts(start_date, end_date, username, password,
                          partner_user="", partner_pass=""):
    """Fetch planning shifts for a date range (max 7 days per call)."""
    import json as _json, datetime
    from datetime import timedelta
    all_shifts = []
    cur = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    while cur <= end:
        chunk_end = min(cur + timedelta(days=6), end)
        body = {"filters": {"start_date": str(cur), "end_date": str(chunk_end)}}
        data, err = eitje_get("planning_shifts", body, username, password,
                              partner_user, partner_pass)
        if err:
            return None, err
        all_shifts.extend(data.get("items", []))
        cur = chunk_end + timedelta(days=1)
    return all_shifts, None


def _shift_hours(s):
    """Compute hours from a single Eitje shift dict."""
    try:
        st = pd.Timestamp(s["start"])
        en = pd.Timestamp(s["end"])
        if st.tzinfo is not None:
            st = st.tz_convert(None)
        if en.tzinfo is not None:
            en = en.tz_convert(None)
        mins = (en - st).total_seconds() / 60 - float(s.get("break_minutes", 0) or 0)
        return max(mins / 60, 0)
    except Exception:
        return 0.0


def shifts_to_daily(shifts):
    """
    Convert raw Eitje planning_shifts list to:
      {
        "dates":    {"YYYY-MM-DD": [{"employee": str, "hours": float, "status": str}, ...]},
        "statuses": {"status_name": count, ...}   # all unique statuses + counts
      }
    """
    import datetime
    dates: dict = {}
    status_counts: dict = {}
    for s in shifts:
        date_str = (s.get("date") or "").strip()
        if not date_str:
            continue
        try:
            datetime.date.fromisoformat(date_str)   # validate
        except Exception:
            continue
        hours    = _shift_hours(s)
        status   = str(s.get("status") or "scheduled").lower().strip()
        employee = ((s.get("user") or {}).get("name") or "Unknown")
        dates.setdefault(date_str, []).append(
            {"employee": employee, "hours": hours, "status": status}
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    return {"dates": dates, "statuses": status_counts}


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULING TAB
# ─────────────────────────────────────────────────────────────────────────────
def make_scheduling_tab():
    import datetime
    today        = datetime.date.today()
    this_monday  = today - datetime.timedelta(days=today.weekday())

    inp_style = {"width": "100%", "padding": "7px 10px",
                 "border": f"1px solid {TAAL_CLAY}",
                 "borderRadius": "6px", "fontSize": "13px"}
    lbl_style = {"fontSize": "12px", "fontWeight": "600",
                 "color": TAAL_DARK, "marginBottom": "4px", "display": "block"}
    nav_btn = {"backgroundColor": "white", "color": TAAL_DARK,
               "border": f"1px solid {TAAL_CLAY}", "borderRadius": "6px",
               "padding": "6px 16px", "fontSize": "15px",
               "fontWeight": "700", "cursor": "pointer"}

    controls = html.Div([
        dbc.Row([
            dbc.Col([
                html.Label("Avg hourly cost (€)", style=lbl_style),
                dcc.Input(id="sched-hourly-rate", type="number", value=18,
                          min=1, max=100, step=0.5, debounce=True, style=inp_style),
            ], md=2),
            dbc.Col([
                html.Label("Target labor % of revenue", style=lbl_style),
                dcc.Input(id="sched-target-pct", type="number", value=30,
                          min=1, max=80, step=1, debounce=True, style=inp_style),
            ], md=2),
            dbc.Col([
                html.Label("Hour type from Eitje", style=lbl_style),
                html.Div(id="sched-hour-type-wrap",
                         children=[
                             dcc.RadioItems(id="sched-hour-type",
                                            options=[], value=None,
                                            style={"display": "none"}),
                             html.Div(
                                 "Fetch shifts first to see available hour types",
                                 style={"fontSize": "11px", "color": TAAL_MUTED,
                                        "paddingTop": "6px"}
                             ),
                         ]),
            ], md=4),
            dbc.Col([
                html.Label("\u00a0", style=lbl_style),   # spacer
                html.Div([
                    html.Button("Fetch Shifts", id="sched-fetch-btn",
                        style={"backgroundColor": TAAL_DARK, "color": "white",
                               "border": "none", "borderRadius": "6px",
                               "padding": "8px 20px", "fontSize": "13px",
                               "fontWeight": "600", "cursor": "pointer",
                               "marginRight": "10px"}),
                    html.Span(id="sched-fetch-status",
                              style={"fontSize": "11px", "color": TAAL_MUTED}),
                ], style={"display": "flex", "alignItems": "center",
                          "paddingTop": "4px"}),
            ], md=4),
        ], className="g-3 align-items-end"),
    ], style={**CARD_STYLE, "marginBottom": "16px"})

    week_nav = html.Div([
        dbc.Row([
            dbc.Col([
                html.Button("←", id="sched-prev-week", style=nav_btn),
                html.Span(id="sched-week-label",
                          style={"fontSize": "15px", "fontWeight": "700",
                                 "color": TAAL_DARK, "margin": "0 18px"}),
                html.Button("→", id="sched-next-week", style=nav_btn),
            ], width="auto"),
        ], align="center"),
    ], style={**CARD_STYLE, "marginBottom": "16px",
              "display": "flex", "alignItems": "center"})

    return html.Div([
        controls,
        week_nav,
        html.Div(id="sched-content"),
    ], style={"padding": "0 4px"})


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS – Eitje test connection callback
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("eitje-test-status", "children"),
    Output("eitje-test-status", "style"),
    Output("eitje-creds-store", "data"),
    Input("eitje-test-btn", "n_clicks"),
    State("eitje-username",     "value"),
    State("eitje-password",     "value"),
    State("eitje-partner-user", "value"),
    State("eitje-partner-pass", "value"),
    prevent_initial_call=True,
)
def test_eitje_connection(_, username, password, partner_user, partner_pass):
    base = {"fontSize": "12px", "paddingTop": "8px", "fontWeight": "500"}
    if not username or not password:
        return "Enter username and password first.", {**base, "color": TAAL_RED}, {}
    if not partner_user or not partner_pass:
        return ("✗ Partner credentials required — the Eitje Open API needs both your API credentials "
                "and separate Partner credentials. Contact Eitje support to obtain Partner-Username "
                "and Partner-Password for your account.",
                {**base, "color": TAAL_RED}, {})
    data, err = eitje_get("environments", None, username, password,
                          partner_user or "", partner_pass or "")
    if err:
        if "partner not found" in err.lower():
            msg = ("✗ Partner not found — your Partner credentials were not recognised. "
                   "Check Partner-Username/Password with Eitje support.")
        elif "not all required" in err.lower():
            msg = ("✗ Incomplete auth — the API requires Api-Username, Api-Password, "
                   "Partner-Username, and Partner-Password. Fill all four fields.")
        else:
            msg = f"✗ {err}"
        return msg, {**base, "color": TAAL_RED}, {}
    envs = [e.get("name","?") for e in (data.get("items") or [])]
    creds = {"username": username, "password": password,
             "partner_user": partner_user or "", "partner_pass": partner_pass or ""}
    return (f"✓ Connected — environments: {', '.join(envs) or 'none'}",
            {**base, "color": TAAL_GREEN}, creds)


@app.callback(
    Output("eitje-creds-store", "data", allow_duplicate=True),
    Output("eitje-test-status", "children", allow_duplicate=True),
    Output("eitje-test-status", "style", allow_duplicate=True),
    Input("eitje-save-btn", "n_clicks"),
    State("eitje-username",     "value"),
    State("eitje-password",     "value"),
    State("eitje-partner-user", "value"),
    State("eitje-partner-pass", "value"),
    prevent_initial_call=True,
)
def save_eitje_creds(_, username, password, partner_user, partner_pass):
    base = {"fontSize": "12px", "paddingTop": "8px", "fontWeight": "500"}
    if not username or not password:
        return {}, "Enter username and password first.", {**base, "color": TAAL_RED}
    creds = {"username": username, "password": password,
             "partner_user": partner_user or "", "partner_pass": partner_pass or ""}
    return creds, "✓ Credentials saved.", {**base, "color": TAAL_GREEN}


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULING – fetch shifts callback
# ─────────────────────────────────────────────────────────────────────────────
def _load_daily_revenue():
    """Re-read daily_summary.csv and return {date_str: float} for the revenue store."""
    try:
        df = pd.read_csv(DATA / "daily_summary.csv")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df["_rev"] = (
            pd.to_numeric(df.get("Total Revenue excl. VAT"), errors="coerce")
            .fillna(
                pd.to_numeric(df.get("Net Amount VAT 9%"),  errors="coerce").fillna(0)
                + pd.to_numeric(df.get("Net Amount VAT 21%"), errors="coerce").fillna(0)
                + pd.to_numeric(df.get("Net Amount VAT 0%"),  errors="coerce").fillna(0)
            )
        )
        return {
            str(row["date"]): float(row["_rev"])
            for _, row in df.dropna(subset=["date"]).iterrows()
            if pd.notna(row["_rev"])
        }
    except Exception:
        return {}


@app.callback(
    Output("sched-shifts-store",   "data"),
    Output("sched-revenue-store",  "data"),
    Output("sched-fetch-status",   "children"),
    Output("sched-hour-type-wrap", "children"),
    Input("sched-fetch-btn",       "n_clicks"),
    State("eitje-creds-store",     "data"),
    State("sched-shifts-store",    "data"),
    prevent_initial_call=True,
)
def fetch_shifts(_, creds, existing_store):
    import datetime

    # Always refresh revenue from CSV (picks up newly added POS files)
    fresh_revenue = _load_daily_revenue()
    n_rev_days    = len(fresh_revenue)

    no_creds_msg = html.Div(
        "⚠ Set Eitje credentials in Settings first.",
        style={"fontSize": "11px", "color": TAAL_RED}
    )
    if not creds or not creds.get("username"):
        return (
            (existing_store or {}),
            fresh_revenue,
            f"Revenue refreshed ({n_rev_days} days)  ⚠ Set Eitje credentials in Settings",
            no_creds_msg,
        )

    # Fetch current month only (1st of month → end of month or today)
    today      = datetime.date.today()
    month_start = today.replace(day=1)
    # Also include next 4 weeks of future scheduling
    fetch_end  = today + datetime.timedelta(weeks=4)

    shifts, err = fetch_planning_shifts(
        str(month_start), str(fetch_end),
        creds["username"], creds["password"],
        creds.get("partner_user", ""), creds.get("partner_pass", ""),
    )
    if err:
        err_widget = html.Div(f"✗ {err}", style={"fontSize": "11px", "color": TAAL_RED})
        msg = f"Revenue refreshed ({n_rev_days} days)  ✗ Shifts: {err}"
        return (existing_store or {}), fresh_revenue, msg, err_widget

    new_store = shifts_to_daily(shifts)

    # ── Merge: keep all historical dates, overwrite only fetched date range ──
    merged_dates    = dict((existing_store or {}).get("dates", {}))
    merged_statuses = dict((existing_store or {}).get("statuses", {}))

    # Remove dates in the fetched range from the existing store, then add new ones
    for d_str in list(merged_dates.keys()):
        try:
            d = datetime.date.fromisoformat(d_str)
            if month_start <= d <= fetch_end:
                del merged_dates[d_str]
        except Exception:
            pass

    merged_dates.update(new_store.get("dates", {}))

    # Rebuild status counts across all dates
    merged_statuses = {}
    for day_shifts in merged_dates.values():
        for s in day_shifts:
            st = s.get("status", "scheduled")
            merged_statuses[st] = merged_statuses.get(st, 0) + 1

    merged_store = {"dates": merged_dates, "statuses": merged_statuses}

    n_new    = len(new_store.get("dates", {}))
    n_total  = len(merged_dates)
    status_counts = merged_statuses

    # Build radio options from all statuses in merged store
    if not status_counts:
        type_widget = html.Div("No shifts found.",
                               style={"fontSize": "11px", "color": TAAL_MUTED})
    else:
        options = [
            {"label": f" {k}  ({v} shifts)", "value": k}
            for k, v in sorted(status_counts.items())
        ]
        default = next(
            (k for k in ("approved", "published", "scheduled")
             if k in status_counts),
            list(status_counts.keys())[0]
        )
        type_widget = html.Div([
            dcc.RadioItems(
                id="sched-hour-type",
                options=options,
                value=default,
                inline=True,
                labelStyle={"marginRight": "18px", "fontSize": "12px",
                            "fontWeight": "500"},
            ),
            html.Div(
                "Select which shift status to use for labor cost calculation.",
                style={"fontSize": "11px", "color": TAAL_MUTED, "marginTop": "4px"},
            ),
        ])

    status_summary = "  ".join(f"{k}: {v}" for k, v in sorted(status_counts.items()))
    msg = (f"✓ Refreshed {n_new} days (current month+4wk)  |  "
           f"Total history: {n_total} days  |  Revenue: {n_rev_days} days  "
           f"[{status_summary}]")
    return merged_store, fresh_revenue, msg, type_widget


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULING – week navigation
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("sched-week-store", "data"),
    Output("sched-week-label", "children"),
    Input("sched-prev-week",   "n_clicks"),
    Input("sched-next-week",   "n_clicks"),
    State("sched-week-store",  "data"),
    prevent_initial_call=False,
)
def navigate_week(prev_clicks, next_clicks, week_iso):
    import datetime
    triggered = ctx.triggered_id
    try:
        week = datetime.date.fromisoformat(week_iso)
    except Exception:
        today = datetime.date.today()
        week  = today - datetime.timedelta(days=today.weekday())

    if triggered == "sched-prev-week":
        week -= datetime.timedelta(weeks=1)
    elif triggered == "sched-next-week":
        week += datetime.timedelta(weeks=1)

    week_end = week + datetime.timedelta(days=6)
    label = f"Week {week.strftime('%V')}  ·  {week.strftime('%-d %b')} – {week_end.strftime('%-d %b %Y')}"
    return week.isoformat(), label


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULING – daily chart for selected week
# ─────────────────────────────────────────────────────────────────────────────
@app.callback(
    Output("sched-content",      "children"),
    Input("sched-shifts-store",  "data"),
    Input("sched-revenue-store", "data"),
    Input("sched-week-store",    "data"),
    Input("sched-hourly-rate",   "value"),
    Input("sched-target-pct",    "value"),
    Input("sched-hour-type",     "value"),
)
def render_scheduling(shifts_data, revenue_data, week_iso, hourly_rate, target_pct, hour_type):
    import datetime
    hourly_rate = float(hourly_rate or 18)
    target_pct  = float(target_pct  or 30)
    revenue_data = revenue_data or {}

    try:
        week_start = datetime.date.fromisoformat(week_iso)
    except Exception:
        today = datetime.date.today()
        week_start = today - datetime.timedelta(days=today.weekday())

    week_days = [week_start + datetime.timedelta(days=i) for i in range(7)]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # ── Revenue per day from store (seeded at startup, refreshed on Fetch) ──────
    daily_revenue = [revenue_data.get(d.isoformat(), None) for d in week_days]
    has_revenue   = any(v is not None for v in daily_revenue)

    # ── Labor hours per day from shifts store ─────────────────────────────────
    has_shifts = bool(shifts_data and shifts_data.get("dates"))
    daily_hours = [0.0] * 7
    if has_shifts:
        dates_dict = shifts_data["dates"]
        for i, d in enumerate(week_days):
            day_shifts = dates_dict.get(d.isoformat(), [])
            # Filter by selected hour type (if any)
            if hour_type:
                day_shifts = [s for s in day_shifts
                              if s.get("status", "").lower() == hour_type]
            daily_hours[i] = sum(s["hours"] for s in day_shifts)

    daily_labor = [h * hourly_rate for h in daily_hours]

    if not has_revenue and not has_shifts:
        return html.Div([
            html.Div("No data for this week.",
                     style={"color": TAAL_MUTED, "padding": "30px 0",
                            "textAlign": "center", "fontSize": "14px"}),
            html.Div("Revenue comes from your POS sales reports (loaded from Dashboard Data). "
                     "Labor cost comes from Eitje shifts — click 'Fetch Shifts' above.",
                     style={"color": TAAL_MUTED, "textAlign": "center",
                            "fontSize": "12px", "paddingBottom": "30px"}),
        ], style=CARD_STYLE)

    x_labels = [f"{n}\n{d.strftime('%-d %b')}" for n, d in zip(day_names, week_days)]

    # ── Chart ─────────────────────────────────────────────────────────────────
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        name="Net Revenue",
        x=x_labels,
        y=[v if v is not None else 0 for v in daily_revenue],
        marker_color=TAAL_GREEN, opacity=0.80,
        customdata=daily_revenue,
        hovertemplate="<b>%{x}</b><br>Revenue: €%{customdata:,.0f}<extra></extra>",
    ), secondary_y=False)

    labor_color = [
        (TAAL_RED   if lc > 0 and rev and lc / rev * 100 > target_pct + 5 else
         "#B8860B"  if lc > 0 and rev and lc / rev * 100 > target_pct else
         TAAL_BLUE)
        for lc, rev in zip(daily_labor, daily_revenue)
    ]
    fig.add_trace(go.Bar(
        name=f"Labor cost ({hour_type or 'scheduled'})",
        x=x_labels,
        y=daily_labor,
        marker_color=labor_color, opacity=0.80,
        customdata=list(zip(daily_hours, daily_labor)),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Hours: %{customdata[0]:.1f}h<br>"
            "Labor cost: €%{customdata[1]:,.0f}"
            "<extra></extra>"
        ),
    ), secondary_y=False)

    # Labor % line
    labor_pct = [
        (lc / rev * 100 if rev and rev > 0 else None)
        for lc, rev in zip(daily_labor, daily_revenue)
    ]
    fig.add_trace(go.Scatter(
        name="Labor %",
        x=x_labels, y=labor_pct,
        mode="lines+markers",
        line=dict(color=TAAL_RED, width=2.5), marker=dict(size=8),
        hovertemplate="<b>%{x}</b><br>Labor %%: %{y:.1f}%%<extra></extra>",
    ), secondary_y=True)

    fig.add_hline(y=target_pct, line_dash="dash", line_color=TAAL_CLAY,
                  line_width=1.5, secondary_y=True,
                  annotation_text=f"Target {target_pct:.0f}%",
                  annotation_position="bottom right")

    week_rev   = sum(v for v in daily_revenue if v)
    week_labor = sum(daily_labor)
    week_pct   = (week_labor / week_rev * 100) if week_rev else None
    title_txt  = (
        f"Daily Revenue vs Labor Cost"
        + (f"   |   Week total: Revenue €{week_rev:,.0f}  ·  "
           f"Labor €{week_labor:,.0f}  ·  "
           f"{week_pct:.1f}% {'✓' if week_pct and week_pct <= target_pct else '⚠'}"
           if week_rev else "")
    )

    apply_layout(fig,
        barmode="group", height=380,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=LEGEND_H,
        title=dict(text=title_txt, font=dict(size=14)),
        xaxis=dict(tickfont=dict(size=11), **AXIS_STYLE),
        yaxis=dict(title="EUR", tickformat=",.0f", **AXIS_STYLE),
        yaxis2=dict(title="Labor %", ticksuffix="%", showgrid=False,
                    range=[0, max(80, (max((p for p in labor_pct if p), default=0) + 10))],
                    linecolor="#E8E0D5"),
    )

    # ── Daily detail table ────────────────────────────────────────────────────
    BASE_C = {"fontFamily": "Inter, sans-serif", "fontSize": "12px",
              "padding": "7px 14px", "borderBottom": "1px solid #F0EBE3",
              "textAlign": "right", "whiteSpace": "nowrap"}
    HDR_C  = {**BASE_C, "backgroundColor": TAAL_DARK, "color": "white",
              "fontWeight": "600", "fontSize": "11px", "textAlign": "right"}
    LBL_C  = {**BASE_C, "textAlign": "left", "minWidth": "90px"}

    rows = []
    for i, (name, d, rev, hours, lc, pct) in enumerate(
        zip(day_names, week_days, daily_revenue, daily_hours, daily_labor, labor_pct)
    ):
        is_today = (d == datetime.date.today())
        diff = (pct - target_pct) if pct is not None else None
        pct_col = (TAAL_GREEN if diff is not None and diff <= 0
                   else TAAL_RED   if diff is not None and diff > 5
                   else "#B8860B"  if diff is not None else TAAL_MUTED)
        row_style = {"backgroundColor": "#FFFBF4"} if is_today else {}
        rows.append(html.Tr([
            html.Td(f"{name} {d.strftime('%-d %b')}",
                    style={**LBL_C, "fontWeight": "700" if is_today else "400",
                           **row_style}),
            html.Td(f"€{rev:,.0f}" if rev else "—",
                    style={**BASE_C, **row_style}),
            html.Td(f"{hours:.1f}h" if hours else "—",
                    style={**BASE_C, **row_style}),
            html.Td(f"€{lc:,.0f}" if lc else "—",
                    style={**BASE_C, **row_style}),
            html.Td(f"{pct:.1f}%" if pct is not None else "—",
                    style={**BASE_C, "color": pct_col, "fontWeight": "600",
                           **row_style}),
            html.Td(
                (f"+{diff:.1f}%" if diff > 0 else f"{diff:.1f}%")
                if diff is not None else "—",
                style={**BASE_C, "color": pct_col, **row_style}
            ),
        ]))

    # Totals row
    week_diff = (week_pct - target_pct) if week_pct is not None else None
    diff_col  = (TAAL_GREEN if week_diff and week_diff <= 0
                 else TAAL_RED if week_diff and week_diff > 5
                 else "#B8860B" if week_diff else TAAL_DARK)
    rows.append(html.Tr([
        html.Td("TOTAL", style={**LBL_C, "fontWeight": "700",
                                 "backgroundColor": "#F5EFE6"}),
        html.Td(f"€{week_rev:,.0f}" if week_rev else "—",
                style={**BASE_C, "fontWeight": "700",
                       "backgroundColor": "#F5EFE6"}),
        html.Td(f"{sum(daily_hours):.1f}h",
                style={**BASE_C, "fontWeight": "700",
                       "backgroundColor": "#F5EFE6"}),
        html.Td(f"€{week_labor:,.0f}",
                style={**BASE_C, "fontWeight": "700",
                       "backgroundColor": "#F5EFE6"}),
        html.Td(f"{week_pct:.1f}%" if week_pct else "—",
                style={**BASE_C, "fontWeight": "700", "color": diff_col,
                       "backgroundColor": "#F5EFE6"}),
        html.Td(
            (f"+{week_diff:.1f}%" if week_diff > 0 else f"{week_diff:.1f}%")
            if week_diff is not None else "—",
            style={**BASE_C, "color": diff_col, "fontWeight": "700",
                   "backgroundColor": "#F5EFE6"}
        ),
    ]))

    hdr = html.Tr([
        html.Th("Day",        style={**HDR_C, "textAlign": "left"}),
        html.Th("Net Revenue",style=HDR_C),
        html.Th("Hours",      style=HDR_C),
        html.Th("Labor cost", style=HDR_C),
        html.Th("Labor %",    style={**HDR_C, "color": TAAL_ACCENT}),
        html.Th("vs Target",  style=HDR_C),
    ])
    table = html.Table(
        [html.Thead(hdr), html.Tbody(rows)],
        style={"width": "100%", "borderCollapse": "collapse"},
    )

    return html.Div([
        html.Div([dcc.Graph(figure=fig, config={"displayModeBar": False})],
                 style={**CARD_STYLE, "marginBottom": "16px"}),
        html.Div([
            html.Div(f"Daily Detail  ·  hour type: {hour_type or '—'}",
                     style={"fontWeight": "700", "fontSize": "13px",
                            "marginBottom": "12px", "color": TAAL_DARK}),
            html.Div(table, style={"overflowX": "auto"}),
        ], style=CARD_STYLE),
    ])


server = app.server  # exposed for gunicorn: gunicorn dashboard:server

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import webbrowser, threading, time

    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://127.0.0.1:8050")

    threading.Thread(target=open_browser, daemon=True).start()
    print("\nLera World Dashboard  →  http://127.0.0.1:8050\n")
    port = int(os.environ.get("PORT", 8050))
    app.run(debug=False, host="0.0.0.0", port=port)
