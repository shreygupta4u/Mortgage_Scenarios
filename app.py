"""
Canadian Mortgage Analyzer — Streamlit App v5
Changes from v4:
1. DB-first mandatory gate — no content until connected; auto-load saved setup
2. Single setup row — no setup name, one row per installation
3. No custom payment override — show calculated payment + three-layer stacked bar P&I chart
4. Prompt as downloadable text file only (not on screen)
5. Amortization schedule defaults to current date (auto-scroll to today's row)
6. Advisory penalty: radio between 3-Month Interest, IRD, Custom Value
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
import json, math, uuid

# ──────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ──────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🏠 Canadian Mortgage Analyzer",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────────
# GLOBAL CSS
# ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.main-header{font-size:2.1rem;font-weight:700;color:#1a3c5e;margin-bottom:.1rem;}
.sub-header {font-size:.9rem;color:#555;margin-bottom:1.2rem;}
.mc {background:#f0f4f8;border-radius:9px;padding:.7rem 1.1rem;
     border-left:4px solid #1a3c5e;margin-bottom:.4rem;}
.mc h3{margin:0;font-size:.72rem;color:#666;text-transform:uppercase;letter-spacing:.05em;}
.mc p {margin:0;font-size:1.25rem;font-weight:700;color:#1a3c5e;}
.mc-g{border-left:4px solid #27ae60;}.mc-g p{color:#27ae60;}
.mc-r{border-left:4px solid #e74c3c;}.mc-r p{color:#c0392b;}
.warn{background:#fff3cd;border:1px solid #ffc107;border-radius:7px;
      padding:.6rem .9rem;margin:3px 0;}
.ok  {background:#d4edda;border:1px solid #28a745;border-radius:7px;
      padding:.6rem .9rem;margin:3px 0;}
.inf {background:#cce5ff;border:1px solid #004085;border-radius:7px;
      padding:.6rem .9rem;margin:3px 0;}
.pen {background:#f8d7da;border:1px solid #f5c6cb;border-radius:7px;
      padding:.6rem .9rem;margin:3px 0;}
.db-gate{max-width:480px;margin:80px auto;padding:2rem;
         background:#f8fafc;border:1px solid #d0d7de;border-radius:12px;
         box-shadow:0 4px 20px rgba(0,0,0,.08);}
</style>""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────
def _vline_x(v):
    """Convert date to ms-epoch for Plotly add_vline."""
    try:
        import pandas as _pd
        return int(_pd.Timestamp(v).timestamp() * 1000)
    except Exception:
        return v


# ──────────────────────────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────────────────────────
def get_db_connection(server, database, trusted, user="", pwd=""):
    try:
        import pyodbc
        cs = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server};DATABASE={database};"
            + ("Trusted_Connection=yes;" if trusted
               else f"UID={user};PWD={pwd};")
        )
        conn = pyodbc.connect(cs, timeout=5)
        _init_db(conn)
        return conn, None
    except Exception as e:
        return None, str(e)


def _init_db(conn):
    c = conn.cursor()
    c.execute("""
      IF NOT EXISTS(SELECT*FROM sysobjects WHERE name='mortgage_setup' AND xtype='U')
      CREATE TABLE mortgage_setup(
        id INT IDENTITY PRIMARY KEY,
        saved_at DATETIME DEFAULT GETDATE(),
        setup_data NVARCHAR(MAX))""")
    c.execute("""
      IF NOT EXISTS(SELECT*FROM sysobjects WHERE name='mortgage_scenarios' AND xtype='U')
      CREATE TABLE mortgage_scenarios(
        id INT IDENTITY PRIMARY KEY,name NVARCHAR(200),
        created_at DATETIME DEFAULT GETDATE(),
        params NVARCHAR(MAX),summary NVARCHAR(MAX))""")
    conn.commit()


def db_load_setup(conn):
    """Return single setup dict or None."""
    if not conn: return None
    try:
        c = conn.cursor()
        c.execute("SELECT TOP 1 setup_data FROM mortgage_setup ORDER BY id DESC")
        row = c.fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def db_save_setup(conn, data):
    """Upsert single setup row."""
    if not conn: return False
    try:
        c = conn.cursor()
        c.execute("DELETE FROM mortgage_setup")
        c.execute("INSERT INTO mortgage_setup(setup_data) VALUES(?)",
                  json.dumps(data, default=str))
        conn.commit()
        return True
    except Exception:
        return False


def db_save_scenario(conn, name, params, summary):
    if not conn: return False
    try:
        conn.cursor().execute(
            "INSERT INTO mortgage_scenarios(name,params,summary)VALUES(?,?,?)",
            name, json.dumps(params), json.dumps(summary))
        conn.commit(); return True
    except Exception: return False


def db_load_scenarios(conn):
    if not conn: return []
    try:
        c = conn.cursor()
        c.execute("SELECT id,name,created_at,params,summary "
                  "FROM mortgage_scenarios ORDER BY created_at DESC")
        return [{"id":r[0],"name":r[1],"created_at":str(r[2]),
                 "params":json.loads(r[3]),"summary":json.loads(r[4])}
                for r in c.fetchall()]
    except Exception: return []


def db_delete_scenario(conn, sid):
    if not conn: return
    try:
        conn.cursor().execute("DELETE FROM mortgage_scenarios WHERE id=?", sid)
        conn.commit()
    except Exception: pass


# ──────────────────────────────────────────────────────────────────
# MORTGAGE MATH
# ──────────────────────────────────────────────────────────────────
FREQ = {
    "Monthly":               {"n": 12, "accel": False},
    "Semi-Monthly":          {"n": 24, "accel": False},
    "Bi-Weekly":             {"n": 26, "accel": False},
    "Accelerated Bi-Weekly": {"n": 26, "accel": True},
    "Weekly":                {"n": 52, "accel": False},
    "Accelerated Weekly":    {"n": 52, "accel": True},
}


def periodic_rate(annual_pct, n):
    eff = (1 + annual_pct / 200) ** 2
    return eff ** (1 / n) - 1


def calc_pmt(principal, annual_pct, n, amort_years, accel=False):
    if annual_pct == 0:
        t = amort_years * n
        return principal / t if t else 0
    r = periodic_rate(annual_pct, n)
    np_ = amort_years * n
    pmt = principal * r * (1+r)**np_ / ((1+r)**np_ - 1)
    if accel:
        rm = periodic_rate(annual_pct, 12)
        nm = amort_years * 12
        pmt = (principal * rm * (1+rm)**nm / ((1+rm)**nm - 1)) / (n / 12)
    return pmt


def cmhc_premium(price, down):
    dp = down / price * 100
    if dp >= 20: return 0.0, 0.0
    if price > 1_500_000 or dp < 5: return None, None
    ins = price - down
    rt = 0.04 if dp < 10 else (0.031 if dp < 15 else 0.028)
    p = ins * rt
    return p, p * 0.13


def date_to_period(td, sd, n):
    if isinstance(td, str): td = date.fromisoformat(td)
    if isinstance(sd, str): sd = date.fromisoformat(sd)
    return max(1, int(round((td - sd).days / 365.25 * n)))


def period_to_date(period, sd, n):
    if isinstance(sd, str): sd = date.fromisoformat(sd)
    if n == 12:  return sd + relativedelta(months=int(period-1))
    if n == 24:  return sd + relativedelta(days=int((period-1)*15))
    if n == 26:  return sd + relativedelta(weeks=int((period-1)*2))
    return sd + relativedelta(weeks=int(period-1))


def calc_remaining_years(balance, rate_pct, n, payment):
    if payment <= 0 or balance <= 0: return 0.0
    r = periodic_rate(rate_pct, n)
    if r == 0: return balance / (payment * n)
    denom = payment - balance * r
    if denom <= 0.01: return 999.0
    return math.log(payment / denom) / math.log(1 + r) / n


def build_amortization(principal, annual_pct, n, amort_years,
                       accel=False, start_date=None,
                       extra_payments=None, rate_changes=None,
                       term_periods=None):
    if start_date is None: start_date = date.today().replace(day=1)
    if isinstance(start_date, str): start_date = date.fromisoformat(start_date)

    pmt = calc_pmt(principal, annual_pct, n, amort_years, accel)
    r   = periodic_rate(annual_pct, n)

    em = {}
    if extra_payments:
        for ep in extra_payments:
            p = int(ep["period"])
            em[p] = em.get(p, 0) + float(ep["amount"])

    rm = {}
    if rate_changes:
        for rc in rate_changes: rm[int(rc["period"])] = float(rc["new_rate"])

    rows = []; bal = float(principal); tp = amort_years * n
    cr = float(annual_pct); cur_r = r
    ci = cp = cprep = 0.0; pd_ = start_date

    for i in range(1, int(tp) + 1):
        if bal <= 0.005: break
        if term_periods and i > term_periods: break
        if i in rm:
            cr = rm[i]; cur_r = periodic_rate(cr, n)
            pmt = calc_pmt(bal, cr, n, (tp - i + 1) / n, accel)
        int_c = bal * cur_r
        princ = min(max(pmt - int_c, 0), bal)
        extra = min(em.get(i, 0), max(bal - princ, 0))
        bal  -= princ + extra
        ci += int_c; cp += pmt; cprep += extra

        rows.append({
            "Period": i, "Date": pd_, "Year": ((i-1)//n)+1,
            "Payment": round(pmt, 2), "Interest": round(int_c, 2),
            "Principal": round(princ, 2), "Prepayment": round(extra, 2),
            "Total Paid": round(pmt + extra, 2), "Balance": round(max(bal, 0), 2),
            "Rate (%)": round(cr, 3), "Cum Interest": round(ci, 2),
            "Cum Principal": round(cp - ci, 2), "Cum Prepayment": round(cprep, 2),
        })
        if   n == 12: pd_ += relativedelta(months=1)
        elif n == 24: pd_ += relativedelta(days=15)
        elif n == 26: pd_ += relativedelta(weeks=2)
        else:         pd_ += relativedelta(weeks=1)

    df = pd.DataFrame(rows)
    if df.empty: return df, {}
    ti = df["Interest"].sum(); tprep = df["Prepayment"].sum()
    tt = df["Payment"].sum() + tprep
    return df, {"payment": round(pmt, 2), "total_paid": round(tt, 2),
                "total_interest": round(ti, 2),
                "total_principal": round(df["Principal"].sum(), 2),
                "total_prepaid": round(tprep, 2),
                "end_balance": round(df["Balance"].iloc[-1], 2),
                "payoff_periods": len(df), "payoff_years": round(len(df)/n, 2),
                "interest_pct": round(ti/tt*100, 1) if tt else 0}


def get_today_metrics(df, n):
    today = date.today()
    if df.empty: return {}
    def tod(v): return v if isinstance(v, date) else (
        v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10]))
    past = df[df["Date"].apply(tod) <= today]
    if past.empty:
        return {"balance_today": float(df.iloc[0]["Balance"]),
                "principal_paid_today": 0.0, "interest_paid_today": 0.0,
                "period_today": 0, "remaining_years": round(len(df)/n, 1),
                "as_of_date": today.strftime("%b %d, %Y")}
    row = past.iloc[-1]
    return {"balance_today":   float(row["Balance"]),
            "principal_paid_today": float(row["Cum Principal"]),
            "interest_paid_today":  float(row["Cum Interest"]),
            "period_today":    int(row["Period"]),
            "remaining_years": round((len(df) - int(row["Period"])) / n, 1),
            "as_of_date":      tod(row["Date"]).strftime("%b %d, %Y")}


def calc_break_penalty(bal, rate, mtype, orig_p, curr_p, months_left):
    mr = periodic_rate(rate, 12)
    tmo = bal * mr * 3
    if mtype == "Variable":
        return {"3_months_interest": round(tmo, 2), "ird": None,
                "calc_penalty": round(tmo, 2), "method": "3 months interest (variable)"}
    ird = max(bal * (orig_p - curr_p) / 100 * months_left / 12, 0)
    pen = max(tmo, ird)
    return {"3_months_interest": round(tmo, 2), "ird": round(ird, 2),
            "calc_penalty": round(pen, 2),
            "method": "IRD" if ird > tmo else "3 months interest"}


def stacked_bar_pi(df, today_p, term_end_p, title="Principal & Interest Breakdown"):
    """Three-segment stacked bar: past (grey), current-term (blue/red), post-term (faded)."""
    # Aggregate by year for readability
    df2 = df.copy()
    df2["Segment"] = "post"
    df2.loc[df2["Period"] <= today_p, "Segment"] = "past"
    df2.loc[(df2["Period"] > today_p) & (df2["Period"] <= term_end_p), "Segment"] = "current"

    yr_data = []
    for seg, col_p, col_i, opacity, label_sfx in [
        ("past",    "#999999", "#cccccc", 0.9, "(past)"),
        ("current", "#1a3c5e", "#e74c3c", 1.0, "(current term)"),
        ("post",    "#8ba7c7", "#e8a5a0", 0.6, "(projected)"),
    ]:
        seg_df = df2[df2["Segment"] == seg]
        if seg_df.empty: continue
        g = seg_df.groupby("Year").agg(Principal=("Principal","sum"),
                                        Interest=("Interest","sum"),
                                        Date=("Date","first")).reset_index()
        yr_data.append((seg, g, col_p, col_i, opacity, label_sfx))

    fig = go.Figure()
    for seg, g, col_p, col_i, opac, sfx in yr_data:
        fig.add_bar(name=f"Principal {sfx}", x=g["Year"], y=g["Principal"],
                    marker_color=col_p, opacity=opac, legendgroup=seg,
                    text=g["Principal"].apply(lambda v: f"${v:,.0f}"),
                    textposition="inside", textfont_size=9)
        fig.add_bar(name=f"Interest {sfx}", x=g["Year"], y=g["Interest"],
                    marker_color=col_i, opacity=opac, legendgroup=seg,
                    text=g["Interest"].apply(lambda v: f"${v:,.0f}"),
                    textposition="inside", textfont_size=9)

    # Mark segments
    if today_p > 0:
        today_yr = df[df["Period"] == today_p]["Year"].iloc[0] if (df["Period"] == today_p).any() else None
        if today_yr:
            fig.add_vline(x=today_yr - 0.5, line_dash="dash",
                          line_color="#27ae60", annotation_text="Today")
    if term_end_p > 0 and term_end_p < len(df):
        te_yr = df[df["Period"] == min(term_end_p, len(df)-1)]["Year"].iloc[0]
        fig.add_vline(x=te_yr - 0.5, line_dash="dot",
                      line_color="orange", annotation_text="Term end")

    fig.update_layout(barmode="stack", title=title,
                      xaxis_title="Year", yaxis_title="($)",
                      height=400, legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


# ──────────────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────────────
for k, v in {
    "db_conn": None, "db_error": None,
    "setup_loaded": False, "setup_data": None,
    "saved_scenarios": {}, "rc_scenarios": {},
    "past_prepayments": [], "past_renewals": [],
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════
# GATE 1: DATABASE CONNECTION (full-page form if not connected)
# ══════════════════════════════════════════════════════════════════
if not st.session_state.db_conn:
    st.markdown('<div class="main-header">🏠 Canadian Mortgage Analyzer</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Please connect to your SQL Server database to continue.</div>',
                unsafe_allow_html=True)

    with st.container():
        col_c, col_m, col_c2 = st.columns([1, 2, 1])
        with col_m:
            st.markdown('<div class="db-gate">', unsafe_allow_html=True)
            st.markdown("### 🗄️ Database Connection")
            srv = st.text_input("SQL Server", r"localhost\SQLEXPRESS", key="g_srv")
            db  = st.text_input("Database",   "MortgageDB",            key="g_db")
            tru = st.checkbox("Windows Authentication", True,           key="g_tru")
            if not tru:
                usr = st.text_input("Username", key="g_usr")
                pwd = st.text_input("Password", type="password", key="g_pwd")
            else:
                usr = pwd = ""

            if st.button("🔌 Connect & Continue", use_container_width=True, key="btn_gate_conn"):
                conn, err = get_db_connection(srv, db, tru, usr, pwd)
                if conn:
                    st.session_state.db_conn = conn
                    # Try to load existing setup
                    existing = db_load_setup(conn)
                    if existing:
                        st.session_state.setup_data   = existing
                        st.session_state.setup_loaded = True
                        if "past_renewals"    in existing: st.session_state.past_renewals    = existing["past_renewals"]
                        if "past_prepayments" in existing: st.session_state.past_prepayments = existing["past_prepayments"]
                    st.rerun()
                else:
                    st.error(f"❌ Could not connect: {err}")

            st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ──────────────────────────────────────────────────────────────────
# Load setup from DB if just connected and data exists
# ──────────────────────────────────────────────────────────────────
if not st.session_state.setup_loaded:
    existing = db_load_setup(st.session_state.db_conn)
    if existing:
        st.session_state.setup_data   = existing
        st.session_state.setup_loaded = True
        if "past_renewals"    in existing: st.session_state.past_renewals    = existing.get("past_renewals", [])
        if "past_prepayments" in existing: st.session_state.past_prepayments = existing.get("past_prepayments", [])

setup_complete = st.session_state.setup_loaded and st.session_state.get("base")

# ──────────────────────────────────────────────────────────────────
# HEADER
# ──────────────────────────────────────────────────────────────────
hc1, hc2 = st.columns([4, 1])
hc1.markdown('<div class="main-header">🏠 Canadian Mortgage Analyzer</div>',
             unsafe_allow_html=True)
hc1.markdown('<div class="sub-header">Canadian semi-annual compounding · '
             'CMHC · Multi-term · Prepayments · Rate scenarios · Break penalties</div>',
             unsafe_allow_html=True)

# DB status indicator (top right)
hc2.markdown(f"<div style='text-align:right;padding-top:1.2rem;color:#27ae60;font-size:.85rem;'>"
             f"🟢 DB Connected</div>", unsafe_allow_html=True)

tabs = st.tabs([
    "📊 Setup & Overview",
    "📅 Amortization Schedule",
    "📈 Rate Change Scenarios",
    "💰 Prepayment Analysis",
    "⚠️ Break Penalty",
    "🔄 Scenario Comparison",
    "💾 Saved Scenarios",
])

# ══════════════════════════════════════════════════════════════════
# TAB 1 — SETUP & OVERVIEW
# ══════════════════════════════════════════════════════════════════
with tabs[0]:
    st.subheader("Mortgage Setup")
    sd = st.session_state.setup_data or {}  # existing DB values or {}

    # ── Helpers to pull saved value or fall back to default ──────
    def sv(key, default):
        """Get saved value from DB or default."""
        ws = sd.get("widget_state", {})
        return ws.get(key, default)

    cl, cr = st.columns([1.2, 1])
    with cl:
        st.markdown("#### 🏡 Property & Down Payment")
        a1, a2 = st.columns(2)
        purchase_price = a1.number_input(
            "Purchase Price ($)", 100_000, 5_000_000,
            int(sv("s_price", 1_030_000)), 5_000, format="%d", key="s_price")
        down_pct = a2.slider("Down Payment (%)", 5.0, 50.0,
                             float(sv("s_dpct", 20.0)), 0.5, key="s_dpct")
        down_pay = purchase_price * down_pct / 100
        a1.metric("Down Payment", f"${down_pay:,.0f}")

        cmhc, hst = cmhc_premium(purchase_price, down_pay)
        if cmhc is None:
            st.markdown('<div class="warn">⚠️ CMHC not available (price >$1.5M or down <5%)</div>',
                        unsafe_allow_html=True)
            insured_p = purchase_price - down_pay
        elif cmhc == 0:
            st.markdown('<div class="ok">✅ No CMHC premium — down payment ≥ 20%</div>',
                        unsafe_allow_html=True)
            insured_p = purchase_price - down_pay
        else:
            add_c = a2.checkbox("Add CMHC to mortgage?", bool(sv("s_addcmhc", True)),
                                key="s_addcmhc")
            st.markdown(
                f'<div class="inf">🛡️ CMHC: <b>${cmhc:,.0f}</b> '
                f'(+HST ~${hst:,.0f}) · {cmhc/(purchase_price-down_pay)*100:.2f}%</div>',
                unsafe_allow_html=True)
            insured_p = (purchase_price - down_pay) + (cmhc if add_c else 0)

        st.markdown("#### 💵 Mortgage Terms")
        b1, b2 = st.columns(2)
        mortgage_type = b1.selectbox("Mortgage Type", ["Fixed", "Variable"],
                                     index=["Fixed","Variable"].index(sv("s_mtype","Fixed")),
                                     key="s_mtype")
        payment_freq  = b2.selectbox("Payment Frequency", list(FREQ.keys()),
                                     index=list(FREQ.keys()).index(sv("s_freq","Monthly")),
                                     key="s_freq")
        annual_rate   = b1.number_input("Interest Rate (%)", 0.5, 20.0,
                                        float(sv("s_rate", 5.39)), 0.01,
                                        format="%.2f", key="s_rate")
        amort_years   = b2.slider("Amortization (years)", 5, 30,
                                  int(sv("s_amort", 30)), key="s_amort")
        term_opts     = [0.5, 1, 2, 3, 4, 5, 7, 10]
        term_years    = b1.selectbox("Term (years)", term_opts,
                                     index=term_opts.index(sv("s_term", 3))
                                     if sv("s_term", 3) in term_opts else 3,
                                     key="s_term")
        # Start date — parse from string if from DB
        _sd_raw = sv("s_startdate", "2023-08-15")
        _sd_val = date.fromisoformat(_sd_raw) if isinstance(_sd_raw, str) else _sd_raw
        start_date_in = b2.date_input("Mortgage Start Date", _sd_val, key="s_startdate")

        if down_pct < 20 and amort_years > 25:
            st.markdown('<div class="warn">⚠️ Insured mortgages limited to 25-yr amortization.</div>',
                        unsafe_allow_html=True)

    fc = FREQ[payment_freq]; n_py = fc["n"]; accel = fc["accel"]

    # ── Past Renewals ─────────────────────────────────────────────
    with cl:
        st.markdown("#### 🔄 Past Renewal Terms")
        st.caption("Add any renewal terms already taken effect since mortgage start.")
        if st.button("➕ Add Past Renewal", key="btn_add_rn"):
            if st.session_state.past_renewals:
                last = st.session_state.past_renewals[-1]
                prev_end = date.fromisoformat(last["start_date_str"]) + \
                           relativedelta(years=int(last["term_years"]),
                                         months=int((float(last["term_years"]) % 1)*12))
            else:
                prev_end = start_date_in + relativedelta(
                    years=int(term_years), months=int((term_years % 1)*12))
            st.session_state.past_renewals.append({
                "id": str(uuid.uuid4())[:8], "start_date_str": str(prev_end),
                "rate": annual_rate, "mtype": "Fixed", "term_years": 3})
            st.rerun()

        del_rn = []
        for idx, rn in enumerate(st.session_state.past_renewals):
            rr = st.columns([2, 1.5, 1.5, 1.5, 0.7])
            nsd = rr[0].date_input(f"Start #{idx+1}",
                                   date.fromisoformat(rn["start_date_str"]),
                                   key=f"rn_sd_{rn['id']}")
            nr  = rr[1].number_input(f"Rate #{idx+1} (%)", 0.5, 20.0,
                                     float(rn["rate"]), 0.01,
                                     format="%.2f", key=f"rn_rt_{rn['id']}")
            nmt = rr[2].selectbox(f"Type #{idx+1}", ["Fixed","Variable"],
                                  index=0 if rn["mtype"]=="Fixed" else 1,
                                  key=f"rn_mt_{rn['id']}")
            nty = rr[3].selectbox(f"Term #{idx+1}", term_opts,
                                  index=term_opts.index(rn["term_years"])
                                  if rn["term_years"] in term_opts else 3,
                                  key=f"rn_ty_{rn['id']}")
            if rr[4].button("🗑️", key=f"del_rn_{rn['id']}"): del_rn.append(idx)
            end_d = date.fromisoformat(str(nsd)) + relativedelta(
                years=int(nty), months=int((float(nty)%1)*12))
            rr[0].caption(f"End: {end_d.strftime('%b %Y')}")
            st.session_state.past_renewals[idx].update(
                start_date_str=str(nsd), rate=float(nr), mtype=nmt, term_years=nty)
        for i in sorted(del_rn, reverse=True): st.session_state.past_renewals.pop(i)
        if del_rn: st.rerun()

    past_renewal_rcs = [
        {"period": date_to_period(rn["start_date_str"], start_date_in, n_py),
         "new_rate": float(rn["rate"])}
        for rn in st.session_state.past_renewals]

    # ── Past Prepayments ──────────────────────────────────────────
    with cl:
        st.markdown("#### 💳 Past Prepayments Already Made")
        if st.button("➕ Add Past Prepayment", key="btn_add_pp"):
            st.session_state.past_prepayments.append(
                {"id": str(uuid.uuid4())[:8], "date_str": str(start_date_in),
                 "amount": 0.0})
            st.rerun()
        del_pp = []
        for idx, pp in enumerate(st.session_state.past_prepayments):
            r = st.columns([2, 2, 1])
            nd = r[0].date_input(f"Date #{idx+1}",
                                  date.fromisoformat(pp["date_str"]),
                                  min_value=start_date_in, max_value=date.today(),
                                  key=f"ppd_{pp['id']}")
            na = r[1].number_input(f"Amount ($) #{idx+1}", 0, 2_000_000,
                                   int(pp["amount"]), 500, key=f"ppa_{pp['id']}")
            if r[2].button("🗑️", key=f"del_pp_{pp['id']}"): del_pp.append(idx)
            st.session_state.past_prepayments[idx].update(
                date_str=str(nd), amount=float(na))
        for i in sorted(del_pp, reverse=True): st.session_state.past_prepayments.pop(i)
        if del_pp: st.rerun()

    past_extra = [
        {"period": date_to_period(pp["date_str"], start_date_in, n_py),
         "amount": float(pp["amount"])}
        for pp in st.session_state.past_prepayments if pp["amount"] > 0]

    # Build schedules (with past_extra for correct today metrics)
    full_df, full_sum = build_amortization(
        insured_p, annual_rate, n_py, amort_years,
        accel=accel, start_date=start_date_in,
        extra_payments=past_extra or None,
        rate_changes=past_renewal_rcs or None)
    today_m = get_today_metrics(full_df, n_py)

    # ── Right column: Key Metrics ─────────────────────────────────
    with cr:
        pmt_a = calc_pmt(insured_p, annual_rate, n_py, amort_years, accel)
        term_end_d = start_date_in + relativedelta(
            years=int(term_years), months=int((term_years%1)*12))
        _, t_sum = build_amortization(
            insured_p, annual_rate, n_py, amort_years,
            accel=accel, start_date=start_date_in,
            extra_payments=past_extra or None,
            rate_changes=past_renewal_rcs or None,
            term_periods=int(term_years * n_py))

        st.markdown("#### 📊 Key Metrics at a Glance")
        st.markdown(f"""
        <div class="mc"><h3>Mortgage Principal</h3><p>${insured_p:,.0f}</p></div>
        <div class="mc"><h3>{payment_freq} Payment</h3><p>${pmt_a:,.2f}</p></div>
        <div class="mc"><h3>Balance at Term End ({term_end_d.strftime('%b %Y')})</h3>
             <p>${t_sum.get('end_balance', insured_p):,.0f}</p></div>
        """, unsafe_allow_html=True)

        if today_m:
            rem_y = today_m.get("remaining_years", 0)
            # Current payment based on remaining amortization from today
            today_bal = today_m.get("balance_today", insured_p)
            current_pmt = calc_pmt(today_bal, annual_rate, n_py, rem_y, accel) if rem_y > 0 else pmt_a
            st.markdown(f"""
            <div class="mc mc-g">
                <h3>🟢 Balance as of Today ({today_m.get('as_of_date','')})</h3>
                <p>${today_m.get('balance_today',0):,.0f}</p></div>
            <div class="mc mc-g"><h3>🟢 Principal Paid to Date</h3>
                <p>${today_m.get('principal_paid_today',0):,.0f}</p></div>
            <div class="mc mc-g"><h3>🟢 Interest Paid to Date</h3>
                <p>${today_m.get('interest_paid_today',0):,.0f}</p></div>
            <div class="mc"><h3>⏳ Remaining Amortization (from today)</h3>
                <p>{rem_y:.1f} years</p></div>
            <div class="mc"><h3>📆 Current Monthly Payment (on remaining balance)</h3>
                <p>${current_pmt:,.2f}</p></div>
            """, unsafe_allow_html=True)

        st.markdown(f"""
        <div class="mc mc-r"><h3>Total Interest (full amortization)</h3>
             <p>${full_sum.get('total_interest',0):,.0f}</p></div>
        <div class="mc mc-r"><h3>Interest as % of Total Paid</h3>
             <p>{full_sum.get('interest_pct',0):.1f}%</p></div>
        <div class="mc"><h3>Original Payoff</h3>
             <p>{full_sum.get('payoff_years',0):.1f} years</p></div>
        """, unsafe_allow_html=True)

    # ── Charts ────────────────────────────────────────────────────
    st.divider()
    cc1, cc2 = st.columns(2)
    with cc1:
        fig_d = go.Figure(go.Pie(
            labels=["Principal", "Total Interest"],
            values=[insured_p, full_sum.get("total_interest", 0)],
            hole=0.55, marker_colors=["#1a3c5e", "#e74c3c"],
            textinfo="label+percent"))
        fig_d.update_layout(title="Principal vs Interest",
                            height=280, margin=dict(t=40, b=5))
        st.plotly_chart(fig_d, use_container_width=True, key="ch_donut")

    with cc2:
        # Three-layer stacked bar P&I chart
        today_p_g = today_m.get("period_today", 0)
        term_end_p_g = int(term_years * n_py)
        fig_pi = stacked_bar_pi(full_df, today_p_g, term_end_p_g,
                                 "Yearly Principal & Interest (3 Segments)")
        st.plotly_chart(fig_pi, use_container_width=True, key="ch_stackedbar")

    # ── Auto-save to DB ───────────────────────────────────────────
    setup_payload = {
        "widget_state": {
            "s_price": purchase_price, "s_dpct": down_pct,
            "s_mtype": mortgage_type, "s_freq": payment_freq,
            "s_rate": annual_rate, "s_amort": amort_years,
            "s_term": term_years, "s_startdate": str(start_date_in),
            "s_addcmhc": True,
        },
        "past_renewals":    st.session_state.past_renewals,
        "past_prepayments": st.session_state.past_prepayments,
        "summary":          full_sum,
        "today_metrics":    today_m,
    }
    sv1, sv2 = st.columns([1, 4])
    if sv1.button("💾 Save Setup to DB", key="btn_ss"):
        ok = db_save_setup(st.session_state.db_conn, setup_payload)
        if ok:
            st.session_state.setup_data   = setup_payload
            st.session_state.setup_loaded = True
            sv2.success("✅ Setup saved to database.")
        else:
            sv2.error("❌ Failed to save.")

    # ── Store base in session ─────────────────────────────────────
    st.session_state["base"] = dict(
        principal=insured_p, annual_rate=annual_rate, n_py=n_py,
        amort_years=amort_years, accel=accel, start_date=start_date_in,
        mortgage_type=mortgage_type, term_years=term_years,
        payment_freq=payment_freq, purchase_price=purchase_price,
        down_payment=down_pay, past_extra=past_extra,
        past_renewal_rcs=past_renewal_rcs, today_m=today_m,
    )

# ── Helper for tabs that need setup ──────────────────────────────
def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Please complete the **Setup & Overview** tab first, then click 💾 Save Setup to DB.")
        st.stop()

# ══════════════════════════════════════════════════════════════════
# TAB 2 — AMORTIZATION SCHEDULE (auto-scroll to current date)
# ══════════════════════════════════════════════════════════════════
with tabs[1]:
    st.subheader("📅 Full Amortization Schedule")
    require_setup()
    b = st.session_state["base"]

    today_ym = date.today().strftime("%Y-%m")
    cv1, cv2, cv3 = st.columns(3)
    view_mode  = cv1.radio("View", ["All Periods","Monthly","Yearly"],
                           horizontal=True, key="sch_v")
    show_all   = cv2.checkbox("Show full schedule (not just from today)", False,
                              key="sch_show_all",
                              help="Uncheck to start the table from today's row")
    do_hl      = cv3.checkbox("Highlight current month", True, key="sch_hl")

    all_rcs = b.get("past_renewal_rcs") or []
    df_sch, _ = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=all_rcs or None)

    if df_sch.empty:
        st.error("Cannot build schedule.")
    else:
        def get_ym(d): return d.strftime("%Y-%m") if hasattr(d,"strftime") else str(d)[:7]
        today_rows = df_sch[df_sch["Date"].apply(get_ym) == today_ym]
        today_period = int(today_rows["Period"].iloc[0]) if not today_rows.empty else None

        if view_mode == "Yearly":
            disp = df_sch.groupby("Year").agg(
                Payments=("Payment","count"), Total_Paid=("Total Paid","sum"),
                Interest=("Interest","sum"), Principal=("Principal","sum"),
                Prepayment=("Prepayment","sum"),
                Ending_Balance=("Balance","last"),
                Cum_Interest=("Cum Interest","last")).reset_index()
            disp.columns = ["Year","Payments","Total Paid","Interest","Principal",
                            "Prepayment","Ending Balance","Cum Interest"]
            cur_y = str(date.today().year)
            def _hl(row):
                return ["background:#FFF3CD;font-weight:bold"
                        if do_hl and str(row.get("Year",""))==cur_y else ""]*len(row)
        elif view_mode == "Monthly" and b["n_py"] > 12:
            df_sch["YM"] = df_sch["Date"].apply(get_ym)
            disp = df_sch.groupby("YM").agg(
                Total_Paid=("Total Paid","sum"), Interest=("Interest","sum"),
                Principal=("Principal","sum"),
                Ending_Balance=("Balance","last")).reset_index()
            def _hl(row):
                return ["background:#FFF3CD;font-weight:bold"
                        if do_hl and str(row.get("YM",""))==today_ym else ""]*len(row)
        else:
            disp = df_sch[["Period","Date","Payment","Interest","Principal",
                           "Prepayment","Total Paid","Balance",
                           "Rate (%)","Cum Interest"]].copy()
            disp["Date"] = disp["Date"].apply(
                lambda d: d.strftime("%Y-%m-%d") if hasattr(d,"strftime") else str(d)[:10])
            def _hl(row):
                return ["background:#FFF3CD;font-weight:bold"
                        if do_hl and str(row.get("Date",""))[:7]==today_ym else ""]*len(row)

        # FIX #5: Default to scrolled to current date row
        if not show_all and today_period and view_mode != "Yearly":
            # Show rows from (today_period - 3) onwards so today is near the top
            start_row = max(0, today_period - 4)
            disp_show = disp.iloc[start_row:].reset_index(drop=True)
            st.caption(f"📍 Showing from period {start_row+1} — uncheck 'Show full schedule' to always start here")
        else:
            disp_show = disp

        mc_ = [c for c in disp_show.columns
               if c not in ["Period","Year","Payments","YM","Date","Rate (%)"]]
        st.dataframe(
            disp_show.style.apply(_hl, axis=1).format({c:"${:,.2f}" for c in mc_}),
            use_container_width=True, height=480)

        if today_period:
            bal_t = float(df_sch[df_sch["Period"]==today_period]["Balance"].iloc[0])
            st.markdown(
                f'<div class="ok">🟡 Current month: Period <b>{today_period}</b> '
                f'({date.today().strftime("%B %Y")}) · Balance: <b>${bal_t:,.0f}</b></div>',
                unsafe_allow_html=True)

        # Balance chart with date x-axis
        fig_bal = go.Figure()
        fig_bal.add_scatter(x=df_sch["Date"], y=df_sch["Balance"],
                            fill="tozeroy", name="Balance",
                            line=dict(color="#1a3c5e"))
        fig_bal.add_scatter(x=df_sch["Date"], y=df_sch["Cum Interest"],
                            name="Cum Interest", line=dict(color="#e74c3c", dash="dash"))
        if today_period:
            td_d = df_sch[df_sch["Period"]==today_period]["Date"].iloc[0]
            fig_bal.add_vline(x=_vline_x(td_d), line_dash="dash",
                              line_color="#27ae60", annotation_text="Today")
        fig_bal.update_layout(title="Balance & Cumulative Interest",
                              xaxis_title="Date", yaxis_title="($)", height=360)
        st.plotly_chart(fig_bal, use_container_width=True, key="ch_sch_bal")
        st.download_button("⬇️ Download CSV", df_sch.to_csv(index=False).encode(),
                           "schedule.csv", "text/csv")

# ══════════════════════════════════════════════════════════════════
# TAB 3 — RATE CHANGE SCENARIOS
# ══════════════════════════════════════════════════════════════════
with tabs[2]:
    st.subheader("📈 Rate Change / Renewal Scenarios")
    require_setup()
    b = st.session_state["base"]

    st.info("Create unlimited named scenarios. Early renewal auto-detects break penalty "
            "(advisory — you choose which figure to apply). "
            "Variable renewals support sub-scenarios a, b, c with multiple rate changes.")

    rcs: dict = st.session_state.rc_scenarios
    if st.button("➕ New Scenario", key="btn_new_rc"):
        nid = str(uuid.uuid4())[:8]
        rcs[nid] = {"name": f"Scenario {len(rcs)+1}", "desc": "", "renewals": []}
        st.rerun()

    if not rcs:
        st.markdown('<div class="inf">Click ➕ New Scenario to begin.</div>',
                    unsafe_allow_html=True)

    orig_term_end_p = int(b["term_years"] * b["n_py"])
    df_base_ref, s_base_ref = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None)

    today_p_ref = b.get("today_m", {}).get("period_today", 0)
    sc_del = []
    term_opts_sc = [0.5, 1, 2, 3, 4, 5, 7, 10]

    for sc_id, sc in rcs.items():
        with st.expander(
            f"📋 {sc['name']}" + (f" — {sc['desc'][:55]}" if sc["desc"] else ""),
                expanded=True):
            h1, h2, h3 = st.columns([2, 3, 1])
            sc["name"] = h1.text_input("Name", sc["name"], key=f"rcn_{sc_id}")
            sc["desc"] = h2.text_input("Description", sc["desc"],
                                       placeholder="e.g. Early renewal at lower rate",
                                       key=f"rcd_{sc_id}")
            if h3.button("🗑️ Delete", key=f"del_sc_{sc_id}"): sc_del.append(sc_id)

            # Templates via checkbox
            show_tpl = st.checkbox("🚀 Quick templates", False, key=f"tpl_cb_{sc_id}")
            if show_tpl:
                ren_p = orig_term_end_p + 1
                ren_d = str(period_to_date(ren_p, b["start_date"], b["n_py"]))
                tpls = {
                    "+1% at renewal": [{"date":ren_d,"rate":b["annual_rate"]+1,"mtype":"Fixed","term":3}],
                    "+2% at renewal": [{"date":ren_d,"rate":b["annual_rate"]+2,"mtype":"Fixed","term":3}],
                    "-1% at renewal": [{"date":ren_d,"rate":b["annual_rate"]-1,"mtype":"Fixed","term":3}],
                    "-2% at renewal": [{"date":ren_d,"rate":b["annual_rate"]-2,"mtype":"Fixed","term":3}],
                    "Variable at renewal": [{"date":ren_d,"rate":b["annual_rate"]-0.5,"mtype":"Variable","term":3}],
                    "BoC hike then cut": [
                        {"date":str(period_to_date(ren_p//2,b["start_date"],b["n_py"])),
                         "rate":b["annual_rate"]+2,"mtype":"Fixed","term":1},
                        {"date":ren_d,"rate":b["annual_rate"]+1,"mtype":"Fixed","term":3}],
                    "Rate stays flat": [{"date":ren_d,"rate":b["annual_rate"],"mtype":"Fixed","term":3}],
                }
                tc1, tc2 = st.columns([3, 1])
                tpl_s = tc1.selectbox("Template", list(tpls.keys()), key=f"tpl_sel_{sc_id}")
                if tc2.button("Apply", key=f"tpl_ap_{sc_id}"):
                    sc["renewals"] = [
                        {"id":str(uuid.uuid4())[:8],"mode":"By Date",
                         "date_str":t["date"],
                         "period":date_to_period(t["date"],b["start_date"],b["n_py"]),
                         "new_rate":t["rate"],"mtype":t["mtype"],"term_years":t["term"],
                         "actual_penalty":0,"misc_fees":250,
                         "orig_posted":t["rate"]+1.5,"curr_posted":t["rate"]-0.5,
                         "variable_subs":{}}
                        for t in tpls[tpl_s]]
                    st.rerun()

            st.markdown("---")
            if st.button("➕ Add Renewal Entry", key=f"add_ren_{sc_id}"):
                dd = str(start_date_in + relativedelta(
                    years=int(b["term_years"]), months=int((b["term_years"]%1)*12)))
                sc["renewals"].append({
                    "id":str(uuid.uuid4())[:8],"mode":"By Date","date_str":dd,
                    "period":date_to_period(dd,b["start_date"],b["n_py"]),
                    "new_rate":b["annual_rate"],"mtype":"Fixed","term_years":3,
                    "actual_penalty":0,"misc_fees":250,
                    "orig_posted":b["annual_rate"]+1.5,"curr_posted":b["annual_rate"]-0.5,
                    "variable_subs":{}})
                st.rerun()

            prev_term_end_p = orig_term_end_p
            ren_del = []

            for ri, rn in enumerate(sc["renewals"]):
                rid = rn["id"]
                st.markdown(f"**Renewal {ri+1}**")
                rc1, rc2, rc3, rc4, rc5 = st.columns([1.5, 1.8, 1.5, 1.5, 0.7])

                rn["mode"] = rc1.radio("Mode", ["By Date","By Period"],
                                       index=0 if rn.get("mode","By Date")=="By Date" else 1,
                                       horizontal=True, key=f"rm_{sc_id}_{rid}")
                if rn["mode"] == "By Date":
                    pd_v = rc2.date_input("Effective date",
                                          date.fromisoformat(rn.get("date_str", str(b["start_date"]))),
                                          key=f"rd_{sc_id}_{rid}")
                    rn["date_str"] = str(pd_v)
                    rn["period"]   = date_to_period(pd_v, b["start_date"], b["n_py"])
                    rc2.caption(f"≈ Period {rn['period']}")
                else:
                    mx = int(b["amort_years"] * b["n_py"])
                    rn["period"] = int(rc2.number_input("Period #", 1, mx,
                                       int(rn.get("period", orig_term_end_p+1)),
                                       key=f"rp_{sc_id}_{rid}"))
                    rc2.caption(f"≈ {period_to_date(rn['period'],b['start_date'],b['n_py']).strftime('%b %Y')}")

                rn["mtype"]   = rc3.selectbox("Type", ["Fixed","Variable"],
                                              index=0 if rn.get("mtype","Fixed")=="Fixed" else 1,
                                              key=f"rmt_{sc_id}_{rid}")
                rn["new_rate"] = float(rc4.number_input(
                    "Rate (%)", 0.5, 20.0, float(rn.get("new_rate",b["annual_rate"])),
                    0.01, format="%.2f", key=f"rrt_{sc_id}_{rid}",
                    help="New interest rate at this renewal"))
                if rc5.button("🗑️", key=f"delren_{sc_id}_{rid}"): ren_del.append(ri)

                rn["term_years"] = st.selectbox(
                    f"Term (years) — Renewal {ri+1}", term_opts_sc,
                    index=term_opts_sc.index(rn.get("term_years",3))
                    if rn.get("term_years",3) in term_opts_sc else 3,
                    key=f"rty_{sc_id}_{rid}")

                rn_start_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
                rn_end_d   = rn_start_d + relativedelta(
                    years=int(rn["term_years"]),
                    months=int((float(rn["term_years"])%1)*12))
                st.caption(f"📅 Term: **{rn_start_d.strftime('%b %d, %Y')}** → **{rn_end_d.strftime('%b %d, %Y')}**")

                # Early renewal detection
                is_early = rn["period"] < prev_term_end_p
                months_left_at = max(int((prev_term_end_p - rn["period"]) / b["n_py"] * 12), 1) if is_early else 0

                if is_early:
                    ren_df = df_base_ref[df_base_ref["Period"] <= rn["period"]]
                    bal_ren  = float(ren_df["Balance"].iloc[-1])  if not ren_df.empty else b["principal"]
                    rate_ren = float(ren_df["Rate (%)"].iloc[-1]) if not ren_df.empty else b["annual_rate"]

                    st.markdown(
                        f'<div class="warn">⚡ <b>Early Renewal</b> — '
                        f'{months_left_at} months remain · Balance: <b>${bal_ren:,.0f}</b></div>',
                        unsafe_allow_html=True)

                    rn["orig_posted"] = float(st.columns(2)[0].number_input(
                        "Original posted rate (%)", 0.5, 20.0,
                        float(rn.get("orig_posted", rate_ren+1.5)), 0.01,
                        format="%.2f", key=f"op_{sc_id}_{rid}",
                        help="Posted rate when you originally signed"))
                    rn["curr_posted"] = float(st.columns(2)[1].number_input(
                        "Current posted rate (%)", 0.5, 20.0,
                        float(rn.get("curr_posted", max(rate_ren-0.5,0.5))), 0.01,
                        format="%.2f", key=f"cp_{sc_id}_{rid}",
                        help="Current posted rate for remaining term length"))

                    adv = calc_break_penalty(bal_ren, rate_ren, rn["mtype"],
                                             rn["orig_posted"], rn["curr_posted"],
                                             months_left_at)

                    # FIX #6: Radio button between 3-month interest, IRD, custom
                    opts_labels = [f"3-Month Interest (${adv['3_months_interest']:,.0f})"]
                    if adv["ird"] is not None:
                        opts_labels.append(f"IRD (${adv['ird']:,.0f})")
                    opts_labels.append("Custom value")

                    pen_choice = st.radio(
                        "Apply which penalty?", opts_labels,
                        key=f"pen_radio_{sc_id}_{rid}",
                        help="Advisory values calculated above — banks may charge differently")

                    if "Custom" in pen_choice:
                        rn["actual_penalty"] = float(st.number_input(
                            "Custom penalty ($)", 0, 500_000,
                            int(rn.get("actual_penalty", adv["calc_penalty"])), 100,
                            key=f"ap_{sc_id}_{rid}"))
                    elif "IRD" in pen_choice:
                        rn["actual_penalty"] = adv["ird"] or 0.0
                    else:
                        rn["actual_penalty"] = adv["3_months_interest"]

                    rn["misc_fees"] = float(st.number_input(
                        "Miscellaneous fees ($)", 0, 50_000,
                        int(rn.get("misc_fees", 500)), 50,
                        key=f"mf_{sc_id}_{rid}",
                        help="Admin, appraisal, legal fees"))

                    total_exit = rn["actual_penalty"] + rn["misc_fees"]
                    old_pmt = calc_pmt(bal_ren, rate_ren, 12,
                                       max(b["amort_years"] - rn["period"]/b["n_py"], 1))
                    new_pmt = calc_pmt(bal_ren, rn["new_rate"], 12,
                                       max(b["amort_years"] - rn["period"]/b["n_py"], 1))
                    st.markdown(
                        f'<div class="pen">💸 Penalty applied: <b>${rn["actual_penalty"]:,.0f}</b> · '
                        f'Misc: <b>${rn["misc_fees"]:,.0f}</b> · '
                        f'<b>Total exit cost: ${total_exit:,.0f}</b></div>',
                        unsafe_allow_html=True)
                    if abs(old_pmt - new_pmt) > 1:
                        rec = total_exit / abs(old_pmt - new_pmt)
                        st.caption(f"Monthly saving: ${abs(old_pmt-new_pmt):,.0f} → "
                                   f"Break-even: **{rec:.0f} months** ({rec/12:.1f} yrs)")
                else:
                    rn["misc_fees"] = float(st.number_input(
                        "Misc fees ($)", 0, 50_000, int(rn.get("misc_fees",250)), 50,
                        key=f"mf2_{sc_id}_{rid}",
                        help="Admin/appraisal fees at normal renewal"))
                    rn["actual_penalty"] = 0

                # Variable sub-scenarios
                if rn["mtype"] == "Variable":
                    st.markdown(f"**📊 Variable sub-scenarios for Renewal {ri+1}:**")
                    if "variable_subs" not in rn: rn["variable_subs"] = {}
                    vsubs = rn["variable_subs"]

                    if st.button("➕ Add Sub-Scenario", key=f"add_vsub_{sc_id}_{rid}"):
                        letter = chr(ord('a') + len(vsubs))
                        vsubs[letter] = {"name": f"Sub {letter}", "n_changes": 1, "changes": []}
                        st.rerun()

                    vsub_del = []
                    for sub_k, sub in vsubs.items():
                        sub_lbl = f"{sc['name']}{sub_k}: {sub['name']}"
                        st.markdown(f"###### {sub_lbl}")
                        vsa, vsb, vsc = st.columns([2, 1, 1])
                        sub["name"]      = vsa.text_input("Name", sub["name"], key=f"vsn_{sc_id}_{rid}_{sub_k}")
                        sub["n_changes"] = int(vsb.number_input("# rate changes", 1, 12,
                                               int(sub.get("n_changes",1)), 1,
                                               key=f"vsnc_{sc_id}_{rid}_{sub_k}"))
                        if vsc.button("🗑️", key=f"del_vsub_{sc_id}_{rid}_{sub_k}"):
                            vsub_del.append(sub_k)

                        while len(sub["changes"]) < sub["n_changes"]:
                            sub["changes"].append({"id":str(uuid.uuid4())[:8],
                                                   "date_str":rn["date_str"],
                                                   "new_rate":rn["new_rate"]})
                        sub["changes"] = sub["changes"][:sub["n_changes"]]

                        for ci, chg in enumerate(sub["changes"]):
                            vc1, vc2 = st.columns(2)
                            chg_d = vc1.date_input(
                                f"Change {ci+1} — date",
                                value=date.fromisoformat(chg.get("date_str", rn["date_str"])),
                                key=f"vcd_{sc_id}_{rid}_{sub_k}_{ci}")
                            chg["date_str"] = str(chg_d)
                            chg["period"]   = date_to_period(chg_d, b["start_date"], b["n_py"])
                            chg["new_rate"] = float(vc2.number_input(
                                f"Rate (%) — change {ci+1}", 0.5, 20.0,
                                float(chg.get("new_rate", rn["new_rate"])), 0.01,
                                format="%.2f", key=f"vcr_{sc_id}_{rid}_{sub_k}_{ci}"))

                    for sk in vsub_del: del vsubs[sk]
                    if vsub_del: st.rerun()

                prev_term_end_p = int(rn["period"]) + int(float(rn["term_years"]) * b["n_py"])
                st.markdown("---")

            for ri in sorted(ren_del, reverse=True): sc["renewals"].pop(ri)
            if ren_del: st.rerun()

            # Build scenario
            if sc["renewals"]:
                last_rn = sc["renewals"][-1]
                sc_term_end_p = int(last_rn["period"]) + int(float(last_rn["term_years"]) * b["n_py"])
            else:
                sc_term_end_p = orig_term_end_p

            main_rc = [{"period": rn["period"], "new_rate": rn["new_rate"]}
                       for rn in sc["renewals"]]
            all_rcs_sc = (b.get("past_renewal_rcs") or []) + main_rc

            df_sc, s_sc = build_amortization(
                b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                accel=b["accel"], start_date=b["start_date"],
                extra_payments=b.get("past_extra") or None,
                rate_changes=all_rcs_sc or None)

            tm = b.get("today_m", {})
            today_p_sc = tm.get("period_today", 0)
            rem_sc = round((len(df_sc) - today_p_sc) / b["n_py"], 1) if today_p_sc > 0 and not df_sc.empty else b["amort_years"]
            base_rem = b["amort_years"] - today_p_sc / b["n_py"]

            # Calculated payment on remaining balance at scenario rate
            last_rate = sc["renewals"][-1]["new_rate"] if sc["renewals"] else b["annual_rate"]
            today_bal = tm.get("balance_today", b["principal"])
            sc_pmt = calc_pmt(today_bal, last_rate, b["n_py"], rem_sc, b["accel"]) if rem_sc > 0 else 0

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Base Interest",     f"${s_base_ref.get('total_interest',0):,.0f}")
            m2.metric("Scenario Interest", f"${s_sc.get('total_interest',0):,.0f}",
                      delta=f"${s_sc.get('total_interest',0)-s_base_ref.get('total_interest',0):+,.0f}")
            m3.metric("Base Remaining",    f"{base_rem:.1f} yrs")
            m4.metric("Scenario Remaining",f"{rem_sc:.1f} yrs",
                      delta=f"{rem_sc-base_rem:+.1f} yrs")
            m5.metric("Payment at renewal rate", f"${sc_pmt:,.2f}",
                      help="Monthly payment calculated on today's balance at last renewal rate")

            # Three-layer stacked bar P&I
            if not df_sc.empty:
                fig_sc_bar = stacked_bar_pi(df_sc, today_p_sc, sc_term_end_p,
                                             f"{sc['name']} — Principal & Interest")
                for rn in sc["renewals"]:
                    rn_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
                    col_v = "red" if rn["period"] < orig_term_end_p else "orange"
                    yr_n  = ((rn["period"]-1) // b["n_py"]) + 1
                    fig_sc_bar.add_vline(x=yr_n - 0.5, line_dash="dot",
                                         line_color=col_v,
                                         annotation_text=f"{rn['new_rate']}%")
                st.plotly_chart(fig_sc_bar, use_container_width=True, key=f"ch_sc_bar_{sc_id}")

                # Rate over time
                fig_rr = go.Figure()
                fig_rr.add_scatter(x=df_sc["Date"], y=df_sc["Rate (%)"],
                                   fill="tozeroy", name="Rate",
                                   line=dict(color="#27ae60"))
                fig_rr.update_layout(title="Rate over time", xaxis_title="Date",
                                      yaxis_title="%", height=200)
                st.plotly_chart(fig_rr, use_container_width=True, key=f"ch_rate_{sc_id}")

            # Variable sub-scenario charts
            for rn in sc["renewals"]:
                if rn["mtype"] != "Variable": continue
                for sub_k, sub in rn.get("variable_subs", {}).items():
                    sub_rc = all_rcs_sc + [
                        {"period": chg["period"], "new_rate": chg["new_rate"]}
                        for chg in sub["changes"]]
                    df_sub, s_sub = build_amortization(
                        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                        accel=b["accel"], start_date=b["start_date"],
                        extra_payments=b.get("past_extra") or None,
                        rate_changes=sub_rc or None)
                    if df_sub.empty: continue
                    sub_id = f"{sc['name']}{sub_k}"
                    rem_sub = round((len(df_sub)-today_p_sc)/b["n_py"],1) if today_p_sc>0 else b["amort_years"]
                    st.markdown(f"**Sub-scenario {sub_id}: {sub['name']}**")
                    s1, s2, s3 = st.columns(3)
                    s1.metric(f"Interest", f"${s_sub.get('total_interest',0):,.0f}",
                              delta=f"${s_sub.get('total_interest',0)-s_base_ref.get('total_interest',0):+,.0f}")
                    s2.metric(f"Remaining", f"{rem_sub:.1f} yrs")
                    s3.metric(f"End Balance", f"${s_sub.get('end_balance',0):,.0f}")
                    fig_sub_ = stacked_bar_pi(df_sub, today_p_sc, sc_term_end_p,
                                              f"Sub-scenario {sub_id}: {sub['name']}")
                    st.plotly_chart(fig_sub_, use_container_width=True,
                                    key=f"ch_sub_{sc_id}_{rn['id']}_{sub_k}")

            if st.button("💾 Save scenario", key=f"save_rc_{sc_id}"):
                sc_data = {"type": "rate_change",
                           "params": {**b, "start_date": str(b["start_date"]),
                                      "rate_changes": main_rc, "sc_name": sc["name"]},
                           "summary": s_sc, "rate_changes": main_rc}
                st.session_state.saved_scenarios[sc["name"]] = sc_data
                if db_save_scenario(st.session_state.db_conn, sc["name"],
                                    sc_data["params"], sc_data["summary"]):
                    st.success(f"Saved to DB: {sc['name']}")
                else:
                    st.success(f"Saved locally: {sc['name']}")

    for sc_id in sc_del: del rcs[sc_id]
    if sc_del: st.rerun()

# ══════════════════════════════════════════════════════════════════
# TAB 4 — PREPAYMENT ANALYSIS
# ══════════════════════════════════════════════════════════════════
with tabs[3]:
    st.subheader("💰 Prepayment Analysis")
    require_setup()
    b = st.session_state["base"]
    fn = b["n_py"]

    saved_rc = {k: v for k, v in st.session_state.saved_scenarios.items()
                if v.get("type") == "rate_change"}
    chosen_rc = st.selectbox("Rate scenario base",
                             ["Base Rate — no rate changes"] + list(saved_rc.keys()),
                             key="pp_rc_sel")
    active_rc  = [] if chosen_rc == "Base Rate — no rate changes" \
                 else saved_rc[chosen_rc].get("rate_changes", [])
    all_rcs_pp = (b.get("past_renewal_rcs") or []) + active_rc

    col_pp1, col_pp2 = st.columns(2)
    with col_pp1:
        st.markdown("##### 📅 Annual Lump-Sum Prepayments")
        annual_lump = st.number_input("Annual lump-sum ($)", 0, 500_000, 10_000, 500, key="pp_al")
        lump_month  = st.selectbox("Month each year",
                                   ["Jan","Feb","Mar","Apr","May","Jun",
                                    "Jul","Aug","Sep","Oct","Nov","Dec"], key="pp_lm")
        lump_start  = st.number_input("Starting year", 1, 30, 1, key="pp_ls")
        lump_nyrs   = st.number_input("For how many years?", 1, 30, 5, key="pp_ln")
        lmm = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
               "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
        future_extra = []
        if annual_lump > 0:
            for yr in range(int(lump_start), int(lump_start + lump_nyrs)):
                p = max(1, int((yr-1)*fn + lmm[lump_month]*fn/12))
                future_extra.append({"period": p, "amount": float(annual_lump)})
        pp_lim = st.slider("Lender prepayment limit (%)", 10, 30, 20, key="pp_lim")
        if annual_lump > b["principal"] * pp_lim / 100:
            st.warning(f"⚠️ Exceeds {pp_lim}% limit (${b['principal']*pp_lim/100:,.0f}).")

    with col_pp2:
        st.markdown("##### 💳 Increased Regular Payments")
        inc_t = st.radio("Increase type", ["Fixed $","% increase","None"],
                         index=2, horizontal=True, key="pp_it")
        inc_v = 0.0
        if inc_t == "Fixed $":
            inc_v = float(st.number_input("Extra/payment ($)", 0, 10_000, 200, 50, key="pp_if"))
        elif inc_t == "% increase":
            inc_pct = st.slider("% increase", 1, 100, 10, key="pp_ip")
            inc_v = calc_pmt(b["principal"], b["annual_rate"], fn,
                             b["amort_years"], b["accel"]) * inc_pct / 100
        if inc_v > 0:
            for p in range(1, int(b["amort_years"]*fn)+1):
                future_extra.append({"period": p, "amount": inc_v})

        st.markdown("##### 🔁 One-Time Lump Sum")
        ot_mode = st.radio("Mode", ["By Date","By Period"], horizontal=True, key="pp_om")
        if ot_mode == "By Date":
            ot_d = st.date_input("Date", b["start_date"]+relativedelta(years=1),
                                 min_value=b["start_date"], key="pp_od")
            ot_p = date_to_period(ot_d, b["start_date"], fn)
            st.caption(f"≈ Period {ot_p}")
        else:
            ot_p = int(st.number_input("Period #", 1, int(b["amort_years"]*fn), fn, key="pp_op"))
        ot_a = st.number_input("Amount ($)", 0, 2_000_000, 0, 1_000, key="pp_oa")
        if ot_a > 0:
            future_extra.append({"period": int(ot_p), "amount": float(ot_a)})

    past_extra = b.get("past_extra", [])
    all_extra  = past_extra + future_extra

    df_rsc, s_rsc = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=past_extra or None, rate_changes=all_rcs_pp or None)
    df_pp, s_pp = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=all_extra or None, rate_changes=all_rcs_pp or None)

    tm_pp = b.get("today_m", {})
    today_p_pp = tm_pp.get("period_today", 0)
    rem_rsc = round((len(df_rsc)-today_p_pp)/b["n_py"],1) if today_p_pp>0 and not df_rsc.empty else b["amort_years"]
    rem_pp  = round((len(df_pp) -today_p_pp)/b["n_py"],1) if today_p_pp>0 and not df_pp.empty  else b["amort_years"]

    int_saved = s_rsc.get("total_interest",0) - s_pp.get("total_interest",0)
    new_total = sum(e["amount"] for e in future_extra)

    st.divider()
    m1,m2,m3,m4,m5,m6 = st.columns(6)
    m1.metric("Interest (rate sc.)",       f"${s_rsc.get('total_interest',0):,.0f}")
    m2.metric("Interest (+ prepayments)",  f"${s_pp.get('total_interest',0):,.0f}",
              delta=f"${-int_saved:+,.0f}")
    m3.metric("Remaining (rate sc.)",      f"{rem_rsc:.1f} yrs")
    m4.metric("Remaining (+ prepayments)", f"{rem_pp:.1f} yrs",
              delta=f"{rem_pp-rem_rsc:+.1f} yrs")
    m5.metric("Total New Prepaid",         f"${new_total:,.0f}")
    m6.metric("Interest ROI",
              f"{int_saved/new_total*100:.1f}%" if new_total>0 and int_saved>0 else "—")

    if int_saved > 0:
        st.markdown(f'<div class="ok">💚 Prepayments save <b>${int_saved:,.0f}</b> in interest · '
                    f'Shorten remaining by <b>{rem_rsc-rem_pp:.1f} yrs</b></div>',
                    unsafe_allow_html=True)

    sc_end_p = int(b["term_years"]*b["n_py"]) + int(3*b["n_py"])
    if not df_pp.empty:
        fig_pp_bar = stacked_bar_pi(
            df_pp, today_p_pp, sc_end_p,
            f"Prepayment Impact — Principal & Interest ({chosen_rc})")
        st.plotly_chart(fig_pp_bar, use_container_width=True, key="ch_pp_bar")

    sc_np = st.text_input("Save as", "Prepayment Scenario", key="pp_scname")
    if st.button("💾 Save", key="btn_save_pp"):
        sc_data = {"type":"prepayment",
                   "params":{**b,"start_date":str(b["start_date"]),
                              "extra_payments":all_extra,"rate_changes":active_rc},
                   "summary":s_pp,"rate_changes":active_rc}
        st.session_state.saved_scenarios[sc_np] = sc_data
        db_save_scenario(st.session_state.db_conn, sc_np,
                         {"extra":len(all_extra)}, s_pp)
        st.success("Saved")

# ══════════════════════════════════════════════════════════════════
# TAB 5 — BREAK PENALTY
# ══════════════════════════════════════════════════════════════════
with tabs[4]:
    st.subheader("⚠️ Mortgage Break Penalty Calculator")
    require_setup()
    b = st.session_state["base"]

    st.markdown("**Variable**: 3 months interest · **Fixed**: max(3 months interest, IRD)")

    c1, c2 = st.columns(2)
    with c1:
        bp_bal   = st.number_input("Outstanding Balance ($)", 100, 5_000_000,
                                   int(b.get("principal",500_000)*0.85), 1_000,
                                   key="bp_bal", help="Current outstanding mortgage balance")
        bp_rate  = st.number_input("Contract Rate (%)", 0.5, 20.0,
                                   float(b.get("annual_rate",5.39)), 0.01,
                                   format="%.2f", key="bp_rate",
                                   help="Your current mortgage interest rate")
        bp_mtype = st.selectbox("Mortgage Type", ["Fixed","Variable"],
                                index=0 if b.get("mortgage_type","Fixed")=="Fixed" else 1,
                                key="bp_mtype_tab5",
                                help="Fixed or Variable rate mortgage")
        bp_mleft = st.slider("Months Remaining in Term", 1, 120, 36, key="bp_mleft",
                             help="Months left in your current term")
        bp_misc  = st.number_input("Miscellaneous Fees ($)", 0, 50_000, 500, 50,
                                   key="bp_misc",
                                   help="Admin, appraisal, legal, registration fees")
    with c2:
        if bp_mtype == "Fixed":
            st.markdown("##### IRD Inputs")
            st.caption("IRD uses posted rates (not your discounted contract rate).")
            bp_orig = st.number_input("Posted Rate at Origination (%)", 0.5, 20.0,
                                      float(b.get("annual_rate",5.39))+1.5, 0.01,
                                      format="%.2f", key="bp_orig",
                                      help="Bank's posted rate when you originally signed")
            bp_curr = st.number_input("Current Posted Rate for Remaining Term (%)", 0.5, 20.0,
                                      max(float(b.get("annual_rate",5.39))-0.5, 0.5),
                                      0.01, format="%.2f", key="bp_curr",
                                      help="Bank's current posted rate for your remaining term")
        else:
            bp_orig = bp_curr = float(b.get("annual_rate", 5.39))

    pen = calc_break_penalty(bp_bal, bp_rate, bp_mtype, bp_orig, bp_curr, bp_mleft)

    # FIX #6: Radio button for penalty choice
    st.markdown("#### 💸 Advisory Calculation — Choose Penalty to Apply")
    pen_opts = [f"3-Month Interest (${pen['3_months_interest']:,.0f})"]
    if pen["ird"] is not None:
        pen_opts.append(f"IRD (${pen['ird']:,.0f})")
    pen_opts.append("Custom value")

    bp_choice = st.radio("Apply which penalty?", pen_opts, key="bp_pen_radio",
                         help="Advisory values shown — your bank may charge differently")

    if "Custom" in bp_choice:
        actual_pen = float(st.number_input("Custom penalty ($)", 0, 500_000,
                                           int(pen["calc_penalty"]), 100, key="bp_custom_pen"))
    elif "IRD" in bp_choice:
        actual_pen = pen["ird"] or 0.0
    else:
        actual_pen = pen["3_months_interest"]

    total_exit = actual_pen + bp_misc

    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("3 Months Interest", f"${pen['3_months_interest']:,.2f}")
    if pen["ird"] is not None: cc2.metric("IRD", f"${pen['ird']:,.2f}")
    cc3.metric("Penalty Applied", f"${actual_pen:,.2f}")
    cc4.metric("Total Exit Cost", f"${total_exit:,.2f}")

    st.divider()
    new_r = st.slider("New rate if you break (%)", 0.5, 15.0,
                      max(float(b.get("annual_rate",5.39))-1.0, 0.5),
                      0.05, key="bp_newr")
    ar = max(bp_mleft/12, 1)
    _, s_stay = build_amortization(bp_bal, bp_rate, 12, ar, term_periods=bp_mleft)
    _, s_brk  = build_amortization(bp_bal, new_r,   12, ar, term_periods=bp_mleft)
    int_stay  = s_stay.get("total_interest", 0)
    int_brk   = s_brk.get("total_interest",  0) + total_exit
    net_sav   = int_stay - int_brk

    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("Interest (Stay)",      f"${int_stay:,.0f}")
    bc2.metric("Interest+Fees (Break)", f"${int_brk:,.0f}")
    bc3.metric("Net Savings",           f"${net_sav:,.0f}",
               delta="✅ Worth breaking" if net_sav > 0 else "❌ Not worth it")

    sweep  = np.arange(0.5, float(b.get("annual_rate",5.39))+0.11, 0.25)
    svlist = []
    for tr in sweep:
        _, st_ = build_amortization(bp_bal, tr, 12, ar, term_periods=bp_mleft)
        svlist.append(int_stay - (st_.get("total_interest",0) + total_exit))
    fig_be = go.Figure()
    fig_be.add_scatter(x=list(sweep), y=svlist, mode="lines+markers",
                       line=dict(color="#1a3c5e"), name="Net Savings")
    fig_be.add_hline(y=0, line_dash="dash", line_color="red",
                     annotation_text="Break-even")
    fig_be.add_vline(x=float(b.get("annual_rate",5.39)), line_dash="dot",
                     line_color="orange", annotation_text="Current rate")
    fig_be.update_layout(title="Net Savings vs New Rate",
                         xaxis_title="New Rate (%)", yaxis_title="Net Savings ($)",
                         height=320)
    st.plotly_chart(fig_be, use_container_width=True, key="ch_bpbe")

    op = calc_pmt(bp_bal, bp_rate, 12, ar)
    np_ = calc_pmt(bp_bal, new_r,   12, ar)
    if abs(op - np_) > 1:
        st.markdown(
            f'<div class="inf">Monthly: <b>${op:,.2f}</b> → <b>${np_:,.2f}</b> '
            f'(<b>${np_-op:+,.2f}/mo</b>) · '
            f'Recoup in: <b>{total_exit/abs(op-np_):.0f} months</b></div>',
            unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# TAB 6 — SCENARIO COMPARISON
# ══════════════════════════════════════════════════════════════════
with tabs[5]:
    st.subheader("🔄 Side-by-Side Scenario Comparison")
    require_setup()
    b = st.session_state["base"]

    n_sc  = st.radio("Scenarios", [2, 3, 4], horizontal=True, key="cmp_n")
    sc_defs = []
    cols  = st.columns(int(n_sc))

    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**Scenario {i+1}**")
            lbl  = st.text_input("Label", f"Scenario {i+1}", key=f"cmp_lbl_{i}")
            rate = st.number_input("Rate (%)", 0.5, 20.0,
                                   float(b["annual_rate"])+i*0.5, 0.01,
                                   key=f"cmp_rate_{i}", format="%.2f")
            amt  = st.slider("Amort (yrs)", 5, 30, b["amort_years"], key=f"cmp_amt_{i}")
            frq  = st.selectbox("Frequency", list(FREQ.keys()),
                                index=list(FREQ.keys()).index(b["payment_freq"]),
                                key=f"cmp_frq_{i}")
            lump = st.number_input("Annual lump ($)", 0, 200_000, 0, 1_000,
                                   key=f"cmp_lump_{i}")
            fc   = FREQ[frq]; ny = fc["n"]; ac = fc["accel"]
            ex   = list(b.get("past_extra", []))
            if lump > 0:
                for yr in range(1, amt+1):
                    ex.append({"period": max(1,int((yr-1)*ny+ny//2)),
                               "amount": float(lump)})
            df_c, s_c = build_amortization(
                b["principal"], rate, ny, amt, accel=ac,
                start_date=b["start_date"], extra_payments=ex or None,
                rate_changes=b.get("past_renewal_rcs") or None)
            pmt_c = calc_pmt(b["principal"], rate, ny, amt, ac)
            tp_c  = b.get("today_m",{}).get("period_today", 0)
            rem_c = round((len(df_c)-tp_c)/ny,1) if tp_c>0 and not df_c.empty else amt
            # Current payment on remaining balance
            today_bal_c = b.get("today_m",{}).get("balance_today", b["principal"])
            pmt_today_c = calc_pmt(today_bal_c, rate, ny, rem_c, ac) if rem_c > 0 else pmt_c
            sc_defs.append({"label":lbl,"rate":rate,"amort":amt,"freq":frq,
                            "lump":lump,"df":df_c,"summary":s_c,"payment":pmt_c,
                            "pmt_today":pmt_today_c,"rem":rem_c,"n_py":ny})

    st.divider()
    comp_rows = []
    for sc in sc_defs:
        s = sc["summary"]
        comp_rows.append({
            "Scenario":         sc["label"],
            "Rate":             f"{sc['rate']:.2f}%",
            "Amortization":     f"{sc['amort']} yrs",
            "Frequency":        sc["freq"],
            "Annual Lump":      f"${sc['lump']:,.0f}",
            "Original Payment": f"${sc['payment']:,.2f}",
            "Current Payment":  f"${sc['pmt_today']:,.2f}",
            "Remaining":        f"{sc['rem']:.1f} yrs",
            "Total Interest":   f"${s.get('total_interest',0):,.0f}",
            "Total Paid":       f"${s.get('total_paid',0):,.0f}",
        })
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    pal = ["#1a3c5e","#e74c3c","#27ae60","#f39c12"]
    fig_c = go.Figure()
    for i, sc in enumerate(sc_defs):
        if not sc["df"].empty:
            fig_c.add_scatter(x=sc["df"]["Date"], y=sc["df"]["Balance"],
                              name=sc["label"], line=dict(color=pal[i]))
    fig_c.update_layout(title="Balance Comparison (date x-axis)",
                        xaxis_title="Date", yaxis_title="($)", height=340)
    st.plotly_chart(fig_c, use_container_width=True, key="ch_cmpbal")

    # Three-layer stacked bar per scenario (side by side)
    tp_ref = b.get("today_m",{}).get("period_today",0)
    te_ref = int(b["term_years"]*b["n_py"])
    fig_cmp_bar = go.Figure()
    for i, sc in enumerate(sc_defs):
        if sc["df"].empty: continue
        g = sc["df"].groupby("Year").agg(
            Principal=("Principal","sum"), Interest=("Interest","sum")).reset_index()
        fig_cmp_bar.add_bar(x=g["Year"], y=g["Principal"],
                            name=f"{sc['label']} Principal",
                            marker_color=pal[i], opacity=0.9, legendgroup=sc["label"])
        fig_cmp_bar.add_bar(x=g["Year"], y=g["Interest"],
                            name=f"{sc['label']} Interest",
                            marker_color=pal[i], opacity=0.5, legendgroup=sc["label"])
    fig_cmp_bar.update_layout(barmode="stack",
                               title="Principal & Interest by Scenario",
                               xaxis_title="Year", yaxis_title="($)", height=360)
    st.plotly_chart(fig_cmp_bar, use_container_width=True, key="ch_cmpbar")

    best = min(range(len(sc_defs)),
               key=lambda i: sc_defs[i]["summary"].get("total_interest",1e12))
    worst_i = max(sc["summary"].get("total_interest",0) for sc in sc_defs)
    st.markdown(
        f'<div class="ok">🏆 <b>{sc_defs[best]["label"]}</b> saves '
        f'${worst_i-sc_defs[best]["summary"].get("total_interest",0):,.0f} · '
        f'Remaining: <b>{sc_defs[best]["rem"]:.1f} yrs</b></div>',
        unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# TAB 7 — SAVED SCENARIOS
# ══════════════════════════════════════════════════════════════════
with tabs[6]:
    st.subheader("💾 Saved Scenarios")
    require_setup()

    db_sc  = db_load_scenarios(st.session_state.db_conn)
    loc_sc = st.session_state.get("saved_scenarios", {})
    all_sc: dict = {}
    for s in db_sc:
        all_sc[f"[DB] {s['name']} ({s['created_at'][:16]})"] = s
    for nm, s in loc_sc.items():
        all_sc[f"[Local] {nm}"] = {"name":nm,"params":s.get("params",{}),"summary":s.get("summary",{})}

    if not all_sc:
        st.info("No saved scenarios. Use 💾 Save buttons in other tabs.")
    else:
        st.markdown(f"**{len(all_sc)} scenario(s)**")
        for key, sc in all_sc.items():
            with st.expander(key):
                s = sc["summary"]
                cc_ = st.columns(4)
                cc_[0].metric("Total Interest", f"${s.get('total_interest',0):,.0f}")
                cc_[1].metric("Payoff",          f"{s.get('payoff_years',0):.1f} yrs")
                cc_[2].metric("Payment",         f"${s.get('payment',0):,.2f}")
                cc_[3].metric("Total Prepaid",   f"${s.get('total_prepaid',0):,.0f}")
                show_raw = st.checkbox("Show raw params", False, key=f"raw_{hash(key)}")
                if show_raw: st.json(sc.get("params", {}))
                if "id" in sc:
                    if st.button("🗑️ Delete from DB", key=f"del_db_{sc['id']}"):
                        db_delete_scenario(st.session_state.db_conn, sc["id"])
                        st.rerun()

        if st.button("⬇️ Export CSV", key="btn_exp"):
            rows_e = [{"Scenario":k,**sc.get("summary",{})} for k,sc in all_sc.items()]
            st.download_button("Download", pd.DataFrame(rows_e).to_csv(index=False).encode(),
                               "scenarios.csv","text/csv", key="btn_dl_csv")

    st.divider()
    st.markdown("### 📚 Canadian Mortgage Education")
    with st.expander("🔢 Semi-Annual Compounding"):
        st.markdown("Rate 5.39% → Eff. annual: `(1+0.0539/2)²=5.463%` · "
                    "Monthly: `1.05463^(1/12)-1=0.4453%`\n\n"
                    "**Accelerated bi-weekly**: monthly÷2 every 2 wks → "
                    "~1 extra monthly pmt/yr → ~3 fewer years")
    with st.expander("🛡️ CMHC Insurance"):
        st.markdown("|Down%|Rate||Down%|Rate|\n|---|---|---|---|---|\n"
                    "|5–9.99|4.00%||15–19.99|2.80%|\n|10–14.99|3.10%||≥20|0%|")
    with st.expander("💔 Breaking Your Mortgage"):
        st.markdown("**Variable**: 3 months interest\n\n"
                    "**Fixed**: max(3mo interest, IRD)\n"
                    "- IRD = (orig posted − curr posted) × bal × remaining yrs\n"
                    "- Banks use posted (not discounted) rates\n"
                    "- Open mortgages: no penalty, ~1% rate premium")

    st.divider()
    st.markdown("### 📥 Fresh-Chat Recreation Prompt")
    st.caption("Download the prompt below to recreate this entire app from scratch in a new chat.")
    # FIX #4: Prompt as file only — not displayed on screen
    PROMPT_TEXT = """Build a single-file Canadian Mortgage Analyzer Streamlit app (app.py). Full specification:

