"""
Canadian Mortgage Analyzer — Streamlit App v8 (Modular)
========================================================
Fixes applied vs v7:
1.  TypeError on second scenario save — ternary replaced with explicit if/else
2.  Scenario page: parallel Base | Scenario metric boxes (green tiles)
3.  Dark-mode metric tiles — text colour forced dark (#1a1a1a) for contrast
4.  Modular code — split into mortgage_math / mortgage_db / mortgage_charts /
    mortgage_wireframe / tabs/tab_*.py
5.  DB connect: if tables missing, auto-run setup_db.sql before inline DDL
6.  Chart legend moved to BOTTOM; annotations at y=1.06/1.13 (no overlap)
"""

import streamlit as st
import sys
import os

# ── Ensure local modules are importable ──────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# ── PAGE CONFIG (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="🏠 Canadian Mortgage Analyzer",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── GLOBAL CSS ────────────────────────────────────────────────────────────────
# FIX #3: .mc text colours use !important so they render in dark mode too.
#         Background stays light (#f0f4f8) so coloured text is always readable.
st.markdown("""
<style>
/* ── Main header ─────────────────────────── */
.main-header {
    font-size: 2.1rem; font-weight: 700; color: #1a3c5e; margin-bottom: .1rem;
}
.sub-header {
    font-size: .9rem; color: #555; margin-bottom: 1.2rem;
}

/* ── Metric card base ───────────────────── */
.mc {
    background: #f0f4f8 !important;
    border-radius: 9px;
    padding: .65rem 1rem;
    border-left: 4px solid #1a3c5e;
    margin-bottom: .35rem;
}
.mc h3 {
    margin: 0;
    font-size: .72rem;
    color: #444444 !important;
    text-transform: uppercase;
    letter-spacing: .05em;
}
.mc p {
    margin: 0;
    font-size: 1.2rem;
    font-weight: 700;
    color: #1a3c5e !important;
}

/* ── Colour variants ────────────────────── */
.mc-g { border-left: 4px solid #27ae60 !important; }
.mc-g p { color: #1a6b3c !important; }
.mc-g h3 { color: #2d5a3d !important; }

.mc-r { border-left: 4px solid #e74c3c !important; }
.mc-r p { color: #922b21 !important; }
.mc-r h3 { color: #7b241c !important; }

.mc-b { border-left: 4px solid #2980b9 !important; }
.mc-b p { color: #1a5276 !important; }
.mc-b h3 { color: #1a4a6e !important; }

/* ── Info / warning / status bars ──────── */
.warn {
    background: #fff3cd !important;
    border: 1px solid #ffc107;
    border-radius: 7px;
    padding: .6rem .9rem;
    margin: 3px 0;
    color: #7d5a00 !important;
}
.ok {
    background: #d4edda !important;
    border: 1px solid #28a745;
    border-radius: 7px;
    padding: .6rem .9rem;
    margin: 3px 0;
    color: #155724 !important;
}
.inf {
    background: #cce5ff !important;
    border: 1px solid #004085;
    border-radius: 7px;
    padding: .6rem .9rem;
    margin: 3px 0;
    color: #003366 !important;
}
.pen {
    background: #f8d7da !important;
    border: 1px solid #f5c6cb;
    border-radius: 7px;
    padding: .6rem .9rem;
    margin: 3px 0;
    color: #721c24 !important;
}

/* ── DB gate card ───────────────────────── */
.db-gate {
    max-width: 460px;
    margin: 70px auto;
    padding: 2rem;
    background: #f8fafc !important;
    border: 1px solid #d0d7de;
    border-radius: 12px;
    box-shadow: 0 4px 20px rgba(0,0,0,.08);
}

/* ── Frozen DataFrame header ────────────── */
div[data-testid="stDataFrame"] thead tr th {
    position: sticky !important;
    top: 0;
    z-index: 10;
    background: #1a3c5e !important;
    color: #fff !important;
    font-size: .8rem;
    padding: 6px 8px;
}
</style>
""", unsafe_allow_html=True)

