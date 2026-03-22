"""
pages/tab_comparison.py — Side-by-Side Scenario Comparison.
FIXES vs original:
  - Function renamed: render(tabs_list) → render_tab_comparison(conn, b)
  - Imports changed from flat (from mortgage_X import) → from modules.X import
  - Schema fixed: sc_par / sc_db_match["id"] / "params" → 3NF renewals / db_id
  - db_update_scenario now calls correct signature from modules.mortgage_db
  - st.session_state.db_conn → passed conn parameter throughout
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules.mortgage_math import FREQ, calc_pmt, build_amortization
from modules.mortgage_charts import _vline_x
from modules.mortgage_db import db_load_scenarios, db_update_scenario

PROMPT_TEXT = r"""Build Canadian Mortgage Analyzer Streamlit app. MANDATORY MS SQL Server via pyodbc.
DEFAULTS: DB=localhost\\SQLEXPRESS/MortgageDB/Windows Auth.
Mortgage: $1,030,000 | 20% down | 5.39% | 30yr | 3yr term | 2023-08-15 | Monthly | Fixed.

MODULAR structure:
- app.py (entry, CSS, session state, DB gate, tab routing)
- modules/__init__.py  (re-exports all symbols from submodules)
- modules/mortgage_math.py (FREQ, periodic_rate, calc_pmt, build_amortization, get_today_metrics, etc.)
- modules/mortgage_db.py (DB helpers, reads setup_db.sql if present, includes db_update_scenario)
- modules/mortgage_charts.py (stacked_bar_pi — x=CalYear, legend at bottom, annotations at top)
- modules/mortgage_wireframe.py (generate_wireframe_docx via python-docx)
- pages/__init__.py (re-exports render_tab_* from submodules)
- pages/tab_setup.py        → render_tab_setup(conn)
- pages/tab_scenarios.py    → render_tab_scenarios(conn, b)
- pages/tab_prepayment.py   → render_tab_prepayment(conn, b)
- pages/tab_schedule.py     → render_tab_schedule(conn, b)
- pages/tab_comparison.py   → render_tab_comparison(conn, b)

KEY FIXES:
- FIX #1: save uses if/else not ternary (TypeError)
- FIX #2: metrics: Row1=Current Interest/Remaining/Required Payment; Row2=Adjusted green boxes
- FIX #3: CSS text colors explicitly dark on light-bg notification divs
- FIX #5: DB init reads setup_db.sql from app folder
- FIX #6: Chart legend at bottom, annotations (Today/Term End) at top with bgcolor boxes
- FIX #7: calc_monthly_sc = calc_pmt(today_bal, last_rate, n_py, base_remaining_yrs)
- FIX #8: All page functions use (conn, b) signature; no flat imports; 3NF schema throughout

