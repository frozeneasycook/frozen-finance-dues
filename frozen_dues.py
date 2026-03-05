import streamlit as st
import pandas as pd
import os
from datetime import datetime, date
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import io
import requests

# =========================================================
# CONFIG
# =========================================================
LOGO_PATH = "logo.png"

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
BRAND_GREEN = "#c8f7c5"  # Paid rows
BRAND_RED = "#ffd6d6"    # Unpaid rows

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

    # Keep ONLY expected columns (ignore extras from Sheets)
    for c in expected_cols:
        if c not in df.columns:
            df[c] = ""
    df = df[expected_cols]

    return df


def api_write(sheet_name: str, df: pd.DataFrame, expected_cols: list[str]):
    url, token = _get_api_conf()

    df = df.copy()
    for c in expected_cols:
        if c not in df.columns:
            df[c] = ""
    df = df[expected_cols]
    df = df.where(pd.notnull(df), "")

    r = requests.post(
        url,
        json={"action": "write", "sheet": sheet_name, "token": token, "rows": df.to_dict("records")},
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
# DATE NORMALIZATION (NO .dt USED ANYWHERE)
# Handles:
# - "2026-03-03"
# - "1/13/2026"
# - serial numbers like 46035
# - blanks
# Always returns "YYYY-MM-DD" or ""
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

    # pandas NaN/NaT
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass

    # already date/datetime
    if isinstance(x, (datetime, date, pd.Timestamp)):
        try:
            return pd.Timestamp(x).strftime("%Y-%m-%d")
        except Exception:
            return ""

    # numeric serial
    try:
        num = float(x)
        if 20000 < num < 80000:
            d = _excel_serial_to_date(num)
            if d:
                return pd.Timestamp(d).strftime("%Y-%m-%d")
    except Exception:
        pass

    # string parse
    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nan", "nat"):
        return ""

    # if already yyyy-mm-dd
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
    if series is None:
        return pd.Series([], dtype=str)
    if len(series) == 0:
        return pd.Series([], dtype=str)
    return series.apply(_to_yyyy_mm_dd).astype(str)


# =========================================================
# LOAD / SAVE
# =========================================================
def load_all():
    suppliers = api_read(SUPPLIERS_SHEET, SUPPLIERS_COLS)
    invoices = api_read(INVOICES_SHEET, INVOICES_COLS)

    suppliers["total_due"] = pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0.0)

    for c in ["full_amount", "paid_amount", "remaining"]:
        invoices[c] = pd.to_numeric(invoices[c], errors="coerce").fillna(0.0)

    invoices["auto_unique_id"] = pd.to_numeric(invoices["auto_unique_id"], errors="coerce").fillna(-1).astype(int)

    invoices["date"] = _normalize_dates(invoices["date"])
    invoices["status"] = invoices["status"].replace("", pd.NA).fillna("Unpaid")
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

    inv_supps = set(invoices["supplier"].dropna().astype(str).tolist())
    sup_supps = set(suppliers["supplier_name"].dropna().astype(str).tolist())
    missing = sorted(list(inv_supps - sup_supps))
    if missing:
        suppliers = pd.concat(
            [suppliers, pd.DataFrame({"supplier_name": missing, "total_due": [0.0] * len(missing)})],
            ignore_index=True,
        )

    suppliers["total_due"] = 0.0
    unpaid = invoices[invoices["status"] == "Unpaid"].groupby("supplier")["remaining"].sum()

    suppliers = suppliers.set_index("supplier_name")
    suppliers["total_due"] = suppliers["total_due"].add(unpaid, fill_value=0).clip(lower=0)
    suppliers.reset_index(inplace=True)

    save_invoices(invoices)
    save_suppliers(suppliers)
    return suppliers, invoices


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
# PAGES
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
            st.success(f"Supplier '{new_supplier}' added.")
            st.rerun()


elif page == "Add Invoice":
    st.header("Add New Invoice")

    if suppliers.empty:
        st.warning("No suppliers added yet. Please add a supplier first.")
    else:
        branch = st.selectbox("Branch", branches)
        supplier = st.selectbox("Supplier", suppliers["supplier_name"].astype(str).tolist())
        inv_date = st.date_input("Invoice Date", datetime.now().date())

        full_amount = st.number_input("Full Invoice Amount", min_value=0.0, step=0.01)
        paid_amount = st.number_input("Amount Paid", min_value=0.0, max_value=float(full_amount), step=0.01)

        remaining = max(0.0, float(full_amount - paid_amount))
        st.markdown(f"<div class='brand-card'><b>Remaining:</b> {remaining:,.2f}</div>", unsafe_allow_html=True)

        if st.button("Submit Invoice"):
            suppliers, invoices = load_all()

            new_row = {
                "date": inv_date.strftime("%Y-%m-%d"),
                "branch": branch,
                "supplier": supplier,
                "full_amount": float(full_amount),
                "paid_amount": float(paid_amount),
                "remaining": float(remaining),
                "status": "Unpaid" if remaining > 0 else "Paid",
                "auto_unique_id": next_invoice_id(invoices),
            }

            invoices = pd.concat([invoices, pd.DataFrame([new_row])], ignore_index=True)
            invoices["date"] = _normalize_dates(invoices["date"])

            suppliers, invoices = recalculate_dues_and_save(suppliers, invoices)

            st.success("Invoice added.")
            st.rerun()


elif page == "View Dues":
    st.header("Supplier Dues")

    total_dues_sum = float(pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0).sum())
    st.markdown(f"<div class='brand-card'><b>Total Due:</b> {total_dues_sum:,.2f}</div>", unsafe_allow_html=True)

    gb = GridOptionsBuilder.from_dataframe(suppliers)
    gb.configure_default_column(editable=False, filterable=True, sortable=True)
    grid_options = gb.build()
    AgGrid(suppliers, grid_options, height=420, fit_columns_on_grid_load=True)


elif page == "View Invoices":
    st.header("All Invoices")

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