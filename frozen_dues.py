import streamlit as st
import pandas as pd
import os
from datetime import datetime, date
import requests

# =========================================================
# CONFIG
# =========================================================
LOGO_PATH = "logo.png"
RESET_PASSWORD = "c12q7mgh"

SUPPLIERS_SHEET = "suppliers"
INVOICES_SHEET = "invoices"

SUPPLIERS_COLS = ["supplier_name", "total_due", "credit_balance"]

INVOICES_COLS = [
    "date",               # invoice date (YYYY-MM-DD)
    "branch",
    "supplier",
    "invoice_amount",
    "delivery_cost",
    "total_due",
    "paid_total",
    "paid_cash",
    "paid_visa",          # keep stored as "paid_visa" but UI calls it "Bank"
    "payment_date",
    "payment_note",
    "remaining",
    "credit",
    "status",
    "auto_unique_id",
]

branches = [
    "Frozen Obour",
    "Frozen Shrouq",
    "Frozen Mostakbal",
    "Frozen Zayed",
    "Frozen Heliopolis",
    "Frozen Maadi",
]

# =========================================================
# BRAND COLORS
# =========================================================
BRAND_DARK_BLUE = "#001f3f"
BRAND_LIGHT_BLUE = "#ADD8E6"
BRAND_YELLOW = "#FFDC00"
BRAND_WHITE = "#FFFFFF"

# =========================================================
# API (Google Apps Script Web App)
# Streamlit Secrets:
# [api]
# url   = "https://script.google.com/macros/s/.../exec"
# token = "FrozenFinance2026Key"
# =========================================================
def _get_api_conf():
    if "api" not in st.secrets:
        raise RuntimeError("Missing [api] in Streamlit Secrets.")
    if "url" not in st.secrets["api"] or "token" not in st.secrets["api"]:
        raise RuntimeError("Missing api.url or api.token in Streamlit Secrets.")
    return st.secrets["api"]["url"], st.secrets["api"]["token"]


def api_read(sheet_name: str) -> pd.DataFrame:
    url, token = _get_api_conf()
    r = requests.get(url, params={"action": "read", "sheet": sheet_name, "token": token}, timeout=30)

    text = (r.text or "").strip()
    if not text.startswith("{"):
        raise RuntimeError(f"API did not return JSON. Status={r.status_code}. First 200 chars: {text[:200]}")

    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "API error"))

    return pd.DataFrame(payload.get("data", []))


def api_write(sheet_name: str, df: pd.DataFrame):
    url, token = _get_api_conf()
    df = df.copy()
    df = df.where(pd.notnull(df), "")

    r = requests.post(
        url,
        json={"action": "write", "sheet": sheet_name, "token": token, "rows": df.to_dict("records")},
        timeout=60,
    )

    text = (r.text or "").strip()
    if not text.startswith("{"):
        raise RuntimeError(f"API did not return JSON. Status={r.status_code}. First 200 chars: {text[:200]}")

    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "API error"))


# =========================================================
# DATE NORMALIZATION (NO .dt)
# =========================================================
def _excel_serial_to_date(n: float):
    try:
        base = pd.Timestamp("1899-12-30")
        return (base + pd.to_timedelta(float(n), unit="D")).to_pydatetime()
    except Exception:
        return None


def _to_storage_date(x) -> str:
    """Store dates as plain tagged text to stop Google Sheets / timezone coercion."""
    iso = _to_yyyy_mm_dd(x)
    return f"TEXTDATE:{iso}" if iso else ""


def _strip_date_tag(s: str) -> str:
    s = str(s or "").strip()
    return s.split("TEXTDATE:", 1)[1].strip() if s.startswith("TEXTDATE:") else s


