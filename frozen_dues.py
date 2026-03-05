import streamlit as st
import pandas as pd
import os
from datetime import datetime, date
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode
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
    "paid_visa",
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
BRAND_GREEN = "#c8f7c5"
BRAND_RED = "#ffd6d6"
BRAND_AMBER = "#fff2cc"

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

    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nan", "nat"):
        return ""

    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s

    try:
        dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
        if pd.isna(dt):
            return ""
        return pd.Timestamp(dt).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _normalize_dates(series: pd.Series) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series([], dtype=str)
    return series.apply(_to_yyyy_mm_dd).astype(str)


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

    df["date"] = _normalize_dates(df["date"])
    df["payment_date"] = _normalize_dates(df["payment_date"])

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


def load_all():
    raw_suppliers = api_read(SUPPLIERS_SHEET)
    raw_invoices = api_read(INVOICES_SHEET)
    suppliers = migrate_suppliers(raw_suppliers)
    invoices = migrate_invoices(raw_invoices)
    suppliers = recalc_supplier_balances(suppliers, invoices)
    return suppliers, invoices


def save_all(suppliers: pd.DataFrame, invoices: pd.DataFrame):
    suppliers = recalc_supplier_balances(suppliers, invoices)
    api_write(INVOICES_SHEET, ensure_columns(invoices, INVOICES_COLS))
    api_write(SUPPLIERS_SHEET, ensure_columns(suppliers, SUPPLIERS_COLS))


def recompute_invoice_row(df: pd.DataFrame, idx: int):
    df.at[idx, "date"] = _to_yyyy_mm_dd(df.at[idx, "date"])
    df.at[idx, "payment_date"] = _to_yyyy_mm_dd(df.at[idx, "payment_date"])

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
.stButton > button:hover {{ filter: brightness(0.95); transform: translateY(-1px); }}
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
        if kind == "success":
            st.success(txt)
        else:
            st.error(txt)
        st.session_state["supplier_add_msg"] = None

    if "new_supplier_name" not in st.session_state:
        st.session_state["new_supplier_name"] = ""

    st.text_input(
        "Supplier Name",
        key="new_supplier_name",
        placeholder="please add new supplier",
    )

    st.button("Add Supplier", on_click=add_supplier_callback)

# =========================================================
# Add Invoice (RESET TO DEFAULT AFTER SUBMIT)
# =========================================================
elif page == "Add Invoice":
    st.header("Add New Invoice")

    if "invoice_add_msg" not in st.session_state:
        st.session_state["invoice_add_msg"] = None  # ("success"|"error", "text")

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
                "date": invoice_date.strftime("%Y-%m-%d"),
                "branch": branch,
                "supplier": supplier,
                "invoice_amount": float(invoice_amount),
                "delivery_cost": float(delivery_cost),
                "total_due": float(total_due),
                "paid_total": float(paid_total),
                "paid_cash": float(paid_cash),
                "paid_visa": float(paid_visa),
                "payment_date": payment_date.strftime("%Y-%m-%d") if paid_total > 0 else "",
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
            if kind == "success":
                st.success(txt)
            else:
                st.error(txt)
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
            st.number_input("Paid Visa", min_value=0.0, step=0.01, key="paid_visa")

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
# View Dues
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

    gb = GridOptionsBuilder.from_dataframe(suppliers)
    gb.configure_default_column(editable=False, filterable=True, sortable=True)
    gb.configure_column("supplier_name", filter="agTextColumnFilter")
    AgGrid(suppliers, gb.build(), height=450, fit_columns_on_grid_load=True)

