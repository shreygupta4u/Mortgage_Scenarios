"""
Canadian Mortgage Analyzer v9 — app.py
REQ #8: DB type selector (MS SQL Server or SQLite local file)
Run: streamlit run app.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(page_title="🏠 Canadian Mortgage Analyzer", page_icon="🏠",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
.main-header{font-size:2.1rem;font-weight:700;color:#1a3c5e;margin-bottom:.1rem;}
.sub-header{font-size:.9rem;color:#555;margin-bottom:1.2rem;}
.mc{background:#e8f0fa;border-radius:9px;padding:.65rem 1rem;
    border-left:4px solid #1a3c5e;margin-bottom:.35rem;}
.mc h3{margin:0;font-size:.72rem;color:#444!important;text-transform:uppercase;letter-spacing:.05em;}
.mc p{margin:0;font-size:1.2rem;font-weight:700;color:#1a3c5e!important;}
.mc small{font-size:.75rem;color:#555;}
.mc-g{border-left:4px solid #1e8449!important;background:#d5f0e0!important;}
.mc-g h3{color:#145a32!important;}.mc-g p{color:#1e8449!important;}
.mc-r{border-left:4px solid #c0392b!important;background:#fae5e5!important;}
.mc-r h3{color:#922b21!important;}.mc-r p{color:#c0392b!important;}
.mc-b{border-left:4px solid #1a6ca8!important;background:#d6eaf8!important;}
.mc-b h3{color:#1a5276!important;}.mc-b p{color:#1a6ca8!important;}
.warn{background:#fef9e7!important;border:1px solid #d4ac0d;border-radius:7px;
      padding:.6rem .9rem;margin:3px 0;color:#7d6608!important;}
.warn b,.warn i,.warn small{color:#7d6608!important;}
.ok{background:#eafaf1!important;border:1px solid #1e8449;border-radius:7px;
    padding:.6rem .9rem;margin:3px 0;color:#186a3b!important;}
.ok b,.ok i,.ok small{color:#186a3b!important;}
.inf{background:#d6eaf8!important;border:1px solid #1a6ca8;border-radius:7px;
     padding:.6rem .9rem;margin:3px 0;color:#1a5276!important;}
.inf b,.inf i,.inf small{color:#1a5276!important;}
.pen{background:#fde8e8!important;border:1px solid #e74c3c;border-radius:7px;
     padding:.6rem .9rem;margin:3px 0;color:#922b21!important;}
.pen b,.pen i,.pen small{color:#922b21!important;}
.db-gate{max-width:500px;margin:70px auto;padding:2rem;background:#f8fafc;
         border:1px solid #d0d7de;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);}
div[data-testid="stDataFrame"] thead tr th{
    position:sticky!important;top:0;z-index:10;
    background:#1a3c5e!important;color:#fff!important;font-size:.8rem;padding:6px 8px;}
</style>""", unsafe_allow_html=True)

from modules import get_db_connection, get_sqlite_connection, db_load_setup
from pages import (render_tab_setup, render_tab_scenarios,
                   render_tab_prepayment, render_tab_schedule, render_tab_comparison)

for k, v in {"db_conn": None, "setup_loaded": False, "setup_data": None,
              "rc_scenarios": {}, "past_prepayments": [], "past_renewals": [],
              "wire_bytes": None, "sc_loaded_from_db": False,
              "pp_scenarios": {}, "pp_sc_loaded": False,
              "_editing_sc_id": None, "_editing_pp_sc_id": None, "_dialog_shown": False}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── DB Gate ───────────────────────────────────────────────────────
