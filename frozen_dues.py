import streamlit as st
import pandas as pd
import os
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import io
import requests

# =========================================================
# CONFIG
# =========================================================
LOGO_PATH = "logo.png"  # keep in GitHub repo

SUPPLIERS_SHEET = "suppliers"
INVOICES_SHEET = "invoices"

SUPPLIERS_COLS = ["supplier_name", "total_due"]
INVOICES_COLS = [
    "date",
    "branch",
    "supplier",
    "full_amount",
    "paid_amount",
    "remaining",
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
BRAND_GREEN = "#c8f7c5"  # Paid row background
BRAND_RED = "#ffd6d6"    # Unpaid row background

# =========================================================
# API (Google Apps Script Web App)
# Streamlit Secrets must contain:
#
# [api]
# url = "https://script.google.com/macros/s/XXXXX/exec"
# token = "FrozenFinance2026Key"
# =========================================================
def _get_api_conf():
    if "api" not in st.secrets:
        raise RuntimeError("Missing [api] in Streamlit Secrets.")
    if "url" not in st.secrets["api"] or "token" not in st.secrets["api"]:
        raise RuntimeError("Missing api.url or api.token in Streamlit Secrets.")
    return st.secrets["api"]["url"], st.secrets["api"]["token"]


def api_read(sheet_name: str, expected_cols: list[str]) -> pd.DataFrame:
    url, token = _get_api_conf()

    r = requests.get(
        url,
        params={"action": "read", "sheet": sheet_name, "token": token},
        timeout=30,
    )

    text = (r.text or "").strip()
    if not text.startswith("{"):
        raise RuntimeError(
            f"API did not return JSON. Status={r.status_code}. First 200 chars: {text[:200]}"
        )

    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "API error"))

    df = pd.DataFrame(payload.get("data", []))
    if df.empty:
        df = pd.DataFrame(columns=expected_cols)

    # Keep ONLY expected columns (ignore extras like ::auto_unique_id::...)
    for c in expected_cols:
        if c not in df.columns:
            df[c] = ""
    df = df[expected_cols]

    return df


def api_write(sheet_name: str, df: pd.DataFrame, expected_cols: list[str]):
    url, token = _get_api_conf()

    df = df.copy()

    # Keep ONLY expected columns in correct order
    for c in expected_cols:
        if c not in df.columns:
            df[c] = ""
    df = df[expected_cols]

    # Replace NaN with empty
    df = df.where(pd.notnull(df), "")

    rows = df.to_dict("records")
    r = requests.post(
        url,
        json={"action": "write", "sheet": sheet_name, "token": token, "rows": rows},
        timeout=60,
    )

    text = (r.text or "").strip()
    if not text.startswith("{"):
        raise RuntimeError(
            f"API did not return JSON. Status={r.status_code}. First 200 chars: {text[:200]}"
        )

    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "API error"))