STACK: Python, Streamlit, Pandas, NumPy, Plotly, python-dateutil, uuid, json, math.
MS SQL Server via pyodbc — MANDATORY, no optional mode.

DEFAULTS: DB server=localhost\\SQLEXPRESS, DB=MortgageDB, Windows Auth.
Mortgage: Price=$1,030,000 | Down=20% | Rate=5.39% | Amort=30yr | Term=3yr | Start=2023-08-15 | Monthly | Fixed.

MATH (Canadian semi-annual compounding — Interest Act):
  periodic_rate(annual_pct, n):
    eff_annual = (1 + annual_pct/200)**2      # TWO STEPS — parenthesize carefully
    return eff_annual**(1/n) - 1
  calc_pmt(principal, rate, n, amort_years, accel=False) → standard annuity
  Accelerated bi-weekly = monthly_pmt / 2 paid every 2 weeks
  CMHC: <10%=4%, <15%=3.1%, <20%=2.8%, >=20%=0%. Price <=1.5M. PST/HST 13% on premium.
  date_to_period(target, start, n) = max(1, round((target-start).days / 365.25 * n))
  period_to_date(period, start, n): n=12→+months, n=24→+15days, n=26→+2weeks, n=52→+1week
  build_amortization(principal, rate, n, amort_years, accel, start_date,
                     extra_payments, rate_changes, term_periods) → (DataFrame, summary_dict)
  get_today_metrics(df, n): last row where Date<=today → balance_today, principal_paid_today,
    interest_paid_today, period_today, remaining_years=(len(df)-period_today)/n
    CRITICAL: include past_extra in full_df before calling get_today_metrics
  calc_remaining_years(balance, rate, n, payment): log formula
  calc_break_penalty(bal, rate, mtype, orig_posted, curr_posted, months_left):
    returns {3_months_interest, ird (Fixed only), calc_penalty, method}
  stacked_bar_pi(df, today_p, term_end_p, title): Plotly stacked bar chart
    3 segments by period: past (grey), current-term (blue/red), post-term (faded)
    Aggregated by Year. Returns go.Figure with barmode="stack".
  _vline_x(v): convert date to ms-epoch for Plotly add_vline on date axis.

