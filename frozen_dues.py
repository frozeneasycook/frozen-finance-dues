import streamlit as st
import pandas as pd
import os
from datetime import datetime
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import io

# ---------------- FILES ----------------
SUPPLIERS_FILE = "suppliers.csv"
INVOICES_FILE = "invoices.csv"
LOGO_PATH = "logo.png"  # <-- your attached logo

# ---------------- BRAND COLORS (from your logo) ----------------
BRAND_DARK_BLUE = "#001f3f"
BRAND_NAVY = "#1D2B7A"
BRAND_LIGHT_BLUE = "#ADD8E6"
BRAND_YELLOW = "#FFDC00"
BRAND_WHITE = "#FFFFFF"
BRAND_GREEN = "#c8f7c5"   # Paid row background
BRAND_RED = "#ffd6d6"     # Unpaid row background

# ---------------- LOAD DATA ----------------
if os.path.exists(SUPPLIERS_FILE):
    suppliers = pd.read_csv(SUPPLIERS_FILE)
else:
    suppliers = pd.DataFrame(columns=["supplier_name", "total_due"])
    suppliers.to_csv(SUPPLIERS_FILE, index=False)

if os.path.exists(INVOICES_FILE):
    invoices = pd.read_csv(INVOICES_FILE)
else:
    invoices = pd.DataFrame(columns=[
        "date", "branch", "supplier", "full_amount", "paid_amount", "remaining", "status", "auto_unique_id"
    ])
    invoices.to_csv(INVOICES_FILE, index=False)

# Ensure required columns exist
if "status" not in invoices.columns:
    invoices["status"] = "Unpaid"

if "auto_unique_id" not in invoices.columns:
    invoices["auto_unique_id"] = range(len(invoices))

# Numeric safety
for c in ["full_amount", "paid_amount", "remaining"]:
    if c in invoices.columns:
        invoices[c] = pd.to_numeric(invoices[c], errors="coerce").fillna(0.0)

# Normalize / clean date format (keeps all invoices as YYYY-MM-DD)
if "date" in invoices.columns:
    invoices["date"] = pd.to_datetime(invoices["date"], errors="coerce").dt.strftime("%Y-%m-%d")

# ---------------- BUSINESS LOGIC ----------------
def recalculate_dues():
    """Recalculate supplier total due from invoices (Unpaid remaining only)."""
    global suppliers, invoices

    suppliers = suppliers.copy()
    invoices = invoices.copy()

    invoices["remaining"] = pd.to_numeric(invoices["remaining"], errors="coerce").fillna(0.0)

    suppliers["total_due"] = 0.0
    unpaid = invoices[invoices["status"] == "Unpaid"].groupby("supplier")["remaining"].sum()

    suppliers = suppliers.set_index("supplier_name")
    suppliers["total_due"] = suppliers["total_due"].add(unpaid, fill_value=0)
    suppliers["total_due"] = suppliers["total_due"].clip(lower=0)
    suppliers.reset_index(inplace=True)

    suppliers.to_csv(SUPPLIERS_FILE, index=False)


def save_invoices():
    global invoices
    invoices.to_csv(INVOICES_FILE, index=False)


def next_invoice_id():
    if invoices.empty:
        return 0
    return int(pd.to_numeric(invoices["auto_unique_id"], errors="coerce").fillna(-1).max()) + 1


def normalize_selected_rows(grid_response):
    """
    st_aggrid sometimes returns selected_rows as:
    - None
    - list[dict]
    - pandas.DataFrame
    This normalizes to list[dict].
    """
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
    """
    Get the currently visible/filtered rows from AgGrid response.
    This is what we use to compute sums that change with filters.
    """
    if not grid_response:
        return fallback_df

    data = grid_response.get("data", None)
    if data is None:
        return fallback_df

    try:
        df = pd.DataFrame(data)
        return df
    except Exception:
        return fallback_df


def clamp_session_number(key: str, min_v: float, max_v: float):
    """Clamp a session_state numeric value to [min_v, max_v]."""
    if key in st.session_state:
        try:
            v = float(st.session_state[key])
            st.session_state[key] = max(min_v, min(max_v, v))
        except Exception:
            st.session_state[key] = min_v