# =========================================================
# DATE NORMALIZATION (FIXED .dt ERROR + BLANK DATES)
# Handles:
# - '2026-03-03'
# - '1/13/2026'
# - serial numbers (Excel/Sheets) like 46035
# - blanks
# =========================================================
def _normalize_dates(series: pd.Series) -> pd.Series:
    """
    Robust date parsing that ALWAYS returns YYYY-MM-DD strings.
    Prevents: "Can only use .dt accessor with datetimelike values"
    """
    if series is None:
        return pd.Series([], dtype=str)

    s = series.copy()

    if len(s) == 0:
        return pd.Series([], dtype=str)

    # Force datetime dtype from start (prevents .dt crash)
    out_dt = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # 1) Numeric serial conversion (Excel/Sheets serial days)
    s_num = pd.to_numeric(s, errors="coerce")
    serial_mask = s_num.notna() & (s_num > 20000) & (s_num < 80000)
    if serial_mask.any():
        out_dt.loc[serial_mask] = pd.to_datetime(
            s_num.loc[serial_mask],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    # 2) Parse remaining as strings
    str_mask = ~serial_mask
    if str_mask.any():
        s_str = s.astype(str).str.strip()
        s_str = s_str.replace({"": pd.NA, "None": pd.NA, "nan": pd.NA, "NaT": pd.NA})
        out_dt.loc[str_mask] = pd.to_datetime(
            s_str.loc[str_mask],
            errors="coerce",
            infer_datetime_format=True,
        )

    return out_dt.dt.strftime("%Y-%m-%d").fillna("")


# =========================================================
# LOAD / SAVE
# =========================================================
def load_all():
    suppliers = api_read(SUPPLIERS_SHEET, SUPPLIERS_COLS)
    invoices = api_read(INVOICES_SHEET, INVOICES_COLS)

    # Types
    suppliers["total_due"] = pd.to_numeric(
        suppliers["total_due"], errors="coerce"
    ).fillna(0.0)

    for c in ["full_amount", "paid_amount", "remaining"]:
        invoices[c] = pd.to_numeric(invoices[c], errors="coerce").fillna(0.0)

    invoices["auto_unique_id"] = pd.to_numeric(
        invoices["auto_unique_id"], errors="coerce"
    ).fillna(-1).astype(int)

    invoices["date"] = _normalize_dates(invoices["date"])

    invoices["status"] = invoices["status"].replace("", pd.NA).fillna("Unpaid")

    # Ensure remaining isn't negative
    invoices["remaining"] = invoices["remaining"].clip(lower=0)

    return suppliers, invoices


def save_suppliers(suppliers: pd.DataFrame):
    api_write(SUPPLIERS_SHEET, suppliers, SUPPLIERS_COLS)


def save_invoices(invoices: pd.DataFrame):
    api_write(INVOICES_SHEET, invoices, INVOICES_COLS)


def next_invoice_id(invoices: pd.DataFrame) -> int:
    if invoices.empty:
        return 0
    return int(invoices["auto_unique_id"].max()) + 1


def recalculate_dues_and_save(suppliers: pd.DataFrame, invoices: pd.DataFrame):
    suppliers = suppliers.copy()
    invoices = invoices.copy()

    # Ensure supplier list includes any supplier name found in invoices
    inv_supps = set(invoices["supplier"].dropna().astype(str).tolist())
    sup_supps = set(suppliers["supplier_name"].dropna().astype(str).tolist())
    missing = sorted(list(inv_supps - sup_supps))
    if missing:
        suppliers = pd.concat(
            [
                suppliers,
                pd.DataFrame({"supplier_name": missing, "total_due": [0.0] * len(missing)}),
            ],
            ignore_index=True,
        )

    suppliers["total_due"] = 0.0
    unpaid = invoices[invoices["status"] == "Unpaid"].groupby("supplier")["remaining"].sum()

    suppliers = suppliers.set_index("supplier_name")
    suppliers["total_due"] = suppliers["total_due"].add(unpaid, fill_value=0).clip(lower=0)
    suppliers.reset_index(inplace=True)

    # Save
    save_invoices(invoices)
    save_suppliers(suppliers)

    return suppliers, invoices


def normalize_selected_rows(grid_response):
    if not grid_response or "selected_rows" not in grid_response:
        return []
    sr = grid_response["selected_rows"]
    if sr is None:
        return []
    if isinstance(sr, list):
        return sr
    if isinstance(sr, pd.DataFrame):
        return sr.to_dict("records")
    return []


def get_filtered_rows(grid_response, fallback_df: pd.DataFrame) -> pd.DataFrame:
    if not grid_response:
        return fallback_df
    data = grid_response.get("data", None)
    if data is None:
        return fallback_df
    try:
        return pd.DataFrame(data)
    except Exception:
        return fallback_df


def clamp_session_number(key: str, min_v: float, max_v: float):
    if key in st.session_state:
        try:
            v = float(st.session_state[key])
            st.session_state[key] = max(min_v, min(max_v, v))
        except Exception:
            st.session_state[key] = min_v


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="Frozen Products Invoice Management", layout="wide")

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

.stTextInput input, .stNumberInput input {{ border-radius: 10px !important; }}

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

st.title("Frozen Products Invoice Management")

st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Add Supplier", "Add Invoice", "View Dues", "View Invoices"])

# Load latest from Sheets
try:
    suppliers, invoices = load_all()
except Exception as e:
    st.error(f"Failed to load data from Google Sheet API: {e}")
    st.stop()