DB TABLES: mortgage_setup (single row, no name), mortgage_scenarios(id,name,created_at,params,summary)
db_save_setup: DELETE all rows then INSERT one (no setup name)
db_load_setup: SELECT TOP 1 setup_data FROM mortgage_setup ORDER BY id DESC

SESSION STATE: db_conn, setup_loaded, setup_data, saved_scenarios{}, rc_scenarios{},
               past_prepayments[], past_renewals[], base{}

GATE 1 — DB CONNECTION (full-page form, not sidebar):
  If st.session_state.db_conn is None: show centered connection form, st.stop()
  On connect: call _init_db, then db_load_setup; if data found → set setup_data + setup_loaded=True

GATE 2 — SETUP REQUIRED:
  def require_setup(): if not st.session_state.get("base"): st.info(...); st.stop()
  Call require_setup() at top of tabs 2-7.

7 TABS:

TAB1 (Setup & Overview):
  - Property: price, down%, CMHC advisory
  - Original term: type, freq, rate, amort, term, start_date
  - Populate widget defaults from db_load_setup if available, else hardcoded defaults
  - Past Renewals: add/delete (start_date, rate, type, term) → show end date → past_renewal_rcs
  - Past Prepayments: add/delete (exact date, amount) → date_to_period → past_extra
  - Build full_df WITH past_extra before calling get_today_metrics
  - Key Metrics right column: principal, payment, balance@term_end,
    3 GREEN cards: balance_today, principal_paid_today, interest_paid_today,
    remaining_amortization (from today), current_monthly_payment (on remaining balance+remaining amort),
    total interest, interest %, original payoff
  - Charts: principal vs interest donut + stacked_bar_pi (three-layer P&I)
  - Save Setup button → db_save_setup (single row, no name)
  - Store st.session_state["base"] dict