if not st.session_state.db_conn:
    st.markdown('<div class="main-header">🏠 Canadian Mortgage Analyzer</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Choose a database to begin.</div>', unsafe_allow_html=True)
    _, cm, _ = st.columns([1,2,1])
    with cm:
        st.markdown('<div class="db-gate">', unsafe_allow_html=True)
        st.markdown("### 🗄️ Database Connection")

        # REQ #8: DB type selector
        db_type = st.radio("Database type", ["MS SQL Server", "SQLite (local file)"],
                            horizontal=True, key="g_dbtype",
                            help="MS SQL Server: enterprise DB requiring ODBC Driver 17  |  "
                                 "SQLite: zero-config local file, no server needed")

        if db_type == "MS SQL Server":
            st.caption("Requires ODBC Driver 17 for SQL Server installed on this machine.")
            srv = st.text_input("SQL Server", r"localhost\SQLEXPRESS", key="g_srv",
                                 help="Server name or IP, e.g. localhost\\SQLEXPRESS or 192.168.1.10")
            db  = st.text_input("Database", "MortgageDB", key="g_db",
                                 help="Database name (created in SQL Server Management Studio)")
            tru = st.checkbox("Windows Authentication", True, key="g_tru",
                               help="Use Windows login credentials (recommended for local SQL Express)")
            usr = pwd = ""
            if not tru:
                usr = st.text_input("Username", key="g_usr")
                pwd = st.text_input("Password", type="password", key="g_pwd")
            btn_label = "🔌 Connect to SQL Server"
        else:
            st.caption("All data stored in a local SQLite file. No server or driver needed.")
            default_path = os.path.join(os.getcwd(), "mortgage_local.db")
            db_path = st.text_input("Local DB file path", default_path, key="g_sqlite_path",
                                     help="Full path to the SQLite .db file. Will be created if it doesn't exist.")
            btn_label = "🗂️ Open / Create Local DB"

        if st.button(btn_label, use_container_width=True, key="btn_gate"):
            if db_type == "MS SQL Server":
                conn, err = get_db_connection(srv, db, tru, usr, pwd)
            else:
                conn, err = get_sqlite_connection(db_path)

            if conn:
                st.session_state.db_conn = conn
                ex = db_load_setup(conn)
                if ex:
                    st.session_state.setup_data   = ex
                    st.session_state.setup_loaded = True
                    st.session_state.past_renewals    = ex.get("past_renewals", [])
                    st.session_state.past_prepayments = ex.get("past_prepayments", [])
                st.rerun()
            else:
                st.error(f"❌ Could not connect: {err}")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

conn = st.session_state.db_conn
if not st.session_state.setup_loaded:
    ex = db_load_setup(conn)
    if ex:
        st.session_state.setup_data   = ex
        st.session_state.setup_loaded = True
        st.session_state.past_renewals    = ex.get("past_renewals", [])
        st.session_state.past_prepayments = ex.get("past_prepayments", [])

hc1, hc2 = st.columns([5,1])
hc1.markdown('<div class="main-header">🏠 Canadian Mortgage Analyzer</div>', unsafe_allow_html=True)
hc1.markdown('<div class="sub-header">Canadian semi-annual compounding · CMHC · '
             'Prepayments · Rate scenarios · Break penalties</div>', unsafe_allow_html=True)
db_label = getattr(conn, "label", "DB")
db_type_badge = "🗂️ SQLite" if getattr(conn,"db_type","")=="sqlite" else "🏢 SQL Server"
hc2.markdown(f"<div style='text-align:right;padding-top:1.2rem;color:#27ae60;font-size:.85rem;'>"
             f"🟢 {db_type_badge}</div>", unsafe_allow_html=True)

# Reset dialog guard each script run so only first caller shows it
st.session_state["_dialog_shown"] = False

tabs = st.tabs(["📊 Setup & Overview",
                "📈 Rate Change Scenarios",
                "💰 Prepayment Analysis",
                "📅 Amortization Schedule",
                "🔄 Scenario Comparison"])
with tabs[0]: render_tab_setup(conn)
b = st.session_state.get("base")
with tabs[1]: render_tab_scenarios(conn, b)
with tabs[2]: render_tab_prepayment(conn, b)
with tabs[3]: render_tab_schedule(conn, b)
with tabs[4]: render_tab_comparison(conn, b)