# =========================================================
# ADD SUPPLIER
# =========================================================
if page == "Add Supplier":
    st.header("Add New Supplier")

    new_supplier = st.text_input("Supplier Name")

    if st.button("Add Supplier"):
        new_supplier = new_supplier.strip()

        if new_supplier == "":
            st.error("Supplier name cannot be empty.")
        elif new_supplier in suppliers["supplier_name"].astype(str).values:
            st.error("Supplier already exists.")
        else:
            suppliers = pd.concat(
                [suppliers, pd.DataFrame({"supplier_name": [new_supplier], "total_due": [0.0]})],
                ignore_index=True,
            )
            save_suppliers(suppliers)
            st.success(f"Supplier '{new_supplier}' added successfully.")
            st.rerun()

# =========================================================
# ADD INVOICE
# =========================================================
elif page == "Add Invoice":
    st.header("Add New Invoice")

    if suppliers.empty:
        st.warning("No suppliers added yet. Please add a supplier first.")
    else:
        # session defaults
        if "confirm_invoice_open" not in st.session_state:
            st.session_state["confirm_invoice_open"] = False
        if "pending_invoice" not in st.session_state:
            st.session_state["pending_invoice"] = None
        if "reset_invoice_inputs" not in st.session_state:
            st.session_state["reset_invoice_inputs"] = False

        if "inv_branch" not in st.session_state:
            st.session_state["inv_branch"] = branches[0]
        if "inv_supplier" not in st.session_state:
            st.session_state["inv_supplier"] = suppliers["supplier_name"].astype(str).tolist()[0]
        if "inv_date" not in st.session_state:
            st.session_state["inv_date"] = datetime.now().date()
        if "inv_full" not in st.session_state:
            st.session_state["inv_full"] = 0.0
        if "inv_paid" not in st.session_state:
            st.session_state["inv_paid"] = 0.0

        if st.session_state.get("reset_invoice_inputs", False):
            st.session_state["inv_full"] = 0.0
            st.session_state["inv_paid"] = 0.0
            st.session_state["reset_invoice_inputs"] = False

        branch = st.selectbox("Branch", branches, key="inv_branch")
        supplier = st.selectbox("Supplier", suppliers["supplier_name"].astype(str).tolist(), key="inv_supplier")
        invoice_date = st.date_input("Invoice Date", key="inv_date")

        full_amount = st.number_input("Full Invoice Amount", min_value=0.0, step=0.01, key="inv_full")
        clamp_session_number("inv_paid", 0.0, float(full_amount))
        paid_amount = st.number_input(
            "Amount Paid", min_value=0.0, max_value=float(full_amount), step=0.01, key="inv_paid"
        )

        remaining = max(0.0, float(full_amount - paid_amount))

        st.markdown(
            f"<div class='brand-card'><b>Remaining to Transfer:</b> {remaining:,.2f}</div>",
            unsafe_allow_html=True,
        )

        if st.button("Submit Invoice"):
            if full_amount <= 0:
                st.error("Full amount must be greater than zero.")
            elif paid_amount > full_amount:
                st.error("Paid amount cannot exceed full amount.")
            else:
                st.session_state["pending_invoice"] = {
                    "date": invoice_date.strftime("%Y-%m-%d"),
                    "branch": branch,
                    "supplier": supplier,
                    "full_amount": float(full_amount),
                    "paid_amount": float(paid_amount),
                    "remaining": float(remaining),
                }
                st.session_state["confirm_invoice_open"] = True

        if st.session_state.get("confirm_invoice_open", False) and st.session_state.get("pending_invoice"):
            p = st.session_state["pending_invoice"]
            with st.container(border=True):
                st.subheader("Confirm Invoice Submission")
                st.markdown(
                    f"""
                    <div class="brand-card">
                        <b>Date:</b> {p["date"]}<br>
                        <b>Branch:</b> {p["branch"]}<br>
                        <b>Supplier:</b> {p["supplier"]}<br>
                        <b>Full Amount:</b> {p["full_amount"]:,.2f}<br>
                        <b>Paid Amount:</b> {p["paid_amount"]:,.2f}<br>
                        <b>Remaining:</b> {p["remaining"]:,.2f}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                c_yes, c_no = st.columns(2)

                with c_yes:
                    if st.button("✅ Yes, Submit Now"):
                        # reload latest to reduce overwrites in multi-user use
                        suppliers, invoices = load_all()

                        new_invoice = pd.DataFrame(
                            {
                                "date": [p["date"]],
                                "branch": [p["branch"]],
                                "supplier": [p["supplier"]],
                                "full_amount": [p["full_amount"]],
                                "paid_amount": [p["paid_amount"]],
                                "remaining": [p["remaining"]],
                                "status": ["Unpaid" if p["remaining"] > 0 else "Paid"],
                                "auto_unique_id": [next_invoice_id(invoices)],
                            }
                        )

                        invoices = pd.concat([invoices, new_invoice], ignore_index=True)
                        invoices["date"] = _normalize_dates(invoices["date"])

                        suppliers, invoices = recalculate_dues_and_save(suppliers, invoices)

                        st.session_state["confirm_invoice_open"] = False
                        st.session_state["pending_invoice"] = None
                        st.session_state["reset_invoice_inputs"] = True

                        st.success("Invoice added successfully. Total due updated.")
                        st.rerun()

                with c_no:
                    if st.button("❌ No, Cancel"):
                        st.session_state["confirm_invoice_open"] = False
                        st.session_state["pending_invoice"] = None
                        st.rerun()

# =========================================================
# VIEW DUES
# =========================================================
elif page == "View Dues":
    st.header("Supplier Dues")

    if suppliers.empty:
        st.info("No suppliers yet.")
    else:
        total_dues_sum = float(pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0).sum())
        st.markdown(
            f"<div class='brand-card'><b>Total Due (All Suppliers):</b> {total_dues_sum:,.2f}</div>",
            unsafe_allow_html=True,
        )

        gb = GridOptionsBuilder.from_dataframe(suppliers)
        gb.configure_default_column(editable=False, filterable=True, sortable=True)
        gb.configure_column("supplier_name", filter="agTextColumnFilter")
        gb.configure_column("total_due", type=["numericColumn"], valueFormatter="data.total_due.toFixed(2)")
        grid_options = gb.build()

        AgGrid(suppliers, grid_options, height=420, fit_columns_on_grid_load=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            suppliers.to_excel(writer, index=False, sheet_name="Dues")
        buffer.seek(0)

        st.download_button(
            "Download Dues as Excel",
            data=buffer,
            file_name="supplier_dues.xlsx",
            mime="application/vnd.ms-excel",
        )

# =========================================================
# VIEW INVOICES
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
                if (params.data.status === 'Unpaid') return {{ 'backgroundColor': '{BRAND_RED}' }};
                return null;
            }}
            """
        )

        gb = GridOptionsBuilder.from_dataframe(invoices)
        gb.configure_default_column(editable=False, filterable=True, sortable=True)

        gb.configure_column("auto_unique_id", hide=True)
        gb.configure_column("date", header_name="Invoice Date", filter="agTextColumnFilter", sortable=True)
        gb.configure_column("branch", filter="agTextColumnFilter")
        gb.configure_column("supplier", filter="agTextColumnFilter")
        gb.configure_column("status", filter="agTextColumnFilter")
        gb.configure_column("full_amount", valueFormatter="data.full_amount.toFixed(2)")
        gb.configure_column("paid_amount", valueFormatter="data.paid_amount.toFixed(2)")
        gb.configure_column("remaining", valueFormatter="data.remaining.toFixed(2)")
        gb.configure_selection("multiple", use_checkbox=True)

        grid_options = gb.build()
        grid_options["getRowStyle"] = row_style

        grid_response = AgGrid(
            invoices,
            gridOptions=grid_options,
            height=520,
            fit_columns_on_grid_load=True,
            update_mode="model_changed",
            data_return_mode="FILTERED",
            allow_unsafe_jscode=True,
        )

        filtered_df = get_filtered_rows(grid_response, invoices)
        filtered_remaining_sum = float(pd.to_numeric(filtered_df.get("remaining", 0), errors="coerce").fillna(0).sum())
        st.markdown(
            f"<div class='brand-card'><b>Filtered Total Remaining:</b> {filtered_remaining_sum:,.2f}</div>",
            unsafe_allow_html=True,
        )

        # Download invoices
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            invoices.to_excel(writer, index=False, sheet_name="Invoices")
        buffer.seek(0)

        st.download_button(
            "Download Invoices as Excel",
            data=buffer,
            file_name="invoices.xlsx",
            mime="application/vnd.ms-excel",
        )