TAB2 (Amortization Schedule):
  - View mode: All Periods / Monthly / Yearly
  - Checkbox: "Show full schedule" (default False) → if False, slice df from (today_period - 3)
    so the table starts near the current date (FIX: auto-scroll to today)
  - Highlight current month: yellow #FFF3CD via pandas Styler.apply()
  - Balance chart with date x-axis, "Today" vline using _vline_x()
  - CSV download

TAB3 (Rate Change Scenarios):
  - New Scenario button → rc_scenarios{} session state
  - Each scenario: name, description, list of renewals
  - Quick templates via st.checkbox (NOT nested expander)
  - Each renewal: mode (By Date/By Period), type (Fixed/Variable), rate, term
    Show term start+end dates. 
  - EARLY RENEWAL DETECTION: if period < prev_term_end_p:
    → Show orig_posted + curr_posted inputs
    → Show ADVISORY info box with 3mo interest, IRD, calculated max
    → Show st.radio with choices: "3-Month Interest ($X)", "IRD ($X)", "Custom value"
      (FIX: user picks which to apply, or enters custom; no auto-apply)
    → Misc fees input; total exit cost; break-even months
  - NO custom payment override input; instead show:
    sc_pmt = calc_pmt(today_balance, last_renewal_rate, n_py, rem_yrs, accel)
    Display as metric "Payment at renewal rate"
  - Results: 5 metrics (base interest, scenario interest, base remaining, scenario remaining,
    payment at renewal rate)
    CRITICAL: remaining = (len(df) - today_period) / n_py — NOT original amort_years
  - stacked_bar_pi chart per scenario (three segments)
  - Rate over time chart (date x-axis)
  - Variable sub-scenarios (a,b,c): add sub-scenario button, n_changes, date+rate per change
  - Save button → saved_scenarios{} + DB