def _to_yyyy_mm_dd(x) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass

    if isinstance(x, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(x).strftime("%Y-%m-%d")
        except Exception:
            return ""

    # Excel serial number
    try:
        num = float(x)
        if 20000 < num < 80000:
            d = _excel_serial_to_date(num)
            if d:
                return pd.Timestamp(d).strftime("%Y-%m-%d")
    except Exception:
        pass

    s = _strip_date_tag(x)
    if s == "" or s.lower() in ("none", "nan", "nat"):
        return ""

    # Strict parsing first to avoid month/day flipping
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d",       # canonical ISO-like
        "%d-%m-%Y", "%d/%m/%Y",       # user-facing day-first
        "%m-%d-%Y", "%m/%d/%Y",       # month-first legacy
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    # Fallback: if separator is '-' or '/', prefer day-first for ambiguous text
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _normalize_dates(series: pd.Series) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series([], dtype=str)
    return series.apply(_to_yyyy_mm_dd).astype(str)


def _parse_user_date(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass

    if isinstance(x, pd.Timestamp):
        return x.date()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x

    s = _strip_date_tag(x)
    if s == "" or s.lower() in ("none", "nan", "nat"):
        return None

    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d",
        "%d-%m-%Y", "%d/%m/%Y",
        "%m-%d-%Y", "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(dt):
            return None
        return pd.Timestamp(dt).date()
    except Exception:
        return None


def _display_dd_mm_yyyy(x) -> str:
    d = _parse_user_date(x)
    return d.strftime("%d-%m-%Y") if d else ""


# =========================================================
# MIGRATION / NORMALIZATION
# =========================================================
def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def migrate_invoices(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if df.empty:
        return pd.DataFrame(columns=INVOICES_COLS)

    if "full_amount" in df.columns and "invoice_amount" not in df.columns:
        df["invoice_amount"] = df["full_amount"]
    if "paid_amount" in df.columns and "paid_total" not in df.columns:
        df["paid_total"] = df["paid_amount"]

    df = ensure_columns(
        df,
        [
            "date", "branch", "supplier", "invoice_amount", "delivery_cost", "total_due",
            "paid_total", "paid_cash", "paid_visa", "payment_date", "payment_note",
            "remaining", "credit", "status", "auto_unique_id"
        ],
    )

    df["date"] = _normalize_dates(df["date"]).apply(lambda v: f"TEXTDATE:{v}" if v else "")
    df["payment_date"] = _normalize_dates(df["payment_date"]).apply(lambda v: f"TEXTDATE:{v}" if v else "")

    num_cols = ["invoice_amount", "delivery_cost", "total_due", "paid_total", "paid_cash", "paid_visa", "remaining", "credit"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["auto_unique_id"] = pd.to_numeric(df["auto_unique_id"], errors="coerce").fillna(-1).astype(int)

    df["total_due"] = (df["invoice_amount"] + df["delivery_cost"]).round(2)

    has_split = (df["paid_cash"] + df["paid_visa"]) > 0
    df.loc[has_split, "paid_total"] = (df.loc[has_split, "paid_cash"] + df.loc[has_split, "paid_visa"]).round(2)

    diff = (df["total_due"] - df["paid_total"]).round(2)
    df["remaining"] = diff.clip(lower=0).round(2)
    df["credit"] = (-diff).clip(lower=0).round(2)

    def status_row(r):
        if r["credit"] > 0:
            return "Credit"
        if r["remaining"] == 0 and r["paid_total"] > 0:
            return "Paid"
        if r["paid_total"] > 0 and r["remaining"] > 0:
            return "Partial"
        return "Unpaid"

    df["status"] = df.apply(status_row, axis=1)
    return ensure_columns(df, INVOICES_COLS)


def migrate_suppliers(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if df.empty:
        return pd.DataFrame(columns=SUPPLIERS_COLS)

    df = ensure_columns(df, SUPPLIERS_COLS)
    df["total_due"] = pd.to_numeric(df["total_due"], errors="coerce").fillna(0.0)
    df["credit_balance"] = pd.to_numeric(df["credit_balance"], errors="coerce").fillna(0.0)
    return df


def next_invoice_id(invoices: pd.DataFrame) -> int:
    if invoices.empty:
        return 0
    return int(pd.to_numeric(invoices["auto_unique_id"], errors="coerce").fillna(-1).max()) + 1


def recalc_supplier_balances(suppliers: pd.DataFrame, invoices: pd.DataFrame) -> pd.DataFrame:
    suppliers = suppliers.copy()
    invoices = invoices.copy()

    inv_supps = set(invoices["supplier"].dropna().astype(str).tolist())
    sup_supps = set(suppliers["supplier_name"].dropna().astype(str).tolist())
    missing = sorted(list(inv_supps - sup_supps))
    if missing:
        suppliers = pd.concat(
            [suppliers, pd.DataFrame({"supplier_name": missing, "total_due": [0.0]*len(missing), "credit_balance": [0.0]*len(missing)})],
            ignore_index=True,
        )

    due_by_supplier = invoices.groupby("supplier")["remaining"].sum()
    credit_by_supplier = invoices.groupby("supplier")["credit"].sum()

    suppliers = suppliers.set_index("supplier_name")
    suppliers["total_due"] = suppliers.index.map(lambda s: float(due_by_supplier.get(s, 0.0)))
    suppliers["credit_balance"] = suppliers.index.map(lambda s: float(credit_by_supplier.get(s, 0.0)))
    suppliers.reset_index(inplace=True)

    suppliers["total_due"] = pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0.0)
    suppliers["credit_balance"] = pd.to_numeric(suppliers["credit_balance"], errors="coerce").fillna(0.0)
    return ensure_columns(suppliers, SUPPLIERS_COLS)


def recompute_invoice_row(df: pd.DataFrame, idx: int):
    date_iso = _to_yyyy_mm_dd(df.at[idx, "date"])
    pay_iso = _to_yyyy_mm_dd(df.at[idx, "payment_date"])
    df.at[idx, "date"] = f"TEXTDATE:{date_iso}" if date_iso else ""
    df.at[idx, "payment_date"] = f"TEXTDATE:{pay_iso}" if pay_iso else ""

    for c in ["invoice_amount", "delivery_cost", "paid_cash", "paid_visa", "paid_total"]:
        df.at[idx, c] = float(pd.to_numeric(df.at[idx, c], errors="coerce") or 0.0)

    df.at[idx, "total_due"] = round(float(df.at[idx, "invoice_amount"]) + float(df.at[idx, "delivery_cost"]), 2)
    df.at[idx, "paid_total"] = round(float(df.at[idx, "paid_cash"]) + float(df.at[idx, "paid_visa"]), 2)

    diff = round(float(df.at[idx, "total_due"]) - float(df.at[idx, "paid_total"]), 2)
    df.at[idx, "remaining"] = round(max(diff, 0.0), 2)
    df.at[idx, "credit"] = round(max(-diff, 0.0), 2)

    if float(df.at[idx, "credit"]) > 0:
        df.at[idx, "status"] = "Credit"
    elif float(df.at[idx, "remaining"]) == 0 and float(df.at[idx, "paid_total"]) > 0:
        df.at[idx, "status"] = "Paid"
    elif float(df.at[idx, "paid_total"]) > 0 and float(df.at[idx, "remaining"]) > 0:
        df.at[idx, "status"] = "Partial"
    else:
        df.at[idx, "status"] = "Unpaid"


# =========================================================
# FAST LOAD (CACHE) + SAVE
# =========================================================
@st.cache_data(ttl=20, show_spinner=False)
def _cached_load_all():
    raw_suppliers = api_read(SUPPLIERS_SHEET)
    raw_invoices = api_read(INVOICES_SHEET)
    suppliers = migrate_suppliers(raw_suppliers)
    invoices = migrate_invoices(raw_invoices)
    suppliers = recalc_supplier_balances(suppliers, invoices)
    return suppliers, invoices


def load_all():
    return _cached_load_all()


def save_all(suppliers: pd.DataFrame, invoices: pd.DataFrame):
    suppliers = recalc_supplier_balances(suppliers, invoices)
    api_write(INVOICES_SHEET, ensure_columns(invoices, INVOICES_COLS))
    api_write(SUPPLIERS_SHEET, ensure_columns(suppliers, SUPPLIERS_COLS))
    _cached_load_all.clear()  # refresh cache after write


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="Frozen Supplier Invoices Management", layout="wide")

st.markdown(
    f"""
<style>
[data-testid="stAppViewContainer"] {{ background-color: {BRAND_LIGHT_BLUE}; }}
[data-testid="stSidebar"] {{ background-color: {BRAND_DARK_BLUE}; }}
[data-testid="stSidebar"] * {{ color: {BRAND_WHITE} !important; }}
h1,h2,h3,h4,h5,h6 {{ color: {BRAND_DARK_BLUE}; font-weight: 800; }}
.stButton > button {{
    background-color: {BRAND_YELLOW};
    color: {BRAND_DARK_BLUE};
    border: 0;
    border-radius: 10px;
    font-weight: 800;
    padding: 0.55rem 1rem;
}}
.brand-card {{
    background: rgba(255,255,255,0.55);
    border: 1px solid rgba(0,0,0,0.06);
    border-radius: 16px;
    padding: 14px 16px;
    margin: 8px 0 14px 0;
}}
</style>
""",
    unsafe_allow_html=True,
)

if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, width=260)

st.title("Frozen Supplier Invoices Management")

st.sidebar.title("Navigation")
page = st.sidebar.radio(
    "Go to",
    ["Add Supplier", "Add Invoice", "View Dues", "View Invoices", "Reset (Admin)"],
)

# Load data
try:
    suppliers, invoices = load_all()
except Exception as e:
    st.error(f"Failed to load data from Google Sheet API: {e}")
    st.stop()

# =========================================================
# Add Supplier
# =========================================================
if "supplier_add_msg" not in st.session_state:
    st.session_state["supplier_add_msg"] = None  # ("success"|"error", "text")


def add_supplier_callback():
    try:
        suppliers_local, invoices_local = load_all()
        name = (st.session_state.get("new_supplier_name", "") or "").strip()

        if name == "":
            st.session_state["supplier_add_msg"] = ("error", "Supplier name cannot be empty.")
            return

        if name in suppliers_local["supplier_name"].astype(str).values:
            st.session_state["supplier_add_msg"] = ("error", "Supplier already exists.")
            return

        suppliers_local = pd.concat(
            [suppliers_local, pd.DataFrame({"supplier_name": [name], "total_due": [0.0], "credit_balance": [0.0]})],
            ignore_index=True,
        )
        save_all(suppliers_local, invoices_local)

        st.session_state["new_supplier_name"] = ""
        st.session_state["supplier_add_msg"] = ("success", f"Supplier '{name}' added.")
    except Exception as ex:
        st.session_state["supplier_add_msg"] = ("error", f"Failed to add supplier: {ex}")


if page == "Add Supplier":
    st.header("Add New Supplier")

    if st.session_state["supplier_add_msg"]:
        kind, txt = st.session_state["supplier_add_msg"]
        (st.success if kind == "success" else st.error)(txt)
        st.session_state["supplier_add_msg"] = None

    if "new_supplier_name" not in st.session_state:
        st.session_state["new_supplier_name"] = ""

    st.text_input("Supplier Name", key="new_supplier_name", placeholder="please add new supplier")
    st.button("Add Supplier", on_click=add_supplier_callback)

# =========================================================
# Add Invoice
# =========================================================
elif page == "Add Invoice":
    st.header("Add New Invoice")

    if "invoice_add_msg" not in st.session_state:
        st.session_state["invoice_add_msg"] = None

    defaults = {
        "inv_branch": "Select Branch",
        "inv_supplier": "Select Supplier",
        "inv_date": None,
        "pay_date": None,
        "inv_amount": 0.0,
        "del_cost": 0.0,
        "paid_cash": 0.0,
        "paid_visa": 0.0,
        "pay_note": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    def add_invoice_callback():
        try:
            suppliers_local, invoices_local = load_all()

            branch = st.session_state.get("inv_branch", "Select Branch")
            supplier = st.session_state.get("inv_supplier", "Select Supplier")
            invoice_date = st.session_state.get("inv_date", None)
            payment_date = st.session_state.get("pay_date", None)

            invoice_amount = float(st.session_state.get("inv_amount", 0.0) or 0.0)
            delivery_cost = float(st.session_state.get("del_cost", 0.0) or 0.0)
            paid_cash = float(st.session_state.get("paid_cash", 0.0) or 0.0)
            paid_visa = float(st.session_state.get("paid_visa", 0.0) or 0.0)
            note = (st.session_state.get("pay_note", "") or "").strip()

            errors = []
            if branch == "Select Branch":
                errors.append("Please select a Branch.")
            if supplier == "Select Supplier":
                errors.append("Please select a Supplier.")
            if invoice_date is None:
                errors.append("Please select an Invoice Date.")
            if (paid_cash + paid_visa) > 0 and payment_date is None:
                errors.append("Please select a Payment Date (because paid amount > 0).")

            if errors:
                st.session_state["invoice_add_msg"] = ("error", " | ".join(errors))
                return

            total_due = float(invoice_amount + delivery_cost)
            paid_total = float(paid_cash + paid_visa)
            remaining = max(total_due - paid_total, 0.0)
            credit = max(paid_total - total_due, 0.0)

            if credit > 0:
                status = "Credit"
            elif remaining == 0 and paid_total > 0:
                status = "Paid"
            elif paid_total > 0 and remaining > 0:
                status = "Partial"
            else:
                status = "Unpaid"

            new_row = {
                "date": _to_storage_date(invoice_date),
                "branch": branch,
                "supplier": supplier,
                "invoice_amount": float(invoice_amount),
                "delivery_cost": float(delivery_cost),
                "total_due": float(total_due),
                "paid_total": float(paid_total),
                "paid_cash": float(paid_cash),
                "paid_visa": float(paid_visa),
                "payment_date": _to_storage_date(payment_date) if paid_total > 0 else "",
                "payment_note": note,
                "remaining": float(remaining),
                "credit": float(credit),
                "status": status,
                "auto_unique_id": next_invoice_id(invoices_local),
            }

            invoices_local = pd.concat([invoices_local, pd.DataFrame([new_row])], ignore_index=True)
            invoices_local = migrate_invoices(invoices_local)
            save_all(suppliers_local, invoices_local)

            for k, v in defaults.items():
                st.session_state[k] = v

            st.session_state["invoice_add_msg"] = ("success", "Invoice added successfully.")

        except Exception as ex:
            st.session_state["invoice_add_msg"] = ("error", f"Failed to add invoice: {ex}")

    if suppliers.empty:
        st.warning("No suppliers yet. Add supplier first.")
    else:
        if st.session_state["invoice_add_msg"]:
            kind, txt = st.session_state["invoice_add_msg"]
            (st.success if kind == "success" else st.error)(txt)
            st.session_state["invoice_add_msg"] = None

        c1, c2, c3 = st.columns(3)
        with c1:
            branch_options = ["Select Branch"] + branches
            st.selectbox("Branch", branch_options, key="inv_branch")
        with c2:
            supplier_options = ["Select Supplier"] + suppliers["supplier_name"].astype(str).tolist()
            st.selectbox("Supplier", supplier_options, key="inv_supplier")
        with c3:
            st.date_input("Invoice Date", value=st.session_state["inv_date"], key="inv_date")

        st.subheader("Invoice Amounts")
        a1, a2 = st.columns(2)
        with a1:
            st.number_input("Invoice Amount (without delivery)", min_value=0.0, step=0.01, key="inv_amount")
        with a2:
            st.number_input("Delivery Cost (0 if none)", min_value=0.0, step=0.01, key="del_cost")

        st.subheader("Payment Details (0 / partial / full / overpaid)")
        p1, p2, p3 = st.columns(3)
        with p1:
            st.date_input("Payment Date", value=st.session_state["pay_date"], key="pay_date")
        with p2:
            st.number_input("Paid Cash", min_value=0.0, step=0.01, key="paid_cash")
        with p3:
            st.number_input("Paid Bank (Visa)", min_value=0.0, step=0.01, key="paid_visa")

        st.text_input("Payment Note (optional)", key="pay_note")

        total_due = float((st.session_state.get("inv_amount") or 0.0) + (st.session_state.get("del_cost") or 0.0))
        paid_total = float((st.session_state.get("paid_cash") or 0.0) + (st.session_state.get("paid_visa") or 0.0))
        remaining = max(total_due - paid_total, 0.0)
        credit = max(paid_total - total_due, 0.0)

        if credit > 0:
            status = "Credit"
        elif remaining == 0 and paid_total > 0:
            status = "Paid"
        elif paid_total > 0 and remaining > 0:
            status = "Partial"
        else:
            status = "Unpaid"

        st.markdown(
            f"""
            <div class='brand-card'>
              <b>Total Due:</b> {total_due:,.2f}<br>
              <b>Paid Total:</b> {paid_total:,.2f}<br>
              <b>Remaining:</b> {remaining:,.2f}<br>
              <b>Credit:</b> {credit:,.2f}<br>
              <b>Status:</b> {status}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.button("Submit Invoice", on_click=add_invoice_callback)

# =========================================================
# View Dues (MOBILE FRIENDLY)
# =========================================================
elif page == "View Dues":
    st.header("Supplier Dues")

    total_due_sum = float(pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0.0).sum())
    total_credit_sum = float(pd.to_numeric(suppliers["credit_balance"], errors="coerce").fillna(0.0).sum())

    st.markdown(
        f"""
        <div class='brand-card'>
          <b>Total Due (All Suppliers):</b> {total_due_sum:,.2f}<br>
          <b>Total Credit (All Suppliers):</b> {total_credit_sum:,.2f}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.dataframe(
        suppliers,
        use_container_width=True,
        hide_index=True,
        column_config={
            "supplier_name": st.column_config.TextColumn("Supplier", width="medium"),
            "total_due": st.column_config.NumberColumn("Total Due", format="%.2f", width="small"),
            "credit_balance": st.column_config.NumberColumn("Credit", format="%.2f", width="small"),
        },
    )

# =========================================================
# View Invoices (MOBILE FRIENDLY, FAST)
# =========================================================
elif page == "View Invoices":
    st.header("All Invoices")

    if invoices.empty:
        st.info("No invoices yet.")
    else:
        # ---------- Filters (native) ----------
        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 1.2])
        with f1:
            branch_filter = st.selectbox("Branch", ["All"] + branches, index=0)
        with f2:
            supplier_filter = st.selectbox("Supplier", ["All"] + sorted(invoices["supplier"].astype(str).unique().tolist()), index=0)
        with f3:
            status_filter = st.selectbox("Status", ["All", "Unpaid", "Partial", "Paid", "Credit"], index=0)
        with f4:
            date_mode = st.selectbox("Date Filter", ["All", "Between"], index=0)

        start_d, end_d = None, None
        if date_mode == "Between":
            d1, d2 = st.columns(2)
            with d1:
                start_d = st.date_input("From", value=date.today())
            with d2:
                end_d = st.date_input("To", value=date.today())

        df = invoices.copy()
        df["date"] = df["date"].astype(str)
        df["payment_date"] = df["payment_date"].astype(str)
        df["date_sort"] = df["date"].apply(_parse_user_date)

        if branch_filter != "All":
            df = df[df["branch"] == branch_filter]
        if supplier_filter != "All":
            df = df[df["supplier"] == supplier_filter]
        if status_filter != "All":
            df = df[df["status"] == status_filter]

        if date_mode == "Between" and start_d and end_d:
            df = df[df["date_sort"].notna()]
            df = df[(df["date_sort"] >= start_d) & (df["date_sort"] <= end_d)]

        filtered_remaining_sum = float(pd.to_numeric(df.get("remaining", 0), errors="coerce").fillna(0).sum())
        st.markdown(
            f"<div class='brand-card'><b>Filtered Total Remaining:</b> {filtered_remaining_sum:,.2f}</div>",
            unsafe_allow_html=True,
        )

        # ---------- Selection table (checkbox) ----------
        view = df.copy()
        view["date"] = view["date"].apply(_display_dd_mm_yyyy)
        view["payment_date"] = view["payment_date"].apply(_display_dd_mm_yyyy)
        if "date_sort" in view.columns:
            view = view.drop(columns=["date_sort"])
        view.insert(0, "Select", False)

        # Prevent edits except checkbox: we will ignore any other edits anyway by disabling columns
        disabled_cols = [c for c in view.columns if c != "Select"]

        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            disabled=disabled_cols,
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", width="small"),
                "date": st.column_config.TextColumn("Invoice Date", width="small"),
                "branch": st.column_config.TextColumn("Branch", width="medium"),
                "supplier": st.column_config.TextColumn("Supplier", width="medium"),
                "invoice_amount": st.column_config.NumberColumn("Invoice Amount", format="%.2f", width="small"),
                "delivery_cost": st.column_config.NumberColumn("Delivery", format="%.2f", width="small"),
                "total_due": st.column_config.NumberColumn("Total Due", format="%.2f", width="small"),
                "paid_total": st.column_config.NumberColumn("Paid Total", format="%.2f", width="small"),
                "paid_cash": st.column_config.NumberColumn("Paid Cash", format="%.2f", width="small"),
                "paid_visa": st.column_config.NumberColumn("Paid Bank", format="%.2f", width="small"),
                "payment_date": st.column_config.TextColumn("Payment Date", width="small"),
                "payment_note": st.column_config.TextColumn("Note", width="large"),
                "remaining": st.column_config.NumberColumn("Remaining", format="%.2f", width="small"),
                "credit": st.column_config.NumberColumn("Credit", format="%.2f", width="small"),
                "status": st.column_config.TextColumn("Status", width="small"),
                "auto_unique_id": st.column_config.NumberColumn("ID", width="small"),
            },
        )

        selected = edited[edited["Select"] == True].copy()
        if selected.empty:
            st.info("Select one or more invoices using the checkbox.")
        else:
            selected_ids = sorted(pd.to_numeric(selected["auto_unique_id"], errors="coerce").fillna(-1).astype(int).tolist())
            st.markdown(
                f"<div class='brand-card'><b>Selected:</b> {len(selected_ids)} invoice(s)<br><b>IDs:</b> {', '.join(map(str, selected_ids[:120]))}{' ...' if len(selected_ids)>120 else ''}</div>",
                unsafe_allow_html=True,
            )

            st.subheader("Actions")
            action = st.radio("Choose action", ["Full Pay", "Partial Pay", "Delete Invoice(s)"], horizontal=True)

            pay_date = st.date_input("Payment Date", datetime.now().date(), key="mobile_pay_date")
            method = st.selectbox("Payment Method", ["Bank", "Cash", "Cash + Bank"], index=0)
            note = st.text_input("Payment Note (optional)", value="")

            cash_amt = 0.0
            bank_amt = 0.0
            total_amt = 0.0

            if action == "Partial Pay":
                if method in ["Bank", "Cash"]:
                    total_amt = st.number_input("Amount", min_value=0.0, step=0.01, value=0.0)
                else:
                    c1, c2 = st.columns(2)
                    with c1:
                        cash_amt = st.number_input("Cash Amount", min_value=0.0, step=0.01, value=0.0)
                    with c2:
                        bank_amt = st.number_input("Bank Amount", min_value=0.0, step=0.01, value=0.0)
                    total_amt = float(cash_amt + bank_amt)
            elif action == "Full Pay" and method == "Cash + Bank":
                cash_amt = st.number_input("Cash Amount", min_value=0.0, step=0.01, value=0.0)

            del_pw = ""
            if action == "Delete Invoice(s)":
                del_pw = st.text_input("Enter password to delete", type="password")

            def _apply_payment_to_invoice(df_local: pd.DataFrame, idx: int, add_cash: float, add_bank: float, pdate: date, pnote: str):
                df_local.at[idx, "paid_cash"] = float(pd.to_numeric(df_local.at[idx, "paid_cash"], errors="coerce") or 0.0) + float(add_cash)
                df_local.at[idx, "paid_visa"] = float(pd.to_numeric(df_local.at[idx, "paid_visa"], errors="coerce") or 0.0) + float(add_bank)
                df_local.at[idx, "payment_date"] = _to_storage_date(pdate)
                if (pnote or "").strip():
                    df_local.at[idx, "payment_note"] = (pnote or "").strip()
                recompute_invoice_row(df_local, idx)

            if st.button("APPLY", use_container_width=True):
                suppliers_local, invoices_local = load_all()
                invoices_local["auto_unique_id"] = pd.to_numeric(invoices_local["auto_unique_id"], errors="coerce").fillna(-1).astype(int)
                id_to_idx = {int(invoices_local.at[i, "auto_unique_id"]): i for i in invoices_local.index}

                if action == "Delete Invoice(s)":
                    if del_pw != RESET_PASSWORD:
                        st.error("Wrong password.")
                        st.stop()

                    before = len(invoices_local)
                    invoices_local = invoices_local[~invoices_local["auto_unique_id"].isin(selected_ids)].copy()
                    invoices_local = migrate_invoices(invoices_local)
                    save_all(suppliers_local, invoices_local)
                    st.success(f"Deleted {before - len(invoices_local)} invoice(s).")
                    st.rerun()

                # PAYMENTS
                updated = 0
                for inv_id in selected_ids:
                    if inv_id not in id_to_idx:
                        continue
                    i = id_to_idx[inv_id]

                    for c in ["paid_cash", "paid_visa", "invoice_amount", "delivery_cost", "paid_total", "total_due"]:
                        invoices_local.at[i, c] = float(pd.to_numeric(invoices_local.at[i, c], errors="coerce") or 0.0)

                    recompute_invoice_row(invoices_local, i)
                    needed = max(float(invoices_local.at[i, "total_due"]) - float(invoices_local.at[i, "paid_total"]), 0.0)

                    if action == "Full Pay":
                        if method == "Bank":
                            add_cash, add_bank = 0.0, float(needed)
                        elif method == "Cash":
                            add_cash, add_bank = float(needed), 0.0
                        else:
                            if float(cash_amt) < 0:
                                st.error("Cash amount cannot be negative.")
                                st.stop()
                            if float(cash_amt) - float(needed) > 0.01:
                                st.error(f"Cash amount cannot exceed remaining for FULL PAY. Invoice {inv_id} remaining = {needed:,.2f}")
                                st.stop()
                            add_cash = float(cash_amt)
                            add_bank = round(float(needed) - float(cash_amt), 2)

                        _apply_payment_to_invoice(invoices_local, i, add_cash, add_bank, pay_date, note)
                        updated += 1
                    else:
                        if float(total_amt) <= 0:
                            st.error("Partial Pay amount must be > 0.")
                            st.stop()

                        if method == "Bank":
                            add_cash, add_bank = 0.0, float(total_amt)
                        elif method == "Cash":
                            add_cash, add_bank = float(total_amt), 0.0
                        else:
                            add_cash, add_bank = float(cash_amt), float(bank_amt)

                        _apply_payment_to_invoice(invoices_local, i, add_cash, add_bank, pay_date, note)
                        updated += 1

                invoices_local = migrate_invoices(invoices_local)
                save_all(suppliers_local, invoices_local)
                st.success(f"Done. Updated {updated} invoice(s).")
                st.rerun()

# =========================================================
# Reset (Admin)
# =========================================================
elif page == "Reset (Admin)":
    st.header("Reset Data (Admin)")
    st.warning("This will DELETE all rows in suppliers and invoices (keeps headers). Cannot be undone.")

    pw = st.text_input("Enter password to enable reset", type="password")
    confirm = st.checkbox("I understand this will delete all data")

    if st.button("RESET NOW", use_container_width=True):
        if pw != RESET_PASSWORD:
            st.error("Wrong password.")
            st.stop()
        if not confirm:
            st.error("You must confirm the checkbox.")
            st.stop()

        api_write(SUPPLIERS_SHEET, pd.DataFrame(columns=SUPPLIERS_COLS))
        api_write(INVOICES_SHEET, pd.DataFrame(columns=INVOICES_COLS))
        _cached_load_all.clear()
        st.success("Reset completed. Reloading...")
        st.rerun()