# ── LOCAL IMPORTS (after path setup) ─────────────────────────────────────────
from mortgage_db import get_db_connection, db_load_setup

# ── SESSION STATE INITIALISATION ─────────────────────────────────────────────
for k, v in {
    "db_conn": None,
    "setup_loaded": False,
    "setup_data": None,
    "rc_scenarios": {},
    "past_prepayments": [],
    "past_renewals": [],
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════════════
# GATE: DB CONNECTION
# FIX #5: _init_db (called inside get_db_connection) will auto-run setup_db.sql
#         if tables are missing, before falling back to inline DDL.
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state.db_conn:
    st.markdown(
        '<div class="main-header">🏠 Canadian Mortgage Analyzer</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<div class="sub-header">Connect to your SQL Server database to begin.</div>',
        unsafe_allow_html=True
    )
    _, cm, _ = st.columns([1, 2, 1])
    with cm:
        st.markdown('<div class="db-gate">', unsafe_allow_html=True)
        st.markdown("### 🗄️ Database Connection")
        srv = st.text_input(
            "SQL Server", r"localhost\SQLEXPRESS", key="g_srv",
            help="e.g. localhost\\SQLEXPRESS or myserver.database.windows.net"
        )
        db = st.text_input(
            "Database", "MortgageDB", key="g_db",
            help="Target database name — will be created if it does not exist"
        )
        tru = st.checkbox("Windows Authentication", True, key="g_tru",
                          help="Use current Windows user credentials (recommended for local)")
        usr = pwd = ""
        if not tru:
            usr = st.text_input("Username", key="g_usr")
            pwd = st.text_input("Password", type="password", key="g_pwd")

        if st.button("🔌 Connect & Continue", use_container_width=True, key="btn_gate"):
            conn, err = get_db_connection(srv, db, tru, usr, pwd)
            if conn:
                st.session_state.db_conn = conn
                ex = db_load_setup(conn)
                if ex:
                    st.session_state.setup_data = ex
                    st.session_state.setup_loaded = True
                    st.session_state.past_renewals = ex.get("past_renewals", [])
                    st.session_state.past_prepayments = ex.get("past_prepayments", [])
                st.rerun()
            else:
                st.error(f"❌ {err}")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ── Load saved setup on first run after connect ───────────────────────────────
if not st.session_state.setup_loaded:
    ex = db_load_setup(st.session_state.db_conn)
    if ex:
        st.session_state.setup_data = ex
        st.session_state.setup_loaded = True
        st.session_state.past_renewals = ex.get("past_renewals", [])
        st.session_state.past_prepayments = ex.get("past_prepayments", [])

# ── App Header ────────────────────────────────────────────────────────────────
hc1, hc2 = st.columns([5, 1])
hc1.markdown(
    '<div class="main-header">🏠 Canadian Mortgage Analyzer</div>',
    unsafe_allow_html=True
)
hc1.markdown(
    '<div class="sub-header">'
    'Canadian semi-annual compounding · CMHC · Prepayments · Rate scenarios · Break penalties'
    '</div>',
    unsafe_allow_html=True
)
hc2.markdown(
    "<div style='text-align:right;padding-top:1.2rem;color:#27ae60;font-size:.85rem;'>"
    "🟢 DB Connected</div>",
    unsafe_allow_html=True
)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tabs = st.tabs([
    "📊 Setup & Overview",
    "📈 Rate Change Scenarios",
    "📅 Amortization Schedule",
    "💰 Prepayment Analysis",
    "⚠️ Break Penalty",
    "🔄 Scenario Comparison",
])

# ── Render each tab via its module ───────────────────────────────────────────
import tab_setup, tab_scenarios, tab_amortization
import tab_prepayment, tab_breakpenalty, tab_comparison

tab_setup.render(tabs)
tab_scenarios.render(tabs)
tab_amortization.render(tabs)
tab_prepayment.render(tabs)
tab_breakpenalty.render(tabs)
tab_comparison.render(tabs)
