"""
Canadian Mortgage Analyzer — Streamlit App v3
Key changes from v2:
  1. Updated defaults (SQLEXPRESS, $1.03M, 2023-08-15, 3yr term, 30yr amort, 5.39%)
  2. Key Metrics: balance/principal/interest as of today
  3. Amortization schedule highlights & defaults to current month
  4. Save Setup to DB (mortgage_setup table)
  5. Fixed nested expanders (templates use st.checkbox toggle)
  6. Rate scenarios: any dates, Fixed/Variable type, term periods, break penalty, misc fees
  7. Setup tab: multiple past renewal terms
Database: MS SQL Server (optional — degrades gracefully without it)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import date
from dateutil.relativedelta import relativedelta
import json
import uuid

# ──────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ──────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🏠 Canadian Mortgage Analyzer",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────────
# GLOBAL CSS
# ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header{font-size:2.2rem;font-weight:700;color:#1a3c5e;margin-bottom:0.2rem;}
    .sub-header {font-size:0.95rem;color:#555;margin-bottom:1.5rem;}
    .metric-card{background:#f0f4f8;border-radius:10px;padding:0.8rem 1.2rem;
                 border-left:4px solid #1a3c5e;margin-bottom:0.5rem;}
    .metric-card h3{margin:0;font-size:0.75rem;color:#666;text-transform:uppercase;letter-spacing:.05em;}
    .metric-card p {margin:0;font-size:1.35rem;font-weight:700;color:#1a3c5e;}
    .metric-today{border-left:4px solid #27ae60;}
    .metric-today p{color:#27ae60;}
    .warning-box{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;
                 padding:0.7rem 1rem;margin:4px 0;}
    .success-box{background:#d4edda;border:1px solid #28a745;border-radius:8px;
                 padding:0.7rem 1rem;margin:4px 0;}
    .info-box   {background:#cce5ff;border:1px solid #004085;border-radius:8px;
                 padding:0.7rem 1rem;margin:4px 0;}
    .penalty-box{background:#f8d7da;border:1px solid #f5c6cb;border-radius:8px;
                 padding:0.7rem 1rem;margin:4px 0;}
    .renewal-card{background:#f8fafc;border:1px solid #d0d7de;border-radius:8px;
                  padding:0.8rem 1rem;margin:6px 0;}
    div[data-testid="stExpander"]{border:1px solid #d0d7de;border-radius:8px;}
    .stTabs [data-baseweb="tab"]{padding:8px 18px;border-radius:8px 8px 0 0;}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────
# DATABASE HELPERS
# ──────────────────────────────────────────────────────────────────
def get_db_connection(server, database, trusted):
    try:
        import pyodbc
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};DATABASE={database};"
            + ("Trusted_Connection=yes;" if trusted else
               f"UID={st.session_state.get('db_user','')};PWD={st.session_state.get('db_pass','')};")
        )
        conn = pyodbc.connect(conn_str, timeout=5)
        _init_db(conn)
        return conn
    except Exception:
        return None


def _init_db(conn):
    cur = conn.cursor()
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U')
        CREATE TABLE mortgage_scenarios (
            id INT IDENTITY(1,1) PRIMARY KEY, name NVARCHAR(200),
            created_at DATETIME DEFAULT GETDATE(),
            params NVARCHAR(MAX), summary NVARCHAR(MAX))
    """)
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='mortgage_setup' AND xtype='U')
        CREATE TABLE mortgage_setup (
            id INT IDENTITY(1,1) PRIMARY KEY, setup_name NVARCHAR(200),
            saved_at DATETIME DEFAULT GETDATE(), setup_data NVARCHAR(MAX))
    """)
    conn.commit()


def db_save_scenario(conn, name, params, summary):
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO mortgage_scenarios (name,params,summary) VALUES (?,?,?)",
                    name, json.dumps(params), json.dumps(summary))
        conn.commit(); return True
    except Exception: return False


def db_load_scenarios(conn):
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,created_at,params,summary FROM mortgage_scenarios ORDER BY created_at DESC")
        return [{"id":r[0],"name":r[1],"created_at":str(r[2]),
                 "params":json.loads(r[3]),"summary":json.loads(r[4])} for r in cur.fetchall()]
    except Exception: return []


def db_delete_scenario(conn, sid):
    if not conn: return
    try: cur=conn.cursor(); cur.execute("DELETE FROM mortgage_scenarios WHERE id=?",sid); conn.commit()
    except Exception: pass


def db_save_setup(conn, name, data):
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO mortgage_setup (setup_name,setup_data) VALUES (?,?)",
                    name, json.dumps(data, default=str))
        conn.commit(); return True
    except Exception: return False


def db_load_setups(conn):
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,setup_name,saved_at,setup_data FROM mortgage_setup ORDER BY saved_at DESC")
        return [{"id":r[0],"name":r[1],"saved_at":str(r[2]),"data":json.loads(r[3])}
                for r in cur.fetchall()]
    except Exception: return []

# ──────────────────────────────────────────────────────────────────
# CORE MATH
# ──────────────────────────────────────────────────────────────────
FREQ_CONFIG = {
    "Monthly":               {"n":12,  "accel":False},
    "Semi-Monthly":          {"n":24,  "accel":False},
    "Bi-Weekly":             {"n":26,  "accel":False},
    "Accelerated Bi-Weekly": {"n":26,  "accel":True},
    "Weekly":                {"n":52,  "accel":False},
    "Accelerated Weekly":    {"n":52,  "accel":True},
}


def periodic_rate(annual_pct, n_per_year):
    eff_annual = (1 + annual_pct/200)**2
    return eff_annual**(1/n_per_year) - 1


def calc_regular_payment(principal, annual_pct, n_per_year, amort_years, accel=False):
    if annual_pct == 0:
        t = amort_years * n_per_year
        return principal/t if t else 0
    r = periodic_rate(annual_pct, n_per_year)
    n = amort_years * n_per_year
    pmt = principal * r * (1+r)**n / ((1+r)**n - 1)
    if accel:
        r_m = periodic_rate(annual_pct, 12)
        n_m = amort_years*12
        monthly = principal * r_m * (1+r_m)**n_m / ((1+r_m)**n_m - 1)
        pmt = monthly / (n_per_year/12)
    return pmt


def cmhc_premium(purchase_price, down_payment):
    dp_pct = down_payment / purchase_price * 100
    if dp_pct >= 20: return 0.0, 0.0
    if purchase_price > 1_500_000 or dp_pct < 5: return None, None
    insured = purchase_price - down_payment
    rate = 0.04 if dp_pct < 10 else (0.031 if dp_pct < 15 else 0.028)
    p = insured * rate
    return p, p * 0.13


def date_to_period(target_date, start_date, n_per_year):
    if isinstance(target_date, str): target_date = date.fromisoformat(target_date)
    if isinstance(start_date, str):  start_date  = date.fromisoformat(start_date)
    delta = (target_date - start_date).days
    return max(1, int(round(delta / 365.25 * n_per_year)))


def period_to_date(period, start_date, n_per_year):
    if isinstance(start_date, str): start_date = date.fromisoformat(start_date)
    days = int((period-1) / n_per_year * 365.25)
    return start_date + relativedelta(days=days)


def build_amortization(
    principal, annual_pct, n_per_year, amort_years,
    accel=False, start_date=None,
    extra_payments=None, rate_changes=None, term_periods=None,
):
    if start_date is None: start_date = date.today().replace(day=1)
    if isinstance(start_date, str): start_date = date.fromisoformat(start_date)

    payment = calc_regular_payment(principal, annual_pct, n_per_year, amort_years, accel)
    r       = periodic_rate(annual_pct, n_per_year)

    extra_map = {}
    if extra_payments:
        for ep in extra_payments:
            p = int(ep["period"])
            extra_map[p] = extra_map.get(p, 0) + float(ep["amount"])

    rate_map = {}
    if rate_changes:
        for rc in rate_changes:
            rate_map[int(rc["period"])] = float(rc["new_rate"])

    rows=[];balance=float(principal);total_p=amort_years*n_per_year
    cur_rate=float(annual_pct);cur_r=r;cum_int=0.0;cum_pmt=0.0;cum_prep=0.0
    period_date=start_date

    for i in range(1, int(total_p)+1):
        if balance <= 0.005: break
        if term_periods and i > term_periods: break
        if i in rate_map:
            cur_rate = rate_map[i]; cur_r = periodic_rate(cur_rate, n_per_year)
            remaining = total_p - i + 1
            payment = calc_regular_payment(balance, cur_rate, n_per_year, remaining/n_per_year, accel)
        int_chg = balance * cur_r
        princ   = min(max(payment - int_chg, 0), balance)
        extra   = min(extra_map.get(i, 0), max(balance-princ, 0))
        balance -= princ + extra
        cum_int += int_chg; cum_pmt += payment; cum_prep += extra
        rows.append({
            "Period":i,"Date":period_date,"Year":((i-1)//n_per_year)+1,
            "Payment":round(payment,2),"Interest":round(int_chg,2),
            "Principal":round(princ,2),"Prepayment":round(extra,2),
            "Total Paid":round(payment+extra,2),"Balance":round(max(balance,0),2),
            "Rate (%)":round(cur_rate,3),"Cum Interest":round(cum_int,2),
            "Cum Principal":round(cum_pmt-cum_int,2),"Cum Prepayment":round(cum_prep,2),
        })
        if n_per_year==12: period_date+=relativedelta(months=1)
        elif n_per_year==24: period_date+=relativedelta(days=15)
        elif n_per_year==26: period_date+=relativedelta(weeks=2)
        else: period_date+=relativedelta(weeks=1)

    df = pd.DataFrame(rows)
    if df.empty: return df, {}
    ti=df["Interest"].sum(); tp=df["Prepayment"].sum(); tt=df["Payment"].sum()+tp
    return df, {
        "payment":round(payment,2),"total_paid":round(tt,2),
        "total_interest":round(ti,2),"total_principal":round(df["Principal"].sum(),2),
        "total_prepaid":round(tp,2),"end_balance":round(df["Balance"].iloc[-1],2),
        "payoff_periods":len(df),"payoff_years":round(len(df)/n_per_year,2),
        "interest_pct":round(ti/tt*100,1) if tt else 0,
    }


def get_today_metrics(df):
    """Return balance / principal-paid / interest-paid as of today."""
    today = date.today()
    if df.empty: return {}
    past = df[df["Date"] <= today]
    if past.empty:
        row = df.iloc[0]
        return {"balance_today":float(row["Balance"]),"principal_paid_today":0.0,
                "interest_paid_today":0.0,"period_today":0,"as_of_date":str(today)}
    row = past.iloc[-1]
    return {
        "balance_today":    float(row["Balance"]),
        "principal_paid_today": float(row["Cum Principal"]),
        "interest_paid_today":  float(row["Cum Interest"]),
        "period_today":     int(row["Period"]),
        "as_of_date":       row["Date"].strftime("%b %d, %Y") if hasattr(row["Date"],"strftime")
                            else str(row["Date"]),
    }


def calc_break_penalty(outstanding, rate_pct, mtype, orig_posted, curr_posted, months_left):
    monthly_r = periodic_rate(rate_pct, 12)
    three_mo  = outstanding * monthly_r * 3
    res = {"3_months_interest": round(three_mo, 2)}
    if mtype == "Variable":
        res.update(penalty=round(three_mo,2), method="3 months interest (variable)", ird=None)
    else:
        ird = max(outstanding*(orig_posted-curr_posted)/100*months_left/12, 0)
        pen = max(three_mo, ird)
        res.update(ird=round(ird,2), penalty=round(pen,2),
                   method="IRD" if ird>three_mo else "3 months interest")
    return res

# ──────────────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────────────
_defaults = {
    "db_server":  r"localhost\SQLEXPRESS",   # updated default
    "db_name":    "MortgageDB",
    "db_trusted": True, "db_conn": None, "db_user": "", "db_pass": "",
    "saved_scenarios": {}, "rc_scenarios": {},
    "past_prepayments": [],
    "past_renewals":    [],   # [{id, start_date_str, rate, mtype, term_years}]
    "setup_save_name":  "My Mortgage",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state: st.session_state[_k] = _v

# ──────────────────────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏠 Canadian Mortgage\nAnalyzer")
    st.divider()
    st.markdown("### 🗄️ Database")
    _srv = st.text_input("SQL Server", value=st.session_state.db_server, key="sb_srv")
    _db  = st.text_input("Database",   value=st.session_state.db_name,   key="sb_db")
    _tru = st.checkbox("Windows Auth", value=st.session_state.db_trusted, key="sb_tru")
    if not _tru:
        st.session_state.db_user = st.text_input("Username", key="sb_usr")
        st.session_state.db_pass = st.text_input("Password", type="password", key="sb_pwd")
    if st.button("🔌 Connect", use_container_width=True, key="btn_conn"):
        _c = get_db_connection(_srv, _db, _tru)
        if _c: st.session_state.db_conn=_c; st.success("✅ Connected!")
        else:  st.error("❌ Local mode (no DB).")
    st.caption("🟢 Connected" if st.session_state.db_conn else "🔴 Local mode")
    st.divider()
    # Load saved setups
    saved_setups = db_load_setups(st.session_state.db_conn)
    if saved_setups:
        st.markdown("### 📂 Load Saved Setup")
        sel_setup = st.selectbox("Choose setup", ["— select —"] + [f"{s['name']} ({s['saved_at'][:10]})" for s in saved_setups], key="sb_setup_sel")
        if sel_setup != "— select —" and st.button("Load", key="btn_load_setup"):
            idx = [f"{s['name']} ({s['saved_at'][:10]})" for s in saved_setups].index(sel_setup)
            d   = saved_setups[idx]["data"]
            # Write into session state keys that widgets read from
            for sk, sv in d.get("widget_state", {}).items():
                st.session_state[sk] = sv
            if "past_renewals"    in d: st.session_state.past_renewals    = d["past_renewals"]
            if "past_prepayments" in d: st.session_state.past_prepayments = d["past_prepayments"]
            st.rerun()
    st.divider()
    with st.expander("Canadian Mortgage Rules"):
        st.caption("""
