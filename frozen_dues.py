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
RESET_PASSWORD = "c12q7mgh"

SUPPLIERS_SHEET = "suppliers"
INVOICES_SHEET = "invoices"

# NEW (extended) schema (works even if old sheets exist; app will auto-upgrade)
SUPPLIERS_COLS = ["supplier_name", "total_due", "credit_balance"]

INVOICES_COLS = [
    "date",               # invoice date (YYYY-MM-DD)
    "branch",
    "supplier",
    "invoice_amount",     # base invoice
    "delivery_cost",      # delivery cost
    "total_due",          # invoice_amount + delivery_cost
    "paid_total",         # paid_cash + paid_visa
    "paid_cash",
    "paid_visa",
    "payment_date",       # last payment date
    "payment_note",       # e.g. "Partial - Cash", "Visa", etc.
    "remaining",          # max(total_due - paid_total, 0)
    "credit",             # max(paid_total - total_due, 0)
    "status",             # Unpaid / Partial / Paid / Credit
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
BRAND_RED = "#ffd6d6"    # Unpaid/Partial rows
BRAND_AMBER = "#fff2cc"  # Credit rows

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
    r = requests.get(
        url,
        params={"action": "read", "sheet": sheet_name, "token": token},
        timeout=30,
    )
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
# DATE NORMALIZATION (NO .dt used)
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

    # serial number
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
# Supports old sheet headers:
# invoices: date,branch,supplier,full_amount,paid_amount,remaining,status,auto_unique_id
# suppliers: supplier_name,total_due
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
        df = pd.DataFrame(columns=INVOICES_COLS)
        return df

    # Old -> new mapping if old columns exist
    if "full_amount" in df.columns and "invoice_amount" not in df.columns:
        df["invoice_amount"] = df["full_amount"]
    if "paid_amount" in df.columns and "paid_total" not in df.columns:
        df["paid_total"] = df["paid_amount"]

    # Fill defaults for new columns
    df = ensure_columns(df, [
        "date","branch","supplier","invoice_amount","delivery_cost","total_due",
        "paid_total","paid_cash","paid_visa","payment_date","payment_note",
        "remaining","credit","status","auto_unique_id"
    ])

    # Types
    df["date"] = _normalize_dates(df["date"])
    df["payment_date"] = _normalize_dates(df["payment_date"])

    for c in ["invoice_amount", "delivery_cost", "total_due", "paid_total", "paid_cash", "paid_visa", "remaining", "credit"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["auto_unique_id"] = pd.to_numeric(df["auto_unique_id"], errors="coerce").fillna(-1).astype(int)

    # Compute totals safely
    df["total_due"] = (df["invoice_amount"] + df["delivery_cost"]).round(2)

    # If paid_cash/paid_visa are empty but paid_total exists, keep paid_total.
    # If paid_cash+paid_visa > 0, recompute paid_total from them.
    has_split = (df["paid_cash"] + df["paid_visa"]) > 0
    df.loc[has_split, "paid_total"] = (df.loc[has_split, "paid_cash"] + df.loc[has_split, "paid_visa"]).round(2)

    # Remaining / credit
    diff = (df["total_due"] - df["paid_total"]).round(2)
    df["remaining"] = diff.clip(lower=0).round(2)
    df["credit"] = (-diff).clip(lower=0).round(2)

    # Status
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
        df = pd.DataFrame(columns=SUPPLIERS_COLS)
        return df

    df = ensure_columns(df, ["supplier_name", "total_due", "credit_balance"])
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
            [suppliers, pd.DataFrame({"supplier_name": missing, "total_due": [0.0]*len(missing), "credit_balance":[0.0]*len(missing)})],
            ignore_index=True,
        )

    due_by_supplier = invoices.groupby("supplier")["remaining"].sum()
    credit_by_supplier = invoices.groupby("supplier")["credit"].sum()

    suppliers = suppliers.set_index("supplier_name")
    suppliers["total_due"] = suppliers.index.map(lambda s: float(due_by_supplier.get(s, 0.0))).astype(float)
    suppliers["credit_balance"] = suppliers.index.map(lambda s: float(credit_by_supplier.get(s, 0.0))).astype(float)
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
page = st.sidebar.radio(
    "Go to",
    ["Add Supplier", "Add Invoice", "View Dues", "View Invoices", "Reset (Admin)"],
)

try:
    suppliers, invoices = load_all()
except Exception as e:
    st.error(f"Failed to load data from Google Sheet API: {e}")
    st.stop()

# =========================================================
# Add Supplier
# =========================================================
if page == "Add Supplier":
    st.header("Add New Supplier")
    new_supplier = st.text_input("Supplier Name")

    if st.button("Add Supplier"):
        name = (new_supplier or "").strip()
        if name == "":
            st.error("Supplier name cannot be empty.")
        elif name in suppliers["supplier_name"].astype(str).values:
            st.error("Supplier already exists.")
        else:
            suppliers = pd.concat(
                [suppliers, pd.DataFrame({"supplier_name": [name], "total_due": [0.0], "credit_balance":[0.0]})],
                ignore_index=True,
            )
            save_all(suppliers, invoices)
            st.success(f"Supplier '{name}' added.")
            st.rerun()

# =========================================================
# Add Invoice
# =========================================================
elif page == "Add Invoice":
    st.header("Add New Invoice")

    if suppliers.empty:
        st.warning("No suppliers yet. Add supplier first.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            branch = st.selectbox("Branch", branches)
        with c2:
            supplier = st.selectbox("Supplier", suppliers["supplier_name"].astype(str).tolist())
        with c3:
            invoice_date = st.date_input("Invoice Date", datetime.now().date())

        st.subheader("Invoice Amounts")
        a1, a2 = st.columns(2)
        with a1:
            invoice_amount = st.number_input("Invoice Amount (without delivery)", min_value=0.0, step=0.01, value=0.0)
        with a2:
            delivery_cost = st.number_input("Delivery Cost (0 if none)", min_value=0.0, step=0.01, value=0.0)

        total_due = float(invoice_amount + delivery_cost)

        st.subheader("Payment Details (can be 0, partial, full, or overpaid)")
        p1, p2, p3 = st.columns(3)
        with p1:
            payment_date = st.date_input("Payment Date", datetime.now().date())
        with p2:
            paid_cash = st.number_input("Paid Cash", min_value=0.0, step=0.01, value=0.0)
        with p3:
            paid_visa = st.number_input("Paid Visa", min_value=0.0, step=0.01, value=0.0)

        paid_total = float(paid_cash + paid_visa)

        # Remaining / credit
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

        method_note_parts = []
        if paid_cash > 0:
            method_note_parts.append("Cash")
        if paid_visa > 0:
            method_note_parts.append("Visa")
        method_note = " + ".join(method_note_parts) if method_note_parts else ""

        note = st.text_input("Payment Note (optional)", value=method_note)

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

        if st.button("Submit Invoice"):
            # reload latest to reduce overwrites
            suppliers, invoices = load_all()

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
                "payment_note": (note or "").strip(),
                "remaining": float(remaining),
                "credit": float(credit),
                "status": status,
                "auto_unique_id": next_invoice_id(invoices),
            }

            invoices = pd.concat([invoices, pd.DataFrame([new_row])], ignore_index=True)
            invoices = migrate_invoices(invoices)  # normalize

            save_all(suppliers, invoices)
            st.success("Invoice added.")
            st.rerun()

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
    gb.configure_column("total_due", type=["numericColumn"], valueFormatter="data.total_due.toFixed(2)")
    gb.configure_column("credit_balance", type=["numericColumn"], valueFormatter="data.credit_balance.toFixed(2)")
    grid_options = gb.build()

    AgGrid(suppliers, grid_options, height=450, fit_columns_on_grid_load=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        suppliers.to_excel(writer, index=False, sheet_name="Dues")
    buf.seek(0)

    st.download_button(
        "Download Dues as Excel",
        data=buf,
        file_name="supplier_dues.xlsx",
        mime="application/vnd.ms-excel",
    )

# =========================================================
# View Invoices + Apply Payment
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

        gb = GridOptionsBuilder.from_dataframe(invoices)

        # Default: no filters
        gb.configure_default_column(editable=False, filterable=False, sortable=True)

        # Filters ONLY on: date, branch, supplier, status
        gb.configure_column("date", header_name="Invoice Date", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("branch", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("supplier", filter="agTextColumnFilter", filterable=True)
        gb.configure_column("status", filter="agTextColumnFilter", filterable=True)

        # Hide technical id
        gb.configure_column("auto_unique_id", hide=True)

        # Numeric formatting
        for c in ["invoice_amount", "delivery_cost", "total_due", "paid_total", "paid_cash", "paid_visa", "remaining", "credit"]:
            gb.configure_column(c, type=["numericColumn"], valueFormatter=f"Number(data.{c}).toFixed(2)")

        gb.configure_selection("single", use_checkbox=True)  # select ONE invoice to apply payment
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

        # Filtered total remaining
        filtered_df = pd.DataFrame(grid_response.get("data", invoices.to_dict("records")))
        filtered_remaining_sum = float(pd.to_numeric(filtered_df.get("remaining", 0), errors="coerce").fillna(0).sum())
        st.markdown(
            f"<div class='brand-card'><b>Filtered Total Remaining:</b> {filtered_remaining_sum:,.2f}</div>",
            unsafe_allow_html=True,
        )

        # Apply payment to selected invoice
        selected_rows = grid_response.get("selected_rows", []) or []
        if isinstance(selected_rows, pd.DataFrame):
            selected_rows = selected_rows.to_dict("records")

        st.subheader("Update / Add Payment (select ONE invoice above)")
        if not selected_rows:
            st.info("Select one invoice row (checkbox) to apply a payment.")
        else:
            sel = selected_rows[0]
            inv_id = int(sel.get("auto_unique_id", -1))

            st.markdown(
                f"<div class='brand-card'><b>Selected Invoice:</b> {sel.get('supplier','')} | {sel.get('branch','')} | ID: {inv_id}</div>",
                unsafe_allow_html=True,
            )

            p1, p2, p3 = st.columns(3)
            with p1:
                pay_date2 = st.date_input("Payment Date (update)", datetime.now().date(), key="pay_date_update")
            with p2:
                add_cash = st.number_input("Add Cash Payment", min_value=0.0, step=0.01, value=0.0, key="add_cash_update")
            with p3:
                add_visa = st.number_input("Add Visa Payment", min_value=0.0, step=0.01, value=0.0, key="add_visa_update")

            note2 = st.text_input("Payment Note (e.g. Partial - Cash/Visa)", value="", key="pay_note_update")

            if st.button("Apply Payment to Selected Invoice"):
                # reload latest
                suppliers, invoices = load_all()

                mask = invoices["auto_unique_id"].astype(int) == inv_id
                if not mask.any():
                    st.error("Selected invoice not found (data refreshed). Re-select the invoice.")
                    st.stop()

                i = invoices[mask].index[0]

                # Update paid splits
                invoices.at[i, "paid_cash"] = float(pd.to_numeric(invoices.at[i, "paid_cash"], errors="coerce") or 0) + float(add_cash)
                invoices.at[i, "paid_visa"] = float(pd.to_numeric(invoices.at[i, "paid_visa"], errors="coerce") or 0) + float(add_visa)

                invoices.at[i, "paid_total"] = float(invoices.at[i, "paid_cash"]) + float(invoices.at[i, "paid_visa"])
                invoices.at[i, "payment_date"] = pay_date2.strftime("%Y-%m-%d") if (float(add_cash) + float(add_visa)) > 0 else invoices.at[i, "payment_date"]

                if (note2 or "").strip():
                    invoices.at[i, "payment_note"] = (note2 or "").strip()
                else:
                    # auto note if not provided
                    parts = []
                    if float(add_cash) > 0:
                        parts.append("Cash")
                    if float(add_visa) > 0:
                        parts.append("Visa")
                    if parts:
                        invoices.at[i, "payment_note"] = " + ".join(parts)

                # Recompute totals and status
                invoices.at[i, "date"] = _to_yyyy_mm_dd(invoices.at[i, "date"])
                invoices.at[i, "payment_date"] = _to_yyyy_mm_dd(invoices.at[i, "payment_date"])

                for c in ["invoice_amount", "delivery_cost"]:
                    invoices.at[i, c] = float(pd.to_numeric(invoices.at[i, c], errors="coerce") or 0.0)

                invoices.at[i, "total_due"] = float(invoices.at[i, "invoice_amount"]) + float(invoices.at[i, "delivery_cost"])

                diff = round(float(invoices.at[i, "total_due"]) - float(invoices.at[i, "paid_total"]), 2)
                invoices.at[i, "remaining"] = round(max(diff, 0.0), 2)
                invoices.at[i, "credit"] = round(max(-diff, 0.0), 2)

                if float(invoices.at[i, "credit"]) > 0:
                    invoices.at[i, "status"] = "Credit"
                elif float(invoices.at[i, "remaining"]) == 0 and float(invoices.at[i, "paid_total"]) > 0:
                    invoices.at[i, "status"] = "Paid"
                elif float(invoices.at[i, "paid_total"]) > 0 and float(invoices.at[i, "remaining"]) > 0:
                    invoices.at[i, "status"] = "Partial"
                else:
                    invoices.at[i, "status"] = "Unpaid"

                invoices = migrate_invoices(invoices)
                save_all(suppliers, invoices)
                st.success("Payment applied.")
                st.rerun()

        # Download
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            invoices.to_excel(writer, index=False, sheet_name="Invoices")
        buf.seek(0)
        st.download_button(
            "Download Invoices as Excel",
            data=buf,
            file_name="invoices.xlsx",
            mime="application/vnd.ms-excel",
        )

# =========================================================
# Reset (Admin)
# =========================================================
elif page == "Reset (Admin)":
    st.header("Reset Data (Admin)")

    st.warning("This will DELETE all rows in suppliers and invoices (keeps headers). This action cannot be undone.")

    pw = st.text_input("Enter password to enable reset", type="password")
    confirm = st.checkbox("I understand this will delete all data")

    if st.button("RESET NOW"):
        if pw != RESET_PASSWORD:
            st.error("Wrong password.")
            st.stop()
        if not confirm:
            st.error("You must confirm the checkbox.")
            st.stop()

        # Clear data by writing empty tables (Apps Script clears everything below header)
        empty_sup = pd.DataFrame(columns=SUPPLIERS_COLS)
        empty_inv = pd.DataFrame(columns=INVOICES_COLS)

        api_write(SUPPLIERS_SHEET, empty_sup)
        api_write(INVOICES_SHEET, empty_inv)

        st.success("Reset completed. Reloading...")
        st.rerun()