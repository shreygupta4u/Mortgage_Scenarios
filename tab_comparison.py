"""
tabs/tab_comparison.py — Side-by-Side Scenario Comparison tab
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from mortgage_math import FREQ, calc_pmt, build_amortization
from mortgage_charts import _vline_x
from mortgage_db import db_load_scenarios

PROMPT_TEXT = r"""Build Canadian Mortgage Analyzer Streamlit app (app.py) — modular structure.
MANDATORY MS SQL Server via pyodbc.

DEFAULTS: DB=localhost\SQLEXPRESS / MortgageDB / Windows Auth.
Mortgage: $1,030,000 | 20% down | 5.39% | 30yr amort | 3yr term | 2023-08-15 | Monthly | Fixed.

MATH: periodic_rate two-step: eff=((1+r/200)**2); return eff**(1/n)-1. Build CalYear column in df.

DB: mortgage_setup (single row), mortgage_scenarios(id,name,created_at,params,summary).
DB-ONLY scenarios (no local duplicates).
FIX #5: On connect, if tables not found, run setup_db.sql from script folder before inline DDL.

TAB ORDER: Setup | Rate Change Scenarios | Amortization Schedule | Prepayment | Break Penalty | Comparison.

SETUP: Sections A(Purchase/Down) B(Mortgage Terms) C(Past Renewals collapsable) D(Past Prepayments collapsable).
KEY METRICS: Initial Principal, Balance@TermEnd, Balance Today, Principal Paid, Interest Paid,
Current Remaining Amortization (yrs + end date), Total Interest, Original Amortization Period,
Current Monthly Payment. NO "Original Payoff" metric.

STACKED BAR: stacked_bar_pi(df,today_p,term_end_p,title): x=CalYear strings, 3 colour segments
(past=grey, current=blue/red, post=faded).
FIX #6: Legend at BOTTOM (y=-0.22), annotations at y=1.06/1.13 paper coords.
No add_vline on categorical axis.

AMORTIZATION: Hierarchy toggle default: Past/Current(last4+today)/Future segments.
Full schedule ends at actual payoff period (len(df)).

SCENARIO METRICS: base_remaining = today_m["remaining_years"] not amort_years.
FIX #2: Show Base Interest / Current Remaining / Base Payment alongside Adjusted Interest /
Adjusted Remaining / Mortgage-Free By in parallel two-column green box layout.

COMPARISON: first scenario defaults to "Current Mortgage (base)"; all saved DB scenarios selectable.

WORD WIREFRAME: generate_wireframe_docx() using python-docx. Download button on setup page.

PENALTY: radio 3-Month/IRD/Custom + inline text_input for custom.

ALL METRICS: help= tooltips.
ALL CHARTS: CalYear string or date x-axis (never period numbers).

FIX #1: Save scenario — use explicit if/else NOT ternary expression to avoid TypeError.
FIX #3: Dark mode — add !important to .mc text colors so they contrast against light background.
FIX #4: Modular code — mortgage_math.py, mortgage_db.py, mortgage_charts.py,
mortgage_wireframe.py, tabs/tab_setup.py, tabs/tab_scenarios.py,
tabs/tab_amortization.py, tabs/tab_prepayment.py, tabs/tab_breakpenalty.py,
tabs/tab_comparison.py.

MODULAR ENTRY: app.py imports and calls each tab module's render(tabs_list) function.