- Semi-annual compounding (Interest Act)
- Insured: max 25 yr amort, price ≤ $1.5M
- First-time/new build: up to 30 yrs insured
- CMHC: 2.8%–4.0% of insured amount
- Variable penalty: 3 months interest
- Fixed penalty: max(3 mo interest, IRD)
- Prepayment: typically 10–20%/yr privilege
        """)
    with st.expander("CMHC Premium Rates"):
        st.table(pd.DataFrame({"Down Payment":["5–9.99%","10–14.99%","15–19.99%","≥ 20%"],
                               "Premium":["4.00%","3.10%","2.80%","0%"]}))

# ──────────────────────────────────────────────────────────────────
# HEADER
# ──────────────────────────────────────────────────────────────────
st.markdown('<div class="main-header">🏠 Canadian Mortgage Analyzer</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Canadian semi-annual compounding · CMHC · Multi-term · Prepayments · Rate scenarios · Break penalties</div>', unsafe_allow_html=True)

tabs = st.tabs([
    "📊 Setup & Overview", "📅 Amortization Schedule", "📈 Rate Change Scenarios",
    "💰 Prepayment Analysis", "⚠️ Break Penalty", "🔄 Scenario Comparison", "💾 Saved Scenarios",
])

# ══════════════════════════════════════════════════════════════════
# TAB 1 — SETUP & OVERVIEW
# ══════════════════════════════════════════════════════════════════
with tabs[0]:
    st.subheader("Mortgage Setup")
    col_l, col_r = st.columns([1.2, 1])

    with col_l:
        st.markdown("#### 🏡 Property & Down Payment")
        c1, c2 = st.columns(2)
        purchase_price = c1.number_input("Purchase Price ($)", 100_000, 5_000_000,
                                         1_030_000, 5_000, format="%d", key="s_price")
        down_pct  = c2.slider("Down Payment (%)", 5.0, 50.0, 20.0, 0.5, key="s_dpct")
        down_pay  = purchase_price * down_pct / 100
        c1.metric("Down Payment", f"${down_pay:,.0f}")

        cmhc, hst = cmhc_premium(purchase_price, down_pay)
        if cmhc is None:
            st.markdown('<div class="warning-box">⚠️ CMHC not available (price >$1.5M or down <5%)</div>',
                        unsafe_allow_html=True)
            insured_principal = purchase_price - down_pay
        elif cmhc == 0:
            st.markdown('<div class="success-box">✅ No CMHC premium — down payment ≥ 20%</div>',
                        unsafe_allow_html=True)
            insured_principal = purchase_price - down_pay
        else:
            add_cmhc = c2.checkbox("Add CMHC premium to mortgage?", value=True, key="s_addcmhc")
            st.markdown(
                f'<div class="info-box">🛡️ CMHC Premium: <b>${cmhc:,.0f}</b> '
                f'(+PST/HST ~${hst:,.0f}) · Rate: {cmhc/(purchase_price-down_pay)*100:.2f}%</div>',
                unsafe_allow_html=True)
            insured_principal = (purchase_price - down_pay) + (cmhc if add_cmhc else 0)

        st.markdown("#### 💵 Original Mortgage Term")
        c3, c4 = st.columns(2)
        mortgage_type = c3.selectbox("Mortgage Type", ["Fixed","Variable"], key="s_mtype")
        payment_freq  = c4.selectbox("Payment Frequency", list(FREQ_CONFIG.keys()), index=0, key="s_freq")
        annual_rate   = c3.number_input("Interest Rate (%)", 0.5, 20.0, 5.39, 0.01,   # UPDATED
                                        format="%.2f", key="s_rate")
        amort_years   = c4.slider("Amortization (years)", 5, 30, 30, key="s_amort")    # UPDATED
        term_years    = c3.selectbox("Term (years)", [0.5,1,2,3,4,5,7,10],
                                     index=3, key="s_term")                             # index 3 = 3yr
        start_date_in = c4.date_input("Mortgage Start Date",
                                      date(2023, 8, 15), key="s_startdate")             # UPDATED

        if down_pct < 20 and amort_years > 25:
            st.markdown('<div class="warning-box">⚠️ Insured mortgages limited to 25-yr amortization.</div>',
                        unsafe_allow_html=True)

    freq_cfg = FREQ_CONFIG[payment_freq]
    n_py     = freq_cfg["n"]
    accel    = freq_cfg["accel"]

    # ── Past Renewal Terms ────────────────────────────────────────
    with col_l:
        st.markdown("#### 🔄 Additional Mortgage Terms (Past Renewals)")
        st.caption("Add any renewal terms that have already taken effect since your mortgage started.")

        if st.button("➕ Add Past Renewal Term", key="btn_add_renewal"):
            # Default start = end of original term or end of last renewal
            if st.session_state.past_renewals:
                last = st.session_state.past_renewals[-1]
                prev_end = date.fromisoformat(last["start_date_str"]) + \
                           relativedelta(years=int(last["term_years"]),
                                         months=int((float(last["term_years"])%1)*12))
            else:
                prev_end = start_date_in + relativedelta(years=int(term_years),
                                                          months=int((term_years%1)*12))
            st.session_state.past_renewals.append({
                "id": str(uuid.uuid4())[:8],
                "start_date_str": str(prev_end),
                "rate": annual_rate, "mtype": "Fixed", "term_years": 3,
            })
            st.rerun()

        del_rn = []
        for idx, rn in enumerate(st.session_state.past_renewals):
            st.markdown(f'<div class="renewal-card">', unsafe_allow_html=True)
            rr1,rr2,rr3,rr4,rr5 = st.columns([2,1.5,1.5,1.5,0.8])
            new_sd  = rr1.date_input(f"Start date (renewal {idx+1})",
                                     value=date.fromisoformat(rn["start_date_str"]),
                                     key=f"rn_sd_{rn['id']}")
            new_r   = rr2.number_input(f"Rate (%) #{idx+1}", 0.5, 20.0,
                                       float(rn["rate"]), 0.01, format="%.2f",
                                       key=f"rn_rt_{rn['id']}")
            new_mt  = rr3.selectbox(f"Type #{idx+1}", ["Fixed","Variable"],
                                    index=0 if rn["mtype"]=="Fixed" else 1,
                                    key=f"rn_mt_{rn['id']}")
            new_ty  = rr4.selectbox(f"Term yrs #{idx+1}", [0.5,1,2,3,4,5,7,10],
                                    index=[0.5,1,2,3,4,5,7,10].index(rn["term_years"])
                                    if rn["term_years"] in [0.5,1,2,3,4,5,7,10] else 3,
                                    key=f"rn_ty_{rn['id']}")
            if rr5.button("🗑️", key=f"del_rn_{rn['id']}"):
                del_rn.append(idx)
            end_d = date.fromisoformat(str(new_sd)) + relativedelta(years=int(new_ty),
                                        months=int((float(new_ty)%1)*12))
            rr1.caption(f"End: {end_d.strftime('%b %Y')}")
            st.session_state.past_renewals[idx].update(
                start_date_str=str(new_sd), rate=float(new_r),
                mtype=new_mt, term_years=new_ty)
            st.markdown("</div>", unsafe_allow_html=True)

        for i in sorted(del_rn, reverse=True):
            st.session_state.past_renewals.pop(i)
        if del_rn: st.rerun()

    # Build base rate_changes from past renewals
    past_renewal_rcs = []
    for rn in st.session_state.past_renewals:
        p = date_to_period(rn["start_date_str"], start_date_in, n_py)
        past_renewal_rcs.append({"period": p, "new_rate": float(rn["rate"])})

    with col_r:
        pmt_actual = calc_regular_payment(insured_principal, annual_rate, n_py, amort_years, accel)
        term_p   = int(term_years * n_py)
        full_df, full_sum = build_amortization(
            insured_principal, annual_rate, n_py, amort_years,
            accel=accel, start_date=start_date_in,
            rate_changes=past_renewal_rcs or None,
        )
        term_end = start_date_in + relativedelta(years=int(term_years),
                                                  months=int((term_years%1)*12))
        _, t_sum = build_amortization(
            insured_principal, annual_rate, n_py, amort_years,
            accel=accel, start_date=start_date_in, term_periods=term_p)

        # TODAY'S METRICS
        today_m = get_today_metrics(full_df)

        st.markdown("#### 📊 Key Metrics at a Glance")
        st.markdown(f"""
        <div class="metric-card">
            <h3>Mortgage Principal</h3><p>${insured_principal:,.0f}</p></div>
        <div class="metric-card">
            <h3>{payment_freq} Payment</h3><p>${pmt_actual:,.2f}</p></div>
        <div class="metric-card">
            <h3>Balance at Term End ({term_end.strftime('%b %Y')})</h3>
            <p>${t_sum.get('end_balance', insured_principal):,.0f}</p></div>
        """, unsafe_allow_html=True)

        # Today's metrics (green cards)
        if today_m:
            st.markdown(f"""
            <div class="metric-card metric-today">
                <h3>🟢 Balance as of Today ({today_m.get('as_of_date','')})</h3>
                <p>${today_m.get('balance_today',0):,.0f}</p></div>
            <div class="metric-card metric-today">
                <h3>🟢 Principal Paid as of Today</h3>
                <p>${today_m.get('principal_paid_today',0):,.0f}</p></div>
            <div class="metric-card metric-today">
                <h3>🟢 Interest Paid as of Today</h3>
                <p>${today_m.get('interest_paid_today',0):,.0f}</p></div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
        <div class="metric-card">
            <h3>Total Interest (full amortization)</h3>
            <p style="color:#c0392b">${full_sum.get('total_interest',0):,.0f}</p></div>
        <div class="metric-card">
            <h3>Interest as % of Total Paid</h3>
            <p style="color:#c0392b">{full_sum.get('interest_pct',0):.1f}%</p></div>
        <div class="metric-card">
            <h3>Effective Payoff</h3>
            <p>{full_sum.get('payoff_years',0):.1f} years</p></div>
        """, unsafe_allow_html=True)

    # Charts
    st.divider()
    cd1, cd2 = st.columns(2)
    with cd1:
        fig_d = go.Figure(go.Pie(labels=["Principal","Total Interest"],
                                  values=[insured_principal, full_sum.get("total_interest",0)],
                                  hole=0.55, marker_colors=["#1a3c5e","#e74c3c"],
                                  textinfo="label+percent"))
        fig_d.update_layout(title="Principal vs Interest", height=300, margin=dict(t=40,b=10,l=10,r=10))
        st.plotly_chart(fig_d, use_container_width=True, key="ch_donut")
    with cd2:
        if not full_df.empty:
            yr = full_df.groupby("Year").agg(Interest=("Interest","sum"),
                                              Principal=("Principal","sum")).reset_index()
            fig_b = go.Figure()
            fig_b.add_bar(x=yr["Year"],y=yr["Principal"],name="Principal",marker_color="#1a3c5e")
            fig_b.add_bar(x=yr["Year"],y=yr["Interest"], name="Interest", marker_color="#e74c3c")
            for rn in st.session_state.past_renewals:
                p = date_to_period(rn["start_date_str"], start_date_in, n_py)
                yr_n = ((p-1)//n_py)+1
                fig_b.add_vline(x=yr_n, line_dash="dot", line_color="#27ae60",
                                annotation_text=f"{rn['rate']}%", annotation_position="top left")
            fig_b.update_layout(barmode="stack", title="Yearly Principal vs Interest",
                                 xaxis_title="Year", yaxis_title="($)", height=300,
                                 margin=dict(t=40,b=40,l=10,r=10))
            st.plotly_chart(fig_b, use_container_width=True, key="ch_yearbar")

    # Past Prepayments
    st.divider()
    st.markdown("#### 💳 Past Prepayments Already Made")
    st.caption("Exact lump-sum payments already made — flows into all calculations.")

    if st.button("➕ Add Past Prepayment", key="btn_add_pp"):
        st.session_state.past_prepayments.append(
            {"id":str(uuid.uuid4())[:8],"date_str":str(start_date_in),"amount":0.0})
        st.rerun()

    del_pp=[]
    for idx, pp in enumerate(st.session_state.past_prepayments):
        r1,r2,r3 = st.columns([2,2,1])
        nd = r1.date_input(f"Date #{idx+1}", value=date.fromisoformat(pp["date_str"]),
                           min_value=start_date_in, max_value=date.today(), key=f"ppd_{pp['id']}")
        na = r2.number_input(f"Amount ($) #{idx+1}", 0, 2_000_000,
                             int(pp["amount"]), 500, key=f"ppa_{pp['id']}")
        if r3.button("🗑️", key=f"del_pp_{pp['id']}"): del_pp.append(idx)
        st.session_state.past_prepayments[idx].update(date_str=str(nd), amount=float(na))

    for i in sorted(del_pp, reverse=True): st.session_state.past_prepayments.pop(i)
    if del_pp: st.rerun()

    past_extra = []
    for pp in st.session_state.past_prepayments:
        if pp["amount"] > 0:
            past_extra.append({"period": date_to_period(pp["date_str"], start_date_in, n_py),
                               "amount": float(pp["amount"])})
    if past_extra:
        tot = sum(p["amount"] for p in past_extra)
        st.markdown(f'<div class="info-box">📌 {len(past_extra)} prepayment(s) — total <b>${tot:,.0f}</b></div>',
                    unsafe_allow_html=True)

    # Save Setup to DB
    st.divider()
    st.markdown("#### 💾 Save This Setup")
    sv1, sv2, sv3 = st.columns([2, 1, 1])
    setup_save_name = sv1.text_input("Setup name", st.session_state.setup_save_name, key="s_svname")
    if sv2.button("💾 Save to DB", key="btn_save_setup"):
        setup_payload = {
            "widget_state": {
                "s_price": purchase_price, "s_dpct": down_pct, "s_mtype": mortgage_type,
                "s_freq": payment_freq, "s_rate": annual_rate, "s_amort": amort_years,
                "s_term": term_years, "s_startdate": str(start_date_in),
                "s_addcmhc": True,
            },
            "past_renewals": st.session_state.past_renewals,
            "past_prepayments": st.session_state.past_prepayments,
            "summary": full_sum,
            "today_metrics": today_m,
        }
        if st.session_state.db_conn:
            ok = db_save_setup(st.session_state.db_conn, setup_save_name, setup_payload)
            sv3.success("✅ Saved!" if ok else "❌ Failed")
        else:
            sv3.warning("No DB — connect first.")

    # Store base in session state
    st.session_state["base"] = dict(
        principal=insured_principal, annual_rate=annual_rate, n_py=n_py,
        amort_years=amort_years, accel=accel, start_date=start_date_in,
        mortgage_type=mortgage_type, term_years=term_years,
        payment_freq=payment_freq, purchase_price=purchase_price,
        down_payment=down_pay, past_extra=past_extra,
        past_renewal_rcs=past_renewal_rcs,  # rate changes from past renewals
    )

# ══════════════════════════════════════════════════════════════════
# TAB 2 — AMORTIZATION SCHEDULE  (highlights current month)
# ══════════════════════════════════════════════════════════════════
with tabs[1]:
    st.subheader("📅 Full Amortization Schedule")
    b = st.session_state.get("base", {})
    if not b:
        st.warning("Configure your mortgage in **Setup & Overview** first.")
        st.stop()

    today = date.today()
    today_ym = today.strftime("%Y-%m")

    c_v1, c_v2, c_v3 = st.columns([2,2,2])
    view_mode  = c_v1.radio("View Mode", ["All Periods","Monthly Summary","Yearly Summary"],
                             horizontal=True, key="sch_view")
    show_term  = c_v2.checkbox(f"Current term only ({b['term_years']} yrs)", value=False, key="sch_term")
    jump_today = c_v3.checkbox("Jump to / highlight current month", value=True, key="sch_jump")

    all_rcs = (b.get("past_renewal_rcs") or [])
    term_p  = int(b["term_years"]*b["n_py"]) if show_term else None
    df_sch, _ = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=all_rcs or None, term_periods=term_p)

    if df_sch.empty:
        st.error("Could not build schedule.")
    else:
        today_period = None
        if jump_today:
            mask = df_sch["Date"].apply(lambda d: d.strftime("%Y-%m") if hasattr(d,"strftime") else "") == today_ym
            if mask.any(): today_period = int(df_sch[mask]["Period"].iloc[0])

        if view_mode == "Yearly Summary":
            disp = df_sch.groupby("Year").agg(
                Payments=("Payment","count"), Total_Paid=("Total Paid","sum"),
                Interest=("Interest","sum"), Principal=("Principal","sum"),
                Prepayment=("Prepayment","sum"), Ending_Balance=("Balance","last"),
                Cum_Interest=("Cum Interest","last")).reset_index()
            disp.columns = ["Year","Payments","Total Paid","Interest","Principal",
                            "Prepayment","Ending Balance","Cum Interest"]
        elif view_mode == "Monthly Summary" and b["n_py"] > 12:
            df_sch["YM"] = df_sch["Date"].apply(lambda d: d.strftime("%Y-%m") if hasattr(d,"strftime") else "")
            disp = df_sch.groupby("YM").agg(
                Total_Paid=("Total Paid","sum"), Interest=("Interest","sum"),
                Principal=("Principal","sum"), Ending_Balance=("Balance","last")).reset_index()
        else:
            disp = df_sch[["Period","Date","Payment","Interest","Principal",
                           "Prepayment","Total Paid","Balance","Rate (%)","Cum Interest"]].copy()
            disp["Date"] = disp["Date"].apply(lambda d: d.strftime("%Y-%m-%d") if hasattr(d,"strftime") else str(d))

        mc = [c for c in disp.columns if c not in ["Period","Year","Payments","YM","Date","Rate (%)"]]
        fmt = {c:"${:,.2f}" for c in mc}

        # Highlighting function
        def _highlight(row):
            if not jump_today: return [""]*len(row)
            is_cur = False
            if view_mode == "All Periods":
                is_cur = str(row.get("Date",""))[:7] == today_ym
            elif view_mode == "Yearly Summary":
                is_cur = str(row.get("Year","")) == str(today.year)
            elif view_mode == "Monthly Summary":
                is_cur = str(row.get("YM","")) == today_ym
            return ["background-color:#FFF3CD;font-weight:bold" if is_cur else ""]*len(row)

        st.dataframe(disp.style.apply(_highlight, axis=1).format(fmt),
                     use_container_width=True, height=460)

        if today_period:
            st.markdown(
                f'<div class="success-box">🟡 Current month highlighted — Period <b>{today_period}</b> '
                f'({today.strftime("%B %Y")}). '
                f'Balance: <b>${float(df_sch[df_sch["Period"]==today_period]["Balance"].iloc[0]):,.0f}</b>'
                f'</div>', unsafe_allow_html=True)

        fig_bal = go.Figure()
        fig_bal.add_scatter(x=df_sch["Period"], y=df_sch["Balance"],
                            fill="tozeroy", name="Balance", line=dict(color="#1a3c5e"))
        fig_bal.add_scatter(x=df_sch["Period"], y=df_sch["Cum Interest"],
                            name="Cum Interest", line=dict(color="#e74c3c", dash="dash"))
        if today_period:
            fig_bal.add_vline(x=today_period, line_dash="dash", line_color="#27ae60",
                              annotation_text="Today", annotation_position="top right")
        fig_bal.update_layout(title="Balance & Cumulative Interest",
                              xaxis_title=f"Period ({b['payment_freq']})",
                              yaxis_title="($)", height=380)
        st.plotly_chart(fig_bal, use_container_width=True, key="ch_schbal")
        st.download_button("⬇️ Download CSV", df_sch.to_csv(index=False).encode(),
                           "amortization_schedule.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════
# TAB 3 — RATE CHANGE SCENARIOS (redesigned: term info, break penalty, misc fees)
# ══════════════════════════════════════════════════════════════════
with tabs[2]:
    st.subheader("📈 Rate Change / Renewal Scenarios")
    b = st.session_state.get("base", {})
    if not b:
        st.warning("Configure your mortgage in **Setup & Overview** first.")
        st.stop()

    st.info(
        "Model future renewals at any date — including **early renewal** (break penalty auto-calculated). "
        "Each renewal entry has its own type, term length, and miscellaneous fees. "
        "Saved scenarios are available in Prepayment Analysis."
    )

    rc_scenarios: dict = st.session_state.rc_scenarios

    if st.button("➕ New Scenario", key="btn_new_rc"):
        nid = str(uuid.uuid4())[:8]
        rc_scenarios[nid] = {"name":f"Scenario {len(rc_scenarios)+1}",
                              "desc":"", "renewals":[]}
        st.rerun()

    if not rc_scenarios:
        st.markdown('<div class="info-box">Click ➕ New Scenario to model a future renewal.</div>',
                    unsafe_allow_html=True)

    sc_del = []
    for sc_id, sc in rc_scenarios.items():
        with st.expander(f"📋 {sc['name']}" + (f" — {sc['desc'][:55]}" if sc['desc'] else ""),
                         expanded=True):
            h1,h2,h3 = st.columns([2,3,1])
            sc["name"] = h1.text_input("Name", sc["name"], key=f"rcn_{sc_id}")
            sc["desc"] = h2.text_input("Description", sc["desc"],
                                       placeholder="e.g. Early renewal at lower rate",
                                       key=f"rcd_{sc_id}")
            if h3.button("🗑️ Delete", key=f"del_sc_{sc_id}"): sc_del.append(sc_id)

            # ── Quick Templates (checkbox — NOT nested expander) ──
            show_tpl = st.checkbox("🚀 Show quick templates", value=False, key=f"tpl_cb_{sc_id}")
            if show_tpl:
                ren_p = int(b["term_years"]*b["n_py"]) + 1
                ren_d = period_to_date(ren_p, b["start_date"], b["n_py"])
                tpls  = {
                    "+1% at renewal":         [{"date":str(ren_d),"rate":b["annual_rate"]+1,"mtype":"Fixed","term_years":3}],
                    "+2% at renewal":         [{"date":str(ren_d),"rate":b["annual_rate"]+2,"mtype":"Fixed","term_years":3}],
                    "-1% at renewal":         [{"date":str(ren_d),"rate":b["annual_rate"]-1,"mtype":"Fixed","term_years":3}],
                    "-2% at renewal":         [{"date":str(ren_d),"rate":b["annual_rate"]-2,"mtype":"Fixed","term_years":3}],
                    "Variable at renewal":    [{"date":str(ren_d),"rate":b["annual_rate"]-0.5,"mtype":"Variable","term_years":3}],
                    "BoC hike then cut":      [
                        {"date":str(period_to_date(ren_p//2,b["start_date"],b["n_py"])),
                         "rate":b["annual_rate"]+2,"mtype":"Fixed","term_years":1},
                        {"date":str(ren_d),"rate":b["annual_rate"]+1,"mtype":"Fixed","term_years":3},
                    ],
                    "Rate stays flat":        [{"date":str(ren_d),"rate":b["annual_rate"],"mtype":"Fixed","term_years":3}],
                }
                tc1,tc2 = st.columns([3,1])
                tpl_sel = tc1.selectbox("Template", list(tpls.keys()), key=f"tpl_{sc_id}")
                if tc2.button("Apply", key=f"apptpl_{sc_id}"):
                    sc["renewals"] = [
                        {"id":str(uuid.uuid4())[:8],"mode":"By Date",
                         "date_str":t["date"],"period":date_to_period(t["date"],b["start_date"],b["n_py"]),
                         "new_rate":t["rate"],"mtype":t["mtype"],"term_years":t["term_years"],
                         "misc_fees":0,"orig_posted":t["rate"]+1.5,"curr_posted":t["rate"]-0.5}
                        for t in tpls[tpl_sel]
                    ]
                    st.rerun()

            st.markdown("---")
            if st.button("➕ Add Renewal Entry", key=f"add_ren_{sc_id}"):
                # Default to end of current term
                default_d = str(b["start_date"] + relativedelta(years=int(b["term_years"]),
                                months=int((b["term_years"]%1)*12)))
                sc["renewals"].append({
                    "id":str(uuid.uuid4())[:8],"mode":"By Date",
                    "date_str":default_d,
                    "period":date_to_period(default_d, b["start_date"], b["n_py"]),
                    "new_rate":b["annual_rate"],"mtype":"Fixed","term_years":3,
                    "misc_fees":0,"orig_posted":b["annual_rate"]+1.5,
                    "curr_posted":b["annual_rate"]-0.5,
                })
                st.rerun()

            # Determine "previous term end" periods for break penalty detection
            # Original term end period
            orig_term_end_period = int(b["term_years"]*b["n_py"])
            # Build the list of term end periods for each renewal
            prev_term_end_periods = [orig_term_end_period]
            for rn in sc["renewals"]:
                prev_end_p = prev_term_end_periods[-1]
                rn_start_p = int(rn.get("period", prev_end_p+1))
                ty = float(rn.get("term_years", 3))
                rn_end_p = rn_start_p + int(ty * b["n_py"])
                prev_term_end_periods.append(rn_end_p)

            # Build base schedule once (for balance lookup)
            df_base_rc, s_base_rc = build_amortization(
                b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                accel=b["accel"], start_date=b["start_date"],
                extra_payments=b.get("past_extra") or None,
                rate_changes=b.get("past_renewal_rcs") or None,
            )

            ren_del = []
            for ri, rn in enumerate(sc["renewals"]):
                rid = rn["id"]
                st.markdown(f"**Renewal {ri+1}**")
                rr = st.columns([1.5,1.8,1.5,1.5,1.5,0.8])

                rn["mode"] = rr[0].radio("Mode", ["By Date","By Period"],
                                         index=0 if rn.get("mode","By Date")=="By Date" else 1,
                                         horizontal=True, key=f"rm_{sc_id}_{rid}")

                if rn["mode"] == "By Date":
                    # Allow any date — including before term end (early renewal)
                    picked = rr[1].date_input("Effective date",
                                              value=date.fromisoformat(rn.get("date_str",
                                                    str(b["start_date"]))),
                                              key=f"rd_{sc_id}_{rid}")
                    rn["date_str"] = str(picked)
                    rn["period"]   = date_to_period(picked, b["start_date"], b["n_py"])
                    rr[1].caption(f"≈ Period {rn['period']}")
                else:
                    max_p = int(b["amort_years"]*b["n_py"])
                    rn["period"] = int(rr[1].number_input("Period #", 1, max_p,
                                                          value=int(rn.get("period", orig_term_end_period+1)),
                                                          key=f"rp_{sc_id}_{rid}"))
                    approx_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
                    rr[1].caption(f"≈ {approx_d.strftime('%b %Y')}")

                rn["mtype"]     = rr[2].selectbox(f"Type", ["Fixed","Variable"],
                                                   index=0 if rn.get("mtype","Fixed")=="Fixed" else 1,
                                                   key=f"rmt_{sc_id}_{rid}")
                rn["new_rate"]  = float(rr[3].number_input("Rate (%)", 0.5, 20.0,
                                                             float(rn.get("new_rate", b["annual_rate"])),
                                                             0.01, format="%.2f",
                                                             key=f"rrt_{sc_id}_{rid}"))
                rn["term_years"] = rr[4].selectbox("Term (yrs)", [0.5,1,2,3,4,5,7,10],
                                                    index=[0.5,1,2,3,4,5,7,10].index(rn.get("term_years",3))
                                                    if rn.get("term_years",3) in [0.5,1,2,3,4,5,7,10] else 3,
                                                    key=f"rty_{sc_id}_{rid}")
                if rr[5].button("🗑️", key=f"delren_{sc_id}_{rid}"): ren_del.append(ri)

                # Show term start / end
                rn_start_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
                rn_end_d   = rn_start_d + relativedelta(years=int(rn["term_years"]),
                                                         months=int((float(rn["term_years"])%1)*12))
                st.caption(f"📅 Term: **{rn_start_d.strftime('%b %d, %Y')}** → **{rn_end_d.strftime('%b %d, %Y')}**")

                # Early renewal detection
                prev_end_p = prev_term_end_periods[ri]   # previous term's end period
                is_early   = rn["period"] < prev_end_p
                months_left_at_renewal = max(int((prev_end_p - rn["period"]) / b["n_py"] * 12), 1)

                if is_early:
                    # Get balance and rate at the renewal period from base schedule
                    rn_period_df = df_base_rc[df_base_rc["Period"] <= rn["period"]]
                    bal_at_ren   = float(rn_period_df["Balance"].iloc[-1]) if not rn_period_df.empty else b["principal"]
                    rate_at_ren  = float(rn_period_df["Rate (%)"].iloc[-1]) if not rn_period_df.empty else b["annual_rate"]

                    st.markdown(
                        f'<div class="warning-box">⚡ <b>Early Renewal Detected</b> — '
                        f'{months_left_at_renewal} months remain in current term. '
                        f'Break penalty applies. Balance at renewal: <b>${bal_at_ren:,.0f}</b></div>',
                        unsafe_allow_html=True)

                    # IRD inputs for break penalty
                    with st.container():
                        bp1,bp2,bp3 = st.columns(3)
                        rn["orig_posted"] = float(bp1.number_input(
                            "Original posted rate (%)", 0.5, 20.0,
                            float(rn.get("orig_posted", rate_at_ren+1.5)),
                            0.01, format="%.2f", key=f"op_{sc_id}_{rid}"))
                        rn["curr_posted"] = float(bp2.number_input(
                            "Current posted rate for remaining term (%)", 0.5, 20.0,
                            float(rn.get("curr_posted", max(rate_at_ren-0.5,0.5))),
                            0.01, format="%.2f", key=f"cp_{sc_id}_{rid}"))
                        rn["misc_fees"] = float(bp3.number_input(
                            "Miscellaneous fees ($)", 0, 50_000,
                            int(rn.get("misc_fees",500)), 50, key=f"mf_{sc_id}_{rid}"))

                        pen = calc_break_penalty(
                            bal_at_ren, rate_at_ren, rn["mtype"],
                            rn["orig_posted"], rn["curr_posted"], months_left_at_renewal)
                        total_cost = pen["penalty"] + rn["misc_fees"]
                        st.markdown(
                            f'<div class="penalty-box">💸 Break penalty: '
                            f'<b>${pen["penalty"]:,.0f}</b> via {pen["method"]} · '
                            f'Misc fees: <b>${rn["misc_fees"]:,.0f}</b> · '
                            f'<b>Total early exit cost: ${total_cost:,.0f}</b></div>',
                            unsafe_allow_html=True)
                        # Show ROI of early renewal (months to recoup)
                        old_pmt = calc_regular_payment(bal_at_ren, rate_at_ren, 12,
                                                       b["amort_years"] - rn["period"]/b["n_py"])
                        new_pmt = calc_regular_payment(bal_at_ren, rn["new_rate"], 12,
                                                       b["amort_years"] - rn["period"]/b["n_py"])
                        if abs(old_pmt - new_pmt) > 1:
                            recoup = total_cost / abs(old_pmt - new_pmt)
                            st.caption(
                                f"Monthly saving: ${abs(old_pmt-new_pmt):,.0f} → "
                                f"Recoup costs in **{recoup:.0f} months** ({recoup/12:.1f} yrs)")
                else:
                    # Normal renewal: just misc fees
                    rn["misc_fees"] = float(st.number_input(
                        "Miscellaneous fees ($) — admin, appraisal, etc.", 0, 50_000,
                        int(rn.get("misc_fees",250)), 50, key=f"mf2_{sc_id}_{rid}"))
                    if rn["misc_fees"] > 0:
                        st.caption(f"Misc fees ${rn['misc_fees']:,.0f} noted (informational).")

                st.markdown("---")

            for ri in sorted(ren_del, reverse=True): sc["renewals"].pop(ri)
            if ren_del: st.rerun()

            # Build rate_changes for this scenario
            rc_list = [{"period":rn["period"],"new_rate":rn["new_rate"]} for rn in sc["renewals"]]
            all_rcs_sc = (b.get("past_renewal_rcs") or []) + rc_list

            df_sc_rc, s_sc_rc = build_amortization(
                b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                accel=b["accel"], start_date=b["start_date"],
                extra_payments=b.get("past_extra") or None,
                rate_changes=all_rcs_sc or None,
            )

            # Results
            m1,m2,m3,m4 = st.columns(4)
            int_d = s_sc_rc.get("total_interest",0) - s_base_rc.get("total_interest",0)
            yr_d  = s_sc_rc.get("payoff_years",0)   - s_base_rc.get("payoff_years",0)
            m1.metric("Base Interest",     f"${s_base_rc.get('total_interest',0):,.0f}")
            m2.metric("Scenario Interest", f"${s_sc_rc.get('total_interest',0):,.0f}",
                      delta=f"${int_d:+,.0f}")
            m3.metric("Base Payoff",       f"{s_base_rc.get('payoff_years',0):.1f} yrs")
            m4.metric("Scenario Payoff",   f"{s_sc_rc.get('payoff_years',0):.1f} yrs",
                      delta=f"{yr_d:+.1f} yrs")

            fig_rc = go.Figure()
            fig_rc.add_scatter(x=df_base_rc["Period"], y=df_base_rc["Balance"],
                               name=f"Base ({b['annual_rate']:.2f}%)", line=dict(color="#1a3c5e"))
            fig_rc.add_scatter(x=df_sc_rc["Period"], y=df_sc_rc["Balance"],
                               name=sc["name"], line=dict(color="#e74c3c", dash="dash"))
            for rn in sc["renewals"]:
                col = "red" if rn["period"] < orig_term_end_period else "orange"
                fig_rc.add_vline(x=rn["period"], line_dash="dot", line_color=col,
                                 annotation_text=f"{rn['new_rate']}%",
                                 annotation_position="top right")
            fig_rc.update_layout(title=f"Balance: Base vs {sc['name']}",
                                  xaxis_title="Period", yaxis_title="Balance ($)", height=320)
            st.plotly_chart(fig_rc, use_container_width=True, key=f"ch_rcbal_{sc_id}")

            if not df_sc_rc.empty:
                fig_rr = px.line(df_sc_rc, x="Period", y="Rate (%)", title="Rate over time",
                                 color_discrete_sequence=["#27ae60"])
                fig_rr.update_layout(height=220)
                st.plotly_chart(fig_rr, use_container_width=True, key=f"ch_rcrate_{sc_id}")

            if st.button("💾 Save scenario", key=f"save_rc_{sc_id}"):
                sc_data = {"type":"rate_change",
                           "params":{**b,"start_date":str(b["start_date"]),
                                     "rate_changes":rc_list,"sc_name":sc["name"],
                                     "sc_desc":sc["desc"]},
                           "summary":s_sc_rc,"rate_changes":rc_list}
                st.session_state.saved_scenarios[sc["name"]] = sc_data
                if st.session_state.db_conn:
                    ok = db_save_scenario(st.session_state.db_conn, sc["name"],
                                          sc_data["params"], sc_data["summary"])
                    st.success(f"Saved to DB: {sc['name']}" if ok else "Saved locally only.")
                else: st.success(f"Saved locally: {sc['name']}")

    for sc_id in sc_del:
        del rc_scenarios[sc_id]
    if sc_del: st.rerun()

# ══════════════════════════════════════════════════════════════════
# TAB 4 — PREPAYMENT ANALYSIS
# ══════════════════════════════════════════════════════════════════
with tabs[3]:
    st.subheader("💰 Prepayment Analysis")
    b = st.session_state.get("base",{})
    if not b:
        st.warning("Configure your mortgage in **Setup & Overview** first."); st.stop()

    freq_n = b["n_py"]
    st.markdown("#### 🎯 Choose Rate Scenario")
    saved_rc = {k:v for k,v in st.session_state.saved_scenarios.items() if v.get("type")=="rate_change"}
    rc_opts  = ["Base Rate — no rate changes"] + list(saved_rc.keys())
    chosen_rc = st.selectbox("Rate scenario base", rc_opts, index=0, key="pp_rc_sel")
    active_rc = [] if chosen_rc=="Base Rate — no rate changes" else saved_rc[chosen_rc].get("rate_changes",[])
    all_rcs_pp = (b.get("past_renewal_rcs") or []) + active_rc

    if active_rc:
        st.markdown(f'<div class="info-box">Using: <b>{chosen_rc}</b> — {len(active_rc)} rate change(s).</div>',
                    unsafe_allow_html=True)
    st.divider()

    col_pp1, col_pp2 = st.columns(2)
    with col_pp1:
        st.markdown("##### 📅 Annual Lump-Sum Prepayments")
        annual_lump = st.number_input("Annual lump-sum ($)", 0, 500_000, 10_000, 500, key="pp_alump")
        lump_month  = st.selectbox("Month each year",
                                   ["Jan","Feb","Mar","Apr","May","Jun",
                                    "Jul","Aug","Sep","Oct","Nov","Dec"],
                                   index=0, key="pp_lmonth")
        lump_start  = st.number_input("Starting year (1=first year)", 1, 30, 1, key="pp_lstart")
        lump_nyrs   = st.number_input("For how many years?", 1, 30, 5, key="pp_lnyrs")
        lmm = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
               "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        lmn = lmm[lump_month]
        future_extra = []
        if annual_lump > 0:
            for yr in range(int(lump_start), int(lump_start+lump_nyrs)):
                p = max(1, int((yr-1)*freq_n + lmn*freq_n/12))
                future_extra.append({"period":p,"amount":float(annual_lump)})
        pp_lim_pct = st.slider("Lender prepayment limit (%)", 10, 30, 20, key="pp_lim")
        if annual_lump > b["principal"]*pp_lim_pct/100:
            st.warning(f"⚠️ Exceeds {pp_lim_pct}% limit (${b['principal']*pp_lim_pct/100:,.0f}).")

    with col_pp2:
        st.markdown("##### 💳 Increased Regular Payments")
        inc_type = st.radio("Increase type",["Fixed $ increase","% increase","None"],
                            index=2, horizontal=True, key="pp_inctype")
        inc_val = 0.0
        if inc_type=="Fixed $ increase":
            inc_val = float(st.number_input("Extra per payment ($)",0,10_000,200,50,key="pp_incfixed"))
        elif inc_type=="% increase":
            inc_pct = st.slider("% increase",1,100,10,key="pp_incpct")
            base_p  = calc_regular_payment(b["principal"],b["annual_rate"],freq_n,b["amort_years"],b["accel"])
            inc_val = base_p * inc_pct / 100
        if inc_val > 0:
            for p in range(1, int(b["amort_years"]*freq_n)+1):
                future_extra.append({"period":p,"amount":inc_val})

        st.markdown("##### 🔁 One-Time Lump Sum")
        ot_mode = st.radio("Entry mode",["By Date","By Period"],horizontal=True,key="pp_otmode")
        if ot_mode=="By Date":
            ot_d = st.date_input("Date",value=b["start_date"]+relativedelta(years=1),
                                 min_value=b["start_date"],key="pp_otdate")
            ot_p = date_to_period(ot_d, b["start_date"], freq_n); st.caption(f"≈ Period {ot_p}")
        else:
            ot_p = int(st.number_input("Period #",1,int(b["amort_years"]*freq_n),
                                       freq_n,key="pp_otperiod"))
        ot_amt = st.number_input("Amount ($)",0,2_000_000,0,1_000,key="pp_otamt")
        if ot_amt>0: future_extra.append({"period":int(ot_p),"amount":float(ot_amt)})

    past_extra = b.get("past_extra",[])
    all_extra  = past_extra + future_extra

    df_base_pp, s_base_pp = build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
        accel=b["accel"],start_date=b["start_date"],extra_payments=past_extra or None)
    df_rsc, s_rsc = build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
        accel=b["accel"],start_date=b["start_date"],extra_payments=past_extra or None,
        rate_changes=all_rcs_pp or None)
    df_pp, s_pp = build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
        accel=b["accel"],start_date=b["start_date"],extra_payments=all_extra or None,
        rate_changes=all_rcs_pp or None)

    st.divider()
    int_saved = s_rsc.get("total_interest",0) - s_pp.get("total_interest",0)
    yrs_saved = s_rsc.get("payoff_years",0)   - s_pp.get("payoff_years",0)
    new_total = sum(e["amount"] for e in future_extra)

    st.markdown(f"#### 📊 Impact *(vs {chosen_rc})*")
    m1,m2,m3,m4,m5 = st.columns(5)
    m1.metric("Interest (rate sc.)",          f"${s_rsc.get('total_interest',0):,.0f}")
    m2.metric("Interest (+ prepayments)",     f"${s_pp.get('total_interest',0):,.0f}",
              delta=f"${-int_saved:+,.0f}")
    m3.metric("Payoff (rate scenario)",        f"{s_rsc.get('payoff_years',0):.1f} yrs")
    m4.metric("Payoff (+ prepayments)",        f"{s_pp.get('payoff_years',0):.1f} yrs",
              delta=f"{-yrs_saved:+.1f} yrs")
    m5.metric("Total New Prepayments",         f"${new_total:,.0f}")

    if int_saved > 0:
        st.markdown(f'<div class="success-box">💚 Prepayments save <b>${int_saved:,.0f}</b> in interest '
                    f'and shorten payoff by <b>{yrs_saved:.1f} years</b>.</div>',unsafe_allow_html=True)

    fig_pp = go.Figure()
    fig_pp.add_scatter(x=df_base_pp["Period"],y=df_base_pp["Balance"],
                       name="Base/No Prepayments",line=dict(color="#aaa",dash="dot"))
    fig_pp.add_scatter(x=df_rsc["Period"],y=df_rsc["Balance"],
                       name=f"{chosen_rc}/No New Prepayments",line=dict(color="#e74c3c"))
    fig_pp.add_scatter(x=df_pp["Period"],y=df_pp["Balance"],
                       name=f"{chosen_rc}+Prepayments",line=dict(color="#27ae60"))
    for ep in future_extra:
        if ep["amount"]==annual_lump and annual_lump>0:
            fig_pp.add_vline(x=ep["period"],line_dash="dot",line_color="#3498db",line_width=1)
    fig_pp.update_layout(title="Outstanding Balance Comparison",
                          xaxis_title="Period",yaxis_title="Balance ($)",height=400)
    st.plotly_chart(fig_pp,use_container_width=True,key="ch_ppbal")

    fig_ci=go.Figure()
    fig_ci.add_scatter(x=df_rsc["Period"],y=df_rsc["Cum Interest"],
                       name="Without New Prepayments",line=dict(color="#e74c3c"))
    fig_ci.add_scatter(x=df_pp["Period"],y=df_pp["Cum Interest"],
                       name="With New Prepayments",line=dict(color="#27ae60"))
    fig_ci.update_layout(title="Cumulative Interest",height=280)
    st.plotly_chart(fig_ci,use_container_width=True,key="ch_ppci")

    if new_total>0 and int_saved>0:
        roi=int_saved/new_total*100
        st.markdown(f'<div class="info-box">For every $1 prepaid → save <b>${int_saved/new_total:.2f}</b>. '
                    f'Prepayment ROI: <b>{roi:.1f}%</b></div>',unsafe_allow_html=True)

    sc_name_pp=st.text_input("Scenario name","Prepayment Scenario",key="pp_scname")
    if st.button("💾 Save Prepayment Scenario",key="btn_save_pp"):
        sc_data={"type":"prepayment","params":{**b,"start_date":str(b["start_date"]),
                 "extra_payments":all_extra,"rate_changes":active_rc,"base_rc":chosen_rc},
                 "summary":s_pp,"rate_changes":active_rc}
        st.session_state.saved_scenarios[sc_name_pp]=sc_data
        if st.session_state.db_conn:
            ok=db_save_scenario(st.session_state.db_conn,sc_name_pp,sc_data["params"],sc_data["summary"])
            st.success(f"Saved to DB: {sc_name_pp}" if ok else "Saved locally only.")
        else: st.success(f"Saved locally: {sc_name_pp}")

# ══════════════════════════════════════════════════════════════════
# TAB 5 — BREAK PENALTY
# ══════════════════════════════════════════════════════════════════
with tabs[4]:
    st.subheader("⚠️ Mortgage Break Penalty Calculator")
    b = st.session_state.get("base",{})
    st.markdown("""