# ---------------- UI CONFIG ----------------
st.set_page_config(
    page_title="Frozen Products Invoice Management",
    layout="wide"
)

# Branding CSS
st.markdown(f"""
<style>
    [data-testid="stAppViewContainer"] {{
        background-color: {BRAND_LIGHT_BLUE};
    }}

    [data-testid="stSidebar"] {{
        background-color: {BRAND_DARK_BLUE};
    }}

    /* Sidebar text */
    [data-testid="stSidebar"] * {{
        color: {BRAND_WHITE} !important;
    }}

    /* Titles */
    h1, h2, h3, h4, h5, h6 {{
        color: {BRAND_DARK_BLUE};
        font-weight: 800;
    }}

    /* Buttons */
    .stButton > button {{
        background-color: {BRAND_YELLOW};
        color: {BRAND_DARK_BLUE};
        border: 0;
        border-radius: 10px;
        font-weight: 800;
        padding: 0.55rem 1rem;
    }}
    .stButton > button:hover {{
        filter: brightness(0.95);
        transform: translateY(-1px);
    }}

    /* Inputs a bit nicer */
    .stTextInput input, .stNumberInput input {{
        border-radius: 10px !important;
    }}

    /* Metric-like selection bar */
    .brand-card {{
        background: rgba(255,255,255,0.55);
        border: 1px solid rgba(0,0,0,0.06);
        border-radius: 16px;
        padding: 14px 16px;
        margin: 8px 0 14px 0;
    }}
</style>
""", unsafe_allow_html=True)

# Header
if os.path.exists(LOGO_PATH):
    st.image(LOGO_PATH, width=260)

st.title("Frozen Products Invoice Management")

# ---------------- NAV ----------------
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Add Supplier", "Add Invoice", "View Dues", "View Invoices", "Reset"])

branches = ["Frozen Obour", "Frozen Shrouq", "Frozen Mostakbal", "Frozen Zayed", "Frozen Heliopolis", "Frozen Maadi"]

# ---------------- ADD SUPPLIER ----------------
if page == "Add Supplier":
    st.header("Add New Supplier")
    new_supplier = st.text_input("Supplier Name")

    if st.button("Add Supplier"):
        if new_supplier.strip() == "":
            st.error("Supplier name cannot be empty.")
        elif new_supplier in suppliers["supplier_name"].values:
            st.error("Supplier already exists.")
        else:
            new_row = pd.DataFrame({"supplier_name": [new_supplier], "total_due": [0.0]})
            suppliers = pd.concat([suppliers, new_row], ignore_index=True)
            suppliers.to_csv(SUPPLIERS_FILE, index=False)
            st.success(f"Supplier '{new_supplier}' added successfully.")
            st.rerun()