TAB ORDER: Setup | Rate Change Scenarios | Prepayment Analysis | Amortization Schedule | Comparison.
RUN: streamlit run app.py
"""


def render_tab_comparison(conn, b):
    st.subheader("🔄 Side-by-Side Scenario Comparison")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        return

    # Load saved DB scenarios (3NF schema: each has db_id, name, renewals, pp)
    db_sc_cmp = db_load_scenarios(conn)
    sc_option_names = ["Current Mortgage (base)"] + [s["name"] for s in db_sc_cmp]

    n_sc = st.radio(
        "Number of scenarios to compare", [2, 3, 4],
        horizontal=True, key="cmp_n",
        help="How many columns to show side by side"
    )
    st.caption(
        "Each column loads saved scenario values by default. "
        "Edit Rate or Label and click **💾 Save Changes** to persist updates."
    )

    sc_defs = []
    cols = st.columns(int(n_sc))

    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**Scenario {i+1}**")
            default_opt = 0 if i == 0 else min(i, len(sc_option_names) - 1)
            sc_pick = st.selectbox(
                "Source", sc_option_names,
                index=default_opt, key=f"cmp_src_{i}",
                help="Choose current mortgage or a saved DB scenario"
            )

            is_base = (sc_pick == "Current Mortgage (base)")

            if is_base:
                # ── Base mortgage ──────────────────────────────────────
                def_rate     = float(b["annual_rate"])
                def_amort    = int(b["amort_years"])
                def_freq     = b["payment_freq"]
                def_label    = "Current Mortgage"
                def_rcs      = b.get("past_renewal_rcs") or []
                sc_db_id     = None
                sc_db_match  = None
                sc_renewals  = []
                sc_pp        = {}
            else:
                # ── Saved DB scenario (3NF schema) ─────────────────────
                sc_db_match = next((s for s in db_sc_cmp if s["name"] == sc_pick), None)
                sc_renewals = sc_db_match["renewals"] if sc_db_match else []
                sc_pp       = sc_db_match.get("pp", {}) if sc_db_match else {}
                # Rate: from last renewal; amort/freq not stored per-scenario → use base
                def_rate  = float(sc_renewals[-1]["new_rate"]) if sc_renewals else float(b["annual_rate"])
                def_amort = int(b["amort_years"])
                def_freq  = b["payment_freq"]
                def_label = sc_pick
                # Rate changes = base past renewals + scenario renewals
                def_rcs   = (b.get("past_renewal_rcs") or []) + [
                    {"period": rn["period"], "new_rate": rn["new_rate"]}
                    for rn in sc_renewals
                ]
                sc_db_id  = sc_db_match["db_id"] if sc_db_match else None

            # ── Editable fields ────────────────────────────────────────
            lbl = st.text_input(
                "Label", def_label, key=f"cmp_lbl_{i}",
                help="Display name for this scenario column"
            )
            rate = st.number_input(
                "Rate (%)", 0.5, 20.0, float(def_rate), 0.01,
                key=f"cmp_rate_{i}", format="%.2f",
                help="Interest rate to model; edit to override the scenario rate"
            )
            amt = st.slider(
                "Amort (yrs)", 5, 30, int(def_amort),
                key=f"cmp_amt_{i}",
                help="Amortization period; editable for comparison only (not persisted)"
            )
            st.caption(
                f"📅 Frequency: **{def_freq}** (inherited from base setup)",
                help="Payment frequency comes from the base setup — edit there to change it"
            )

            # ── Save Changes — only for real DB scenarios ──────────────
            if not is_base and sc_db_id is not None and sc_db_match is not None:
                sv1, sv2 = st.columns([1, 2])
                if sv1.button("💾 Save Changes", key=f"cmp_save_{i}",
                              help="Persist label and rate edits back to this DB scenario"):
                    # Update last renewal rate if user changed it
                    updated_renewals = [dict(rn) for rn in sc_renewals]  # deep-ish copy
                    if updated_renewals and abs(float(updated_renewals[-1]["new_rate"]) - rate) > 0.001:
                        updated_renewals[-1]["new_rate"] = rate
                    ok = db_update_scenario(
                        conn, sc_db_id, lbl,
                        sc_db_match.get("desc", ""),
                        updated_renewals, sc_pp
                    )
                    if ok:
                        sv2.success("✅ Saved")
                    else:
                        sv2.error("❌ Save failed")

            # ── Build amortization for this column ─────────────────────
            fc_  = FREQ.get(def_freq, FREQ["Monthly"])
            ny   = fc_["n"]
            ac   = fc_["accel"]

            df_c, s_c = build_amortization(
                b["principal"], rate, ny, amt,
                accel=ac, start_date=b["start_date"],
                extra_payments=b.get("past_extra") or None,
                rate_changes=def_rcs or None,
            )
            pmt_c     = calc_pmt(b["principal"], rate, ny, amt, ac)
            tp_c      = b["today_m"].get("period_today", 0)
            rem_c     = round((len(df_c) - tp_c) / ny, 1) if tp_c > 0 and not df_c.empty else amt
            today_bal = b["today_m"].get("balance_today", b["principal"])
            pmt_today = calc_pmt(today_bal, rate, ny, rem_c, ac) if rem_c > 0 else pmt_c

            sc_defs.append({
                "label":     lbl,
                "rate":      rate,
                "amort":     amt,
                "freq":      def_freq,
                "df":        df_c,
                "summary":   s_c,
                "payment":   pmt_c,
                "pmt_today": pmt_today,
                "rem":       rem_c,
                "n_py":      ny,
            })

    # ── Comparison table ───────────────────────────────────────────────
    st.divider()
    comp_rows = []
    for sc in sc_defs:
        s = sc["summary"]
        comp_rows.append({
            "Scenario":        sc["label"],
            "Rate":            f"{sc['rate']:.2f}%",
            "Amort":           f"{sc['amort']} yrs",
            "Orig Payment":    f"${sc['payment']:,.2f}",
            "Current Payment": f"${sc['pmt_today']:,.2f}",
            "Remaining":       f"{sc['rem']:.1f} yrs",
            "Total Interest":  f"${s.get('total_interest', 0):,.0f}",
            "Total Paid":      f"${s.get('total_paid', 0):,.0f}",
        })
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    # ── Balance comparison chart ───────────────────────────────────────
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

    # ── Annual P&I stacked bar ─────────────────────────────────────────
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

    # ── Best scenario callout ──────────────────────────────────────────
    best = min(
        range(len(sc_defs)),
        key=lambda i: sc_defs[i]["summary"].get("total_interest", 1e12)
    )
    worst_int = max(sc["summary"].get("total_interest", 0) for sc in sc_defs)
    st.markdown(
        f'<div class="ok">🏆 <b>{sc_defs[best]["label"]}</b> saves '
        f'${worst_int - sc_defs[best]["summary"].get("total_interest", 0):,.0f} in interest '
        f'· Remaining: <b>{sc_defs[best]["rem"]:.1f} yrs</b></div>',
        unsafe_allow_html=True
    )

    # ── Prompt download ────────────────────────────────────────────────
    st.divider()
    st.download_button(
        "📥 Download Fresh-Chat Prompt (.txt)",
        data=PROMPT_TEXT.encode("utf-8"),
        file_name="mortgage_analyzer_prompt.txt",
        mime="text/plain",
        key="btn_dl_prompt",
        help="Copy this prompt into a new chat to recreate this exact app"
    )