# =========================================================
# View Invoices
# =========================================================
elif page == "View Invoices":
    st.header("All Invoices")

    if invoices.empty:
        st.info("No invoices yet.")
    else:
        row_style = JsCode(
            f"""
            function(params) {{
                if (!params || !params.data) return null;
                if (params.data.status === 'Paid') return {{ 'backgroundColor': '{BRAND_GREEN}' }};
                if (params.data.status === 'Credit') return {{ 'backgroundColor': '{BRAND_AMBER}' }};
                if (params.data.status === 'Partial' || params.data.status === 'Unpaid') return {{ 'backgroundColor': '{BRAND_RED}' }};
                return null;
            }}
            """
        )

        display_df = invoices.copy()
        display_df.insert(0, "_select", False)

        gb = GridOptionsBuilder.from_dataframe(display_df)
        gb.configure_default_column(editable=False, filterable=False, sortable=True)

        gb.configure_column(
            "_select",
            header_name="Select",
            editable=True,
            width=90,
            cellRenderer="agCheckboxCellRenderer",
            pinned="left",
        )

        gb.configure_column("date", header_name="Invoice Date", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("branch", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("supplier", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("status", filter="agTextColumnFilter", filterable=True)

        gb.configure_column("auto_unique_id", hide=True)

        grid_options = gb.build()
        grid_options["getRowStyle"] = row_style

        grid_response = AgGrid(
            display_df,
            gridOptions=grid_options,
            height=520,
            fit_columns_on_grid_load=True,
            update_mode=GridUpdateMode.VALUE_CHANGED | GridUpdateMode.MODEL_CHANGED,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            allow_unsafe_jscode=True,
        )

        grid_data = pd.DataFrame(grid_response.get("data", display_df.to_dict("records")))
        if "_select" not in grid_data.columns:
            grid_data["_select"] = False

        selected = grid_data[grid_data["_select"] == True].copy()

        filtered_remaining_sum = float(pd.to_numeric(grid_data.get("remaining", 0), errors="coerce").fillna(0).sum())
        st.markdown(
            f"<div class='brand-card'><b>Filtered Total Remaining:</b> {filtered_remaining_sum:,.2f}</div>",
            unsafe_allow_html=True,
        )

        st.subheader("Payment Actions (tick Select checkbox in rows above)")
        if selected.empty:
            st.info("Tick the Select checkbox for one or more invoices above.")
        else:
            selected_ids = sorted(pd.to_numeric(selected["auto_unique_id"], errors="coerce").fillna(-1).astype(int).tolist())
            st.markdown(
                f"<div class='brand-card'><b>Selected:</b> {len(selected_ids)} invoice(s)<br><b>IDs:</b> {', '.join(map(str, selected_ids[:80]))}{' ...' if len(selected_ids)>80 else ''}</div>",
                unsafe_allow_html=True,
            )

            action = st.radio(
                "Choose action",
                ["Mark selected as FULLY PAID", "Add SAME payment amount to each selected invoice"],
            )

            pay_date = st.date_input("Payment Date", datetime.now().date(), key="bulk_pay_date")

            method = st.selectbox("Payment Method", ["Cash", "Visa", "Cash + Visa"], index=2)
            if method == "Cash":
                ratio_cash, ratio_visa = 1.0, 0.0
            elif method == "Visa":
                ratio_cash, ratio_visa = 0.0, 1.0
            else:
                c1, c2 = st.columns(2)
                with c1:
                    pc = st.number_input("Cash %", min_value=0.0, max_value=100.0, value=50.0, step=1.0)
                with c2:
                    pv = st.number_input("Visa %", min_value=0.0, max_value=100.0, value=50.0, step=1.0)
                s = max(pc + pv, 1e-9)
                ratio_cash, ratio_visa = pc / s, pv / s

            note = st.text_input("Payment Note (optional)", value="")

            if action == "Add SAME payment amount to each selected invoice":
                add_amount = st.number_input("Amount to add to EACH selected invoice", min_value=0.0, step=0.01, value=0.0)

            if st.button("APPLY ACTION"):
                suppliers, invoices = load_all()
                invoices["auto_unique_id"] = pd.to_numeric(invoices["auto_unique_id"], errors="coerce").fillna(-1).astype(int)
                id_to_idx = {int(invoices.at[i, "auto_unique_id"]): i for i in invoices.index}

                updated = 0
                for inv_id in selected_ids:
                    if inv_id not in id_to_idx:
                        continue
                    i = id_to_idx[inv_id]

                    for c in ["paid_cash", "paid_visa", "invoice_amount", "delivery_cost"]:
                        invoices.at[i, c] = float(pd.to_numeric(invoices.at[i, c], errors="coerce") or 0.0)

                    invoices.at[i, "payment_date"] = pay_date.strftime("%Y-%m-%d")
                    if (note or "").strip():
                        invoices.at[i, "payment_note"] = (note or "").strip()

                    recompute_invoice_row(invoices, i)

                    if action == "Mark selected as FULLY PAID":
                        total_due = float(invoices.at[i, "total_due"])
                        paid_total = float(invoices.at[i, "paid_total"])
                        needed = max(total_due - paid_total, 0.0)
                        invoices.at[i, "paid_cash"] = float(invoices.at[i, "paid_cash"]) + needed * ratio_cash
                        invoices.at[i, "paid_visa"] = float(invoices.at[i, "paid_visa"]) + needed * ratio_visa
                        recompute_invoice_row(invoices, i)
                    else:
                        amt = float(add_amount)
                        invoices.at[i, "paid_cash"] = float(invoices.at[i, "paid_cash"]) + amt * ratio_cash
                        invoices.at[i, "paid_visa"] = float(invoices.at[i, "paid_visa"]) + amt * ratio_visa
                        recompute_invoice_row(invoices, i)

                    updated += 1

                invoices = migrate_invoices(invoices)
                save_all(suppliers, invoices)
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

    if st.button("RESET NOW"):
        if pw != RESET_PASSWORD:
            st.error("Wrong password.")
            st.stop()
        if not confirm:
            st.error("You must confirm the checkbox.")
            st.stop()

        api_write(SUPPLIERS_SHEET, pd.DataFrame(columns=SUPPLIERS_COLS))
        api_write(INVOICES_SHEET, pd.DataFrame(columns=INVOICES_COLS))
        st.success("Reset completed. Reloading...")
        st.rerun()