# ---------------- ADD INVOICE (WITH CONFIRM + NO RESET) ----------------
elif page == "Add Invoice":
    st.header("Add New Invoice")

    if suppliers.empty:
        st.warning("No suppliers added yet. Please add a supplier first.")
    else:
        # ---- session state defaults ----
        if "confirm_invoice_open" not in st.session_state:
            st.session_state["confirm_invoice_open"] = False
        if "pending_invoice" not in st.session_state:
            st.session_state["pending_invoice"] = None

        # reset flag (must be handled BEFORE widgets)
        if "reset_invoice_inputs" not in st.session_state:
            st.session_state["reset_invoice_inputs"] = False

        # Set defaults once so they persist across reruns
        if "inv_branch" not in st.session_state:
            st.session_state["inv_branch"] = branches[0]
        if "inv_supplier" not in st.session_state:
            st.session_state["inv_supplier"] = suppliers["supplier_name"].tolist()[0]
        if "inv_date" not in st.session_state:
            st.session_state["inv_date"] = datetime.now().date()
        if "inv_full" not in st.session_state:
            st.session_state["inv_full"] = 0.0
        if "inv_paid" not in st.session_state:
            st.session_state["inv_paid"] = 0.0

        # ✅ Apply reset BEFORE widget creation
        if st.session_state.get("reset_invoice_inputs", False):
            st.session_state["inv_full"] = 0.0
            st.session_state["inv_paid"] = 0.0
            st.session_state["reset_invoice_inputs"] = False

        # ---- widgets (with keys) ----
        branch = st.selectbox("Branch", branches, key="inv_branch")
        supplier = st.selectbox("Supplier", suppliers["supplier_name"].tolist(), key="inv_supplier")
        invoice_date = st.date_input("Invoice Date", key="inv_date")

        full_amount = st.number_input("Full Invoice Amount", min_value=0.0, step=0.01, key="inv_full")

        # Ensure paid doesn't exceed full (important when full changes)
        clamp_session_number("inv_paid", 0.0, float(full_amount))

        paid_amount = st.number_input(
            "Amount Paid",
            min_value=0.0,
            max_value=float(full_amount),
            step=0.01,
            key="inv_paid"
        )

        remaining = float(full_amount - paid_amount)

        st.markdown(f"""
        <div class="brand-card">
            <b>Remaining to Transfer:</b> {remaining:,.2f}
        </div>
        """, unsafe_allow_html=True)

        # First click -> open confirmation (do not save yet)
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

        # Confirmation box
        if st.session_state.get("confirm_invoice_open", False) and st.session_state.get("pending_invoice"):
            p = st.session_state["pending_invoice"]

            with st.container(border=True):
                st.subheader("Confirm Invoice Submission")
                st.markdown(f"""
                <div class="brand-card">
                    <b>Date:</b> {p["date"]}<br>
                    <b>Branch:</b> {p["branch"]}<br>
                    <b>Supplier:</b> {p["supplier"]}<br>
                    <b>Full Amount:</b> {p["full_amount"]:,.2f}<br>
                    <b>Paid Amount:</b> {p["paid_amount"]:,.2f}<br>
                    <b>Remaining:</b> {p["remaining"]:,.2f}
                </div>
                """, unsafe_allow_html=True)

                c_yes, c_no = st.columns(2)

                with c_yes:
                    if st.button("✅ Yes, Submit Now"):
                        new_invoice = pd.DataFrame({
                            "date": [p["date"]],
                            "branch": [p["branch"]],
                            "supplier": [p["supplier"]],
                            "full_amount": [p["full_amount"]],
                            "paid_amount": [p["paid_amount"]],
                            "remaining": [p["remaining"]],
                            "status": ["Unpaid" if p["remaining"] > 0 else "Paid"],
                            "auto_unique_id": [next_invoice_id()],
                        })

                        invoices = pd.concat([invoices, new_invoice], ignore_index=True)
                        invoices["date"] = pd.to_datetime(invoices["date"], errors="coerce").dt.strftime("%Y-%m-%d")

                        save_invoices()
                        recalculate_dues()

                        # Close confirmation + clear pending
                        st.session_state["confirm_invoice_open"] = False
                        st.session_state["pending_invoice"] = None

                        # ✅ Request reset next run (safe)
                        st.session_state["reset_invoice_inputs"] = True

                        st.success("Invoice added successfully. Total due updated.")
                        st.rerun()

                with c_no:
                    if st.button("❌ No, Cancel"):
                        st.session_state["confirm_invoice_open"] = False
                        st.session_state["pending_invoice"] = None
                        st.rerun()