Breaking your mortgage before the term ends results in a penalty:
- **Variable**: 3 months interest
- **Fixed**: Greater of (3 months interest) **or** IRD (Interest Rate Differential)
    """)
    col_bp1,col_bp2=st.columns(2)
    with col_bp1:
        bp_bal   = st.number_input("Outstanding Balance ($)",100,5_000_000,
                                   int(b.get("principal",500_000)*0.85),1_000,key="bp_bal")
        bp_rate  = st.number_input("Contract Rate (%)",0.5,20.0,
                                   float(b.get("annual_rate",5.39)),0.01,format="%.2f",key="bp_rate")
        bp_mtype = st.selectbox("Mortgage Type",["Fixed","Variable"],
                                index=0 if b.get("mortgage_type","Fixed")=="Fixed" else 1,
                                key="bp_mtype_tab5")
        bp_mleft = st.slider("Months Remaining in Term",1,120,36,key="bp_mleft")
        bp_misc  = st.number_input("Miscellaneous Fees ($)",0,50_000,500,50,key="bp_misc",
                                   help="Admin, appraisal, registration, legal fees etc.")

    with col_bp2:
        if bp_mtype=="Fixed":
            st.markdown("##### IRD Inputs")
            st.caption("IRD uses *posted* rates, not your discounted contract rate.")
            bp_orig=st.number_input("Posted Rate at Origination (%)",0.5,20.0,
                                    float(b.get("annual_rate",5.39))+1.5,0.01,format="%.2f",key="bp_orig")
            bp_curr=st.number_input("Current Posted Rate for Remaining Term (%)",0.5,20.0,
                                    max(float(b.get("annual_rate",5.39))-0.5,0.5),0.01,
                                    format="%.2f",key="bp_curr")
            st.markdown('<div class="info-box"><b>IRD</b> = (orig posted − curr posted) × balance × remaining yrs</div>',
                        unsafe_allow_html=True)
        else:
            bp_orig=bp_curr=float(b.get("annual_rate",5.39))

    pen=calc_break_penalty(bp_bal,bp_rate,bp_mtype,bp_orig,bp_curr,bp_mleft)
    total_exit=pen["penalty"]+bp_misc
    st.divider()
    pc1,pc2,pc3,pc4=st.columns(4)
    pc1.metric("3 Months Interest",f"${pen['3_months_interest']:,.2f}")
    if pen["ird"] is not None: pc2.metric("IRD Penalty",f"${pen['ird']:,.2f}")
    pc3.metric("Penalty Owing",f"${pen['penalty']:,.2f}")
    pc4.metric("Total Exit Cost (incl. misc)",f"${total_exit:,.2f}")
    box="penalty-box" if pen["penalty"]>5_000 else "warning-box"
    st.markdown(f'<div class="{box}">⚠️ Method: <b>{pen["method"]}</b> · '
                f'Penalty: <b>${pen["penalty"]:,.2f}</b> · '
                f'Misc fees: <b>${bp_misc:,.2f}</b> · '
                f'<b>Total: ${total_exit:,.2f}</b></div>',unsafe_allow_html=True)

    st.divider()
    st.markdown("#### 📐 Break-Even Analysis")
    new_r=st.slider("New mortgage rate if you break (%)",0.5,15.0,
                    max(float(b.get("annual_rate",5.39))-1.0,0.5),0.05,key="bp_newr")
    amort_rem=max(bp_mleft/12,1)
    _,s_stay=build_amortization(bp_bal,bp_rate,12,amort_rem,term_periods=bp_mleft)
    _,s_brk =build_amortization(bp_bal,new_r,12,amort_rem,term_periods=bp_mleft)
    int_stay=s_stay.get("total_interest",0)
    int_brk =s_brk.get("total_interest",0)+total_exit
    net_sav =int_stay-int_brk
    bc1,bc2,bc3=st.columns(3)
    bc1.metric("Interest (Stay)",     f"${int_stay:,.0f}")
    bc2.metric("Interest+Fees (Break)",f"${int_brk:,.0f}")
    bc3.metric("Net Savings",f"${net_sav:,.0f}",
               delta="✅ Worth breaking" if net_sav>0 else "❌ Not worth it")

    sweep =np.arange(0.5,float(b.get("annual_rate",5.39))+0.11,0.25)
    svlist=[]
    for tr in sweep:
        _,st_=build_amortization(bp_bal,tr,12,amort_rem,term_periods=bp_mleft)
        svlist.append(int_stay-(st_.get("total_interest",0)+total_exit))
    fig_be=go.Figure()
    fig_be.add_scatter(x=list(sweep),y=svlist,mode="lines+markers",
                       line=dict(color="#1a3c5e"),name="Net Savings")
    fig_be.add_hline(y=0,line_dash="dash",line_color="red",annotation_text="Break-even")
    fig_be.add_vline(x=float(b.get("annual_rate",5.39)),line_dash="dot",
                     line_color="orange",annotation_text="Current rate")
    fig_be.update_layout(title="Net Savings vs New Rate",
                          xaxis_title="New Rate (%)",yaxis_title="Net Savings ($)",height=360)
    st.plotly_chart(fig_be,use_container_width=True,key="ch_bpbe")

    old_pmt=calc_regular_payment(bp_bal,bp_rate,12,amort_rem)
    new_pmt=calc_regular_payment(bp_bal,new_r,12,amort_rem)
    if abs(old_pmt-new_pmt)>1:
        st.markdown(f'<div class="info-box">Monthly payment: <b>${old_pmt:,.2f}</b> → '
                    f'<b>${new_pmt:,.2f}</b> (<b>${new_pmt-old_pmt:+,.2f}/mo</b>). '
                    f'Months to recoup all costs: '
                    f'<b>{total_exit/abs(old_pmt-new_pmt):.0f}</b></div>',unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# TAB 6 — SCENARIO COMPARISON
# ══════════════════════════════════════════════════════════════════
with tabs[5]:
    st.subheader("🔄 Side-by-Side Scenario Comparison")
    b=st.session_state.get("base",{})
    if not b:
        st.warning("Configure your mortgage in **Setup & Overview** first."); st.stop()

    n_sc=st.radio("Scenarios",[2,3,4],horizontal=True,key="cmp_n")
    sc_defs=[]
    cols=st.columns(int(n_sc))
    for i,col in enumerate(cols):
        with col:
            st.markdown(f"**Scenario {i+1}**")
            lbl =st.text_input("Label",f"Scenario {i+1}",key=f"cmp_lbl_{i}")
            rate=st.number_input("Rate (%)",0.5,20.0,float(b["annual_rate"])+i*0.5,
                                 0.01,key=f"cmp_rate_{i}",format="%.2f")
            amt =st.slider("Amort (yrs)",5,30,b["amort_years"],key=f"cmp_amt_{i}")
            frq =st.selectbox("Frequency",list(FREQ_CONFIG.keys()),
                              index=list(FREQ_CONFIG.keys()).index(b["payment_freq"]),
                              key=f"cmp_frq_{i}")
            lump=st.number_input("Annual lump ($)",0,200_000,0,1_000,key=f"cmp_lump_{i}")
            fc=FREQ_CONFIG[frq];ny=fc["n"];ac=fc["accel"]
            ex=list(b.get("past_extra",[]))
            if lump>0:
                for yr in range(1,amt+1):
                    ex.append({"period":max(1,int((yr-1)*ny+ny//2)),"amount":float(lump)})
            df_c,s_c=build_amortization(b["principal"],rate,ny,amt,accel=ac,
                                         start_date=b["start_date"],
                                         extra_payments=ex or None,
                                         rate_changes=b.get("past_renewal_rcs") or None)
            pmt_c=calc_regular_payment(b["principal"],rate,ny,amt,ac)
            sc_defs.append({"label":lbl,"rate":rate,"amort":amt,"freq":frq,
                            "lump":lump,"df":df_c,"summary":s_c,"payment":pmt_c})

    st.divider()
    st.markdown("#### 📋 Comparison Summary")
    comp_rows=[]
    for sc in sc_defs:
        s=sc["summary"]
        comp_rows.append({"Scenario":sc["label"],"Rate":f"{sc['rate']:.2f}%",
                           "Amortization":f"{sc['amort']} yrs","Frequency":sc["freq"],
                           "Annual Lump":f"${sc['lump']:,.0f}","Payment":f"${sc['payment']:,.2f}",
                           "Total Interest":f"${s.get('total_interest',0):,.0f}",
                           "Total Paid":f"${s.get('total_paid',0):,.0f}",
                           "Payoff":f"{s.get('payoff_years',0):.1f} yrs"})
    st.dataframe(pd.DataFrame(comp_rows),use_container_width=True)

    pal=["#1a3c5e","#e74c3c","#27ae60","#f39c12"]
    fig_c=go.Figure()
    for i,sc in enumerate(sc_defs):
        if not sc["df"].empty:
            fig_c.add_scatter(x=sc["df"]["Period"],y=sc["df"]["Balance"],
                              name=sc["label"],line=dict(color=pal[i]))
    fig_c.update_layout(title="Balance Comparison",xaxis_title="Period",
                         yaxis_title="($)",height=360)
    st.plotly_chart(fig_c,use_container_width=True,key="ch_cmpbal")

    fig_ic=go.Figure(go.Bar(
        x=[sc["label"] for sc in sc_defs],
        y=[sc["summary"].get("total_interest",0) for sc in sc_defs],
        marker_color=pal[:len(sc_defs)],
        text=[f"${sc['summary'].get('total_interest',0):,.0f}" for sc in sc_defs],
        textposition="outside"))
    fig_ic.update_layout(title="Total Interest",yaxis_title="($)",height=320)
    st.plotly_chart(fig_ic,use_container_width=True,key="ch_cmpint")

    fig_pie=make_subplots(rows=1,cols=len(sc_defs),
        subplot_titles=[sc["label"] for sc in sc_defs],
        specs=[[{"type":"pie"}]*len(sc_defs)])
    for i,sc in enumerate(sc_defs):
        s=sc["summary"]
        fig_pie.add_trace(go.Pie(labels=["Principal","Interest","Prepayment"],
            values=[s.get("total_principal",0),s.get("total_interest",0),s.get("total_prepaid",0)],
            marker_colors=["#1a3c5e","#e74c3c","#3498db"],hole=0.4),row=1,col=i+1)
    fig_pie.update_layout(title="Payment Composition",height=320,showlegend=False)
    st.plotly_chart(fig_pie,use_container_width=True,key="ch_cmppie")

    best=min(range(len(sc_defs)),key=lambda i:sc_defs[i]["summary"].get("total_interest",1e12))
    worst_i=max(sc["summary"].get("total_interest",0) for sc in sc_defs)
    st.markdown(f'<div class="success-box">🏆 <b>{sc_defs[best]["label"]}</b> saves '
                f'${worst_i-sc_defs[best]["summary"].get("total_interest",0):,.0f} vs costliest option.</div>',
                unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# TAB 7 — SAVED SCENARIOS
# ══════════════════════════════════════════════════════════════════
with tabs[6]:
    st.subheader("💾 Saved Scenarios")
    db_sc  =db_load_scenarios(st.session_state.db_conn)
    loc_sc =st.session_state.get("saved_scenarios",{})
    all_sc:dict={}
    for s in db_sc:
        all_sc[f"[DB] {s['name']} ({s['created_at'][:16]})"]=s
    for nm,s in loc_sc.items():
        all_sc[f"[Local] {nm}"]={"name":nm,"params":s.get("params",{}),"summary":s.get("summary",{})}

    if not all_sc:
        st.info("No saved scenarios. Use 💾 Save buttons in other tabs.")
    else:
        st.markdown(f"**{len(all_sc)} scenario(s)**")
        for key,sc in all_sc.items():
            with st.expander(key):
                s=sc["summary"]
                cc=st.columns(4)
                cc[0].metric("Total Interest",f"${s.get('total_interest',0):,.0f}")
                cc[1].metric("Payoff",f"{s.get('payoff_years',0):.1f} yrs")
                cc[2].metric("Payment",f"${s.get('payment',0):,.2f}")
                cc[3].metric("Total Prepaid",f"${s.get('total_prepaid',0):,.0f}")
                show_raw = st.checkbox("Show raw params", value=False, key=f"raw_{hash(key)}")
                if show_raw: st.json(sc.get("params",{}))
                if "id" in sc:
                    if st.button("🗑️ Delete from DB",key=f"del_db_{sc['id']}"):
                        db_delete_scenario(st.session_state.db_conn,sc["id"]); st.rerun()

        if st.button("⬇️ Export as CSV",key="btn_exp"):
            rows_e=[{"Scenario":k,**sc.get("summary",{})} for k,sc in all_sc.items()]
            st.download_button("Download CSV",pd.DataFrame(rows_e).to_csv(index=False).encode(),
                               "scenarios.csv","text/csv",key="btn_dl_csv")

    st.divider()
    st.markdown("### 📚 Canadian Mortgage Education")
    with st.expander("🔢 How Canadian Mortgage Math Works"):
        st.markdown("""