TAB4 (Prepayment Analysis):
  - Rate scenario selector (saved rate_change scenarios + base)
  - Annual lump-sum, regular payment increase, one-time
  - stacked_bar_pi chart for with-prepayment schedule
  - 6 metrics including remaining years
  - Save button

TAB5 (Break Penalty):
  - Inputs: balance, rate, type selectbox (key="bp_mtype_tab5"), months remaining, misc fees
  - If Fixed: orig_posted + curr_posted inputs
  - ADVISORY info: show 3mo interest, IRD, calculated max
  - st.radio "Apply which penalty?" with options:
    "3-Month Interest ($X)", "IRD ($X)" (if fixed), "Custom value"
    (FIX: user chooses, or enters custom amount)
  - Total exit cost = chosen_penalty + misc_fees
  - Break-even: new rate slider, stay vs break comparison, net savings chart, recoup months

TAB6 (Scenario Comparison):
  - 2/3/4 scenarios; each: label, rate, amort, freq, annual lump
  - NO custom payment override
  - Show "Original Payment" and "Current Payment (on remaining balance)" separately
  - "Remaining" column = calculated from today, not original amort_years
  - Balance chart (date x-axis)
  - Stacked bar P&I comparison (grouped by scenario + year)

TAB7 (Saved Scenarios):
  - DB + local scenarios; each in expander
  - "Show raw params" CHECKBOX (NOT nested expander)
  - Delete from DB button
  - Export CSV
  - Education: semi-annual compounding, CMHC, break penalty
  - Downloadable prompt.txt file (NOT displayed on screen — FIX)
    Use st.download_button with the full prompt text as a .txt file

UNIQUE KEYS: every widget has explicit unique key=. Loop widgets use uuid IDs.
NO NESTED EXPANDERS: use st.checkbox for sub-sections within expanders.
ALL CHARTS: use df["Date"] on x-axis. All add_vline calls use _vline_x() helper.
STACKED BAR: use stacked_bar_pi() everywhere instead of line charts for P&I breakdown.
COLORS: principal=#1a3c5e, interest=#e74c3c, savings=#27ae60.
TOOLTIPS: help= on all key inputs and metrics.
RUN: streamlit run app.py
"""
    st.download_button(
        "📥 Download Fresh-Chat Prompt (.txt)",
        data=PROMPT_TEXT.encode("utf-8"),
        file_name="mortgage_analyzer_prompt.txt",
        mime="text/plain",
        key="btn_dl_prompt",
    )