RUN: streamlit run app.py
"""


def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        st.stop()


def render(tabs_list):
    with tabs_list[5]:
        st.subheader("🔄 Side-by-Side Scenario Comparison")
        require_setup()
        b = st.session_state["base"]

        n_sc = st.radio(
            "Number of scenarios to compare", [2, 3, 4],
            horizontal=True, key="cmp_n"
        )
        db_sc_cmp = db_load_scenarios(st.session_state.db_conn)
        sc_option_names = ["Current Mortgage (base)"] + [s["name"] for s in db_sc_cmp]

        sc_defs = []
        cols = st.columns(int(n_sc))
        for i, col in enumerate(cols):
            with col:
                st.markdown(f"**Scenario {i+1}**")
                default_opt = 0 if i == 0 else min(i, len(sc_option_names) - 1)
                sc_pick = st.selectbox(
                    f"Scenario {i+1} source", sc_option_names,
                    index=default_opt, key=f"cmp_src_{i}",
                    help="Choose current mortgage or a saved scenario"
                )

                if sc_pick == "Current Mortgage (base)":
                    rate = float(b["annual_rate"])
                    amt = b["amort_years"]
                    frq = b["payment_freq"]
                    lbl = "Current Mortgage"
                    rc_list = b.get("past_renewal_rcs") or []
                else:
                    sc_match = next((s for s in db_sc_cmp if s["name"] == sc_pick), {})
                    sc_par = sc_match.get("params", {})
                    if sc_par.get("rate_changes"):
                        rate = float(
                            sc_par.get("rate_changes", [{}])[-1].get("new_rate", b["annual_rate"])
                        )
                    else:
                        rate = float(sc_par.get("sc_rate", b["annual_rate"]))
                    amt = sc_par.get("amort_years", b["amort_years"])
                    frq = sc_par.get("payment_freq", b["payment_freq"])
                    rc_list = (b.get("past_renewal_rcs") or []) + (sc_par.get("rate_changes") or [])
                    lbl = sc_pick

                lbl = st.text_input("Label", lbl, key=f"cmp_lbl_{i}")
                rate = st.number_input(
                    "Rate (%)", 0.5, 20.0, float(rate), 0.01,
                    key=f"cmp_rate_{i}", format="%.2f"
                )
                amt = st.slider("Amort (yrs)", 5, 30, int(amt), key=f"cmp_amt_{i}")
                frq = st.selectbox(
                    "Frequency", list(FREQ.keys()),
                    index=list(FREQ.keys()).index(b["payment_freq"]),
                    key=f"cmp_frq_{i}"
                )
                lump = st.number_input(
                    "Annual lump ($)", 0, 200_000, 0, 1_000, key=f"cmp_lump_{i}"
                )

                fc_ = FREQ[frq]
                ny = fc_["n"]
                ac = fc_["accel"]
                ex = list(b.get("past_extra", []))
                if lump > 0:
                    for yr in range(1, amt + 1):
                        ex.append({"period": max(1, int((yr - 1) * ny + ny // 2)), "amount": float(lump)})

                df_c, s_c = build_amortization(
                    b["principal"], rate, ny, amt,
                    accel=ac, start_date=b["start_date"],
                    extra_payments=ex or None,
                    rate_changes=rc_list or None,
                )
                pmt_c = calc_pmt(b["principal"], rate, ny, amt, ac)
                tp_c = b["today_m"].get("period_today", 0)
                rem_c = round((len(df_c) - tp_c) / ny, 1) if tp_c > 0 and not df_c.empty else amt
                today_bal_c = b["today_m"].get("balance_today", b["principal"])
                pmt_today_c = (
                    calc_pmt(today_bal_c, rate, ny, rem_c, ac) if rem_c > 0 else pmt_c
                )
                sc_defs.append({
                    "label": lbl, "rate": rate, "amort": amt, "freq": frq,
                    "lump": lump, "df": df_c, "summary": s_c,
                    "payment": pmt_c, "pmt_today": pmt_today_c,
                    "rem": rem_c, "n_py": ny,
                })

        st.divider()
        comp_rows = []
        for sc in sc_defs:
            s = sc["summary"]
            comp_rows.append({
                "Scenario": sc["label"],
                "Rate": f"{sc['rate']:.2f}%",
                "Amort": f"{sc['amort']} yrs",
                "Frequency": sc["freq"],
                "Annual Lump": f"${sc['lump']:,.0f}",
                "Orig Payment": f"${sc['payment']:,.2f}",
                "Current Payment": f"${sc['pmt_today']:,.2f}",
                "Remaining": f"{sc['rem']:.1f} yrs",
                "Total Interest": f"${s.get('total_interest', 0):,.0f}",
                "Total Paid": f"${s.get('total_paid', 0):,.0f}",
            })
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

        pal = ["#1a3c5e", "#e74c3c", "#27ae60", "#f39c12"]
        fig_c = go.Figure()
        for i, sc in enumerate(sc_defs):
            if not sc["df"].empty:
                fig_c.add_scatter(
                    x=sc["df"]["Date"], y=sc["df"]["Balance"],
                    name=sc["label"], line=dict(color=pal[i])
                )

        full_df_ref = b.get("full_df")
        if full_df_ref is not None and b["today_m"].get("period_today"):
            tp = b["today_m"]["period_today"]
            if not full_df_ref.empty and tp <= len(full_df_ref):
                td_d2 = full_df_ref["Date"].iloc[tp - 1]
                fig_c.add_vline(
                    x=_vline_x(td_d2), line_dash="dash", line_color="#27ae60",
                    annotation_text="Today", annotation_position="top right"
                )

        fig_c.update_layout(
            title="Balance Comparison",
            xaxis_title="Date", yaxis_title="($)",
            height=340, margin=dict(t=60, b=40)
        )
        st.plotly_chart(fig_c, use_container_width=True, key="ch_cmpbal")

        fig_cmp_bar = go.Figure()
        for i, sc in enumerate(sc_defs):
            if sc["df"].empty:
                continue
            g = (
                sc["df"]
                .groupby("CalYear")
                .agg(Principal=("Principal", "sum"), Interest=("Interest", "sum"))
                .reset_index()
            )
            fig_cmp_bar.add_bar(
                x=g["CalYear"].astype(str), y=g["Principal"],
                name=f"{sc['label']} P", marker_color=pal[i],
                opacity=0.9, legendgroup=sc["label"]
            )
            fig_cmp_bar.add_bar(
                x=g["CalYear"].astype(str), y=g["Interest"],
                name=f"{sc['label']} I", marker_color=pal[i],
                opacity=0.5, legendgroup=sc["label"]
            )
        fig_cmp_bar.update_layout(
            barmode="stack", title="Annual P & I by Scenario",
            xaxis_title="Year", yaxis_title="($)",
            height=360, margin=dict(t=70, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig_cmp_bar, use_container_width=True, key="ch_cmpbar")

        best = min(range(len(sc_defs)),
                   key=lambda i: sc_defs[i]["summary"].get("total_interest", 1e12))
        worst_i = max(sc["summary"].get("total_interest", 0) for sc in sc_defs)
        st.markdown(
            f'<div class="ok">🏆 <b>{sc_defs[best]["label"]}</b> saves '
            f'${worst_i - sc_defs[best]["summary"].get("total_interest", 0):,.0f} · '
            f'Remaining: <b>{sc_defs[best]["rem"]:.1f} yrs</b></div>',
            unsafe_allow_html=True
        )

        st.divider()
        st.download_button(
            "📥 Download Fresh-Chat Prompt (.txt)",
            data=PROMPT_TEXT.encode("utf-8"),
            file_name="mortgage_analyzer_prompt.txt",
            mime="text/plain",
            key="btn_dl_prompt",
            help="Copy this prompt into a new chat to recreate this exact app"
        )