**Semi-Annual Compounding** (Interest Act requirement)
- Rate 5.39% → Effective annual: (1 + 0.0539/2)² = **5.463%**
- Monthly periodic: 1.05463^(1/12) - 1 = **0.4453%**

**Accelerated Bi-Weekly**: monthly ÷ 2, paid every 2 weeks → ~1 extra monthly payment/yr → ~3 fewer years on a 25-yr mortgage
        """)
    with st.expander("🛡️ CMHC Mortgage Insurance"):
        st.markdown("""
Required: down < 20% and price ≤ $1,500,000

| Down Payment | Rate  | |Down Payment|Rate|
|---|---|---|---|---|
| 5–9.99%     | 4.00% || 15–19.99%  |2.80%|
| 10–14.99%   | 3.10% || ≥ 20%      |0%  |

PST/HST charged on premium — payable upfront (e.g. 13% in Ontario).
        """)
    with st.expander("💔 Breaking Your Mortgage"):
        st.markdown("""
**Variable**: 3 months interest on outstanding balance.

**Fixed**: max(3 months interest, IRD)
- IRD = (your *posted* rate − current posted rate for remaining term) × balance × years remaining
- Banks use posted rates (not your discounted rate) — inflates the IRD

**Open mortgages**: no penalty, ~1% rate premium.

**Early renewal math**: penalty + misc fees / monthly payment saving = months to break even
        """)