# ---------------- VIEW DUES ----------------
elif page == "View Dues":
    st.header("Supplier Dues")

    if suppliers.empty:
        st.info("No suppliers yet.")
    else:
        total_dues_sum = float(pd.to_numeric(suppliers["total_due"], errors="coerce").fillna(0).sum())
        st.markdown(f"""
        <div class="brand-card">
            <b>Total Due (All Suppliers):</b> {total_dues_sum:,.2f}
        </div>
        """, unsafe_allow_html=True)

        gb = GridOptionsBuilder.from_dataframe(suppliers)
        gb.configure_default_column(editable=False, filterable=True, sortable=True)
        gb.configure_column("supplier_name", filter="agTextColumnFilter")
        gb.configure_column("total_due", type=["numericColumn"], valueFormatter="data.total_due.toFixed(2)")
        grid_options = gb.build()

        AgGrid(suppliers, grid_options, height=400, fit_columns_on_grid_load=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            suppliers.to_excel(writer, index=False, sheet_name="Dues")
        buffer.seek(0)

        st.download_button(
            label="Download Dues as Excel",
            data=buffer,
            file_name="supplier_dues.xlsx",
            mime="application/vnd.ms-excel"
        )

# ---------------- VIEW INVOICES ----------------
elif page == "View Invoices":
    st.header("All Invoices")

    if invoices.empty:
        st.info("No invoices yet.")
    else:
        row_style = JsCode(f"""
        function(params) {{
            if (!params || !params.data) return null;
            if (params.data.status === 'Paid') {{
                return {{ 'backgroundColor': '{BRAND_GREEN}' }};
            }}
            if (params.data.status === 'Unpaid') {{
                return {{ 'backgroundColor': '{BRAND_RED}' }};
            }}
            return null;
        }}
        """)

        gb = GridOptionsBuilder.from_dataframe(invoices)
        gb.configure_default_column(editable=False, filterable=True, sortable=True)

        gb.configure_column("auto_unique_id", hide=True)
        gb.configure_column("date", header_name="Invoice Date", filter="agDateColumnFilter", sortable=True)
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
            height=500,
            fit_columns_on_grid_load=True,
            update_mode="model_changed",
            data_return_mode="FILTERED",
            allow_unsafe_jscode=True
        )

        filtered_df = get_filtered_rows(grid_response, invoices)
        filtered_remaining_sum = float(
            pd.to_numeric(filtered_df.get("remaining", 0), errors="coerce").fillna(0).sum()
        )

        st.markdown(f"""
        <div class="brand-card">
            <b>Filtered Total Remaining:</b> {filtered_remaining_sum:,.2f}
        </div>
        """, unsafe_allow_html=True)

        selected_rows = normalize_selected_rows(grid_response)

        st.markdown(
            "<div class='brand-card'><b>Tip:</b> Select invoices using the checkbox to enable actions.</div>",
            unsafe_allow_html=True
        )

        if len(selected_rows) > 0:
            st.success(f"Selected {len(selected_rows)} invoice(s)")

            colA, colB, colC = st.columns(3)

            with colA:
                if st.button("Toggle Status"):
                    ids = [int(r["auto_unique_id"]) for r in selected_rows if "auto_unique_id" in r]
                    if ids:
                        mask = invoices["auto_unique_id"].isin(ids)
                        invoices.loc[mask, "status"] = invoices.loc[mask, "status"].apply(
                            lambda s: "Paid" if s == "Unpaid" else "Unpaid"
                        )

                        paid_mask = mask & (invoices["status"] == "Paid")
                        invoices.loc[paid_mask, "remaining"] = 0.0

                        save_invoices()
                        recalculate_dues()
                        st.rerun()

            with colB:
                if st.button("Add Partial Payment"):
                    st.session_state["partial_payment_open"] = True

            with colC:
                if st.button("Delete Selected"):
                    st.session_state["delete_confirm_open"] = True

        # ---------------- PARTIAL PAYMENT "POPUP" ----------------
        if st.session_state.get("partial_payment_open", False):
            with st.container(border=True):
                st.subheader("Partial Payment")
                st.write("This will ADD to paid amount, and reduce remaining.")

                ids = [int(r["auto_unique_id"]) for r in selected_rows if "auto_unique_id" in r]

                if not ids:
                    st.warning("No valid selection.")
                else:
                    if len(ids) == 1:
                        inv = invoices[invoices["auto_unique_id"] == ids[0]].iloc[0]
                        st.markdown(f"""
                        <div class="brand-card">
                            <b>Supplier:</b> {inv["supplier"]} &nbsp; | &nbsp;
                            <b>Branch:</b> {inv["branch"]} &nbsp; | &nbsp;
                            <b>Remaining:</b> {float(inv["remaining"]):.2f}
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        total_remaining = invoices[invoices["auto_unique_id"].isin(ids)]["remaining"].sum()
                        st.markdown(f"""
                        <div class="brand-card">
                            <b>{len(ids)} invoices selected</b> &nbsp; | &nbsp;
                            <b>Total Remaining:</b> {float(total_remaining):.2f}
                        </div>
                        """, unsafe_allow_html=True)

                    payment = st.number_input("Payment amount to apply", min_value=0.0, step=0.01)

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Apply Payment"):
                            if payment <= 0:
                                st.error("Payment must be greater than 0.")
                            else:
                                sel_df = invoices[invoices["auto_unique_id"].isin(ids)].copy()
                                sel_df["date_sort"] = pd.to_datetime(sel_df["date"], errors="coerce")
                                sel_df = sel_df.sort_values("date_sort").drop(columns=["date_sort"])

                                remaining_payment = float(payment)

                                for _, row in sel_df.iterrows():
                                    if remaining_payment <= 0:
                                        break

                                    inv_id = int(row["auto_unique_id"])
                                    current_remaining = float(row["remaining"])
                                    if current_remaining <= 0:
                                        invoices.loc[invoices["auto_unique_id"] == inv_id, "status"] = "Paid"
                                        invoices.loc[invoices["auto_unique_id"] == inv_id, "remaining"] = 0.0
                                        continue

                                    pay_here = min(current_remaining, remaining_payment)

                                    invoices.loc[invoices["auto_unique_id"] == inv_id, "paid_amount"] = \
                                        float(invoices.loc[invoices["auto_unique_id"] == inv_id, "paid_amount"].values[0]) + pay_here

                                    invoices.loc[invoices["auto_unique_id"] == inv_id, "remaining"] = \
                                        float(invoices.loc[invoices["auto_unique_id"] == inv_id, "remaining"].values[0]) - pay_here

                                    new_rem = float(invoices.loc[invoices["auto_unique_id"] == inv_id, "remaining"].values[0])
                                    if new_rem <= 0.00001:
                                        invoices.loc[invoices["auto_unique_id"] == inv_id, "remaining"] = 0.0
                                        invoices.loc[invoices["auto_unique_id"] == inv_id, "status"] = "Paid"
                                    else:
                                        invoices.loc[invoices["auto_unique_id"] == inv_id, "status"] = "Unpaid"

                                    remaining_payment -= pay_here

                                save_invoices()
                                recalculate_dues()
                                st.session_state["partial_payment_open"] = False
                                st.success("Payment applied successfully.")
                                st.rerun()

                    with c2:
                        if st.button("Cancel"):
                            st.session_state["partial_payment_open"] = False
                            st.rerun()

        # ---------------- DELETE CONFIRMATION "DIALOG" ----------------
        if st.session_state.get("delete_confirm_open", False):
            with st.container(border=True):
                st.subheader("Confirm Delete")
                st.warning("This action cannot be undone.")

                ids = [int(r["auto_unique_id"]) for r in selected_rows if "auto_unique_id" in r]
                st.write(f"Invoices to delete: {len(ids)}")

                d1, d2 = st.columns(2)
                with d1:
                    if st.button("✅ Yes, Delete Now"):
                        if ids:
                            invoices = invoices[~invoices["auto_unique_id"].isin(ids)].reset_index(drop=True)
                            save_invoices()
                            recalculate_dues()
                        st.session_state["delete_confirm_open"] = False
                        st.success("Deleted successfully.")
                        st.rerun()

                with d2:
                    if st.button("❌ Cancel"):
                        st.session_state["delete_confirm_open"] = False
                        st.rerun()

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            invoices.to_excel(writer, index=False, sheet_name="Invoices")
        buffer.seek(0)

        st.download_button(
            label="Download Invoices as Excel",
            data=buffer,
            file_name="invoices.xlsx",
            mime="application/vnd.ms-excel"
        )

# ---------------- RESET ----------------
elif page == "Reset":
    st.header("Reset")
    password = st.text_input("Password", type="password")

    if password == "c12q7mgh":
        if st.button("Reset All Data"):
            suppliers = pd.DataFrame(columns=["supplier_name", "total_due"])
            invoices = pd.DataFrame(columns=[
                "date", "branch", "supplier", "full_amount", "paid_amount", "remaining", "status", "auto_unique_id"
            ])
            suppliers.to_csv(SUPPLIERS_FILE, index=False)
            invoices.to_csv(INVOICES_FILE, index=False)
            st.success("All data reset successfully.")
            st.rerun()
    else:
        if password:
            st.error("Incorrect password.")
