"""
tab_comparison.py — Side-by-Side Scenario Comparison
Changes vs previous:
- Scenario columns pre-populated from saved DB values (rate, amort, label)
- Removed Annual Lump and Frequency inputs (inherited from saved scenario params)
- Users can edit Rate and Amort per column, then save changes back to DB
- Comparison table drops Frequency / Annual Lump columns
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from mortgage_math import FREQ, calc_pmt, build_amortization
from mortgage_charts import _vline_x
from mortgage_db import db_load_scenarios, db_update_scenario

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
Current Remaining Amortization shows: actual_payoff yrs (len(full_df)/n_py), elapsed yrs,
remaining yrs, end date. NO "Original Payoff" metric.

COMPARISON TAB:
- Loads saved DB scenarios by default (pre-populated rate/amort/label from DB params)
- NO Frequency or Annual Lump inputs — those are inherited from each scenario's saved params
- Editable per column: Label, Rate (%), Amort (yrs)
- Show inherited Frequency as read-only caption
- Add "Save Changes to DB" button per DB scenario column
- Comparison table: Scenario | Rate | Amort | Orig Payment | Current Payment | Remaining | Total Interest | Total Paid
- Balance chart + Annual P&I stacked bar + best-scenario callout
- Download Prompt (.txt) button

RUN: streamlit run app.py
"""


def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        st.stop()


def _scenario_rate(sc_par, fallback):
    rcs = sc_par.get("rate_changes") or []
    if rcs:
        return float(rcs[-1].get("new_rate", fallback))
    return float(sc_par.get("sc_rate", sc_par.get("annual_rate", fallback)))


def render(tabs_list):
    with tabs_list[5]:
        st.subheader("🔄 Side-by-Side Scenario Comparison")
        require_setup()
        b = st.session_state["base"]

        db_sc_cmp = db_load_scenarios(st.session_state.db_conn)
        sc_option_names = ["Current Mortgage (base)"] + [s["name"] for s in db_sc_cmp]

        n_sc = st.radio(
            "Number of scenarios to compare", [2, 3, 4],
            horizontal=True, key="cmp_n"
        )
        st.caption(
            "Each column loads saved scenario values by default. "
            "Edit Rate or Amortization and click **💾 Save Changes** to persist updates."
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
                sc_db_match = None

                if is_base:
                    def_rate  = float(b["annual_rate"])
                    def_amort = int(b["amort_years"])
                    def_freq  = b["payment_freq"]
                    def_label = "Current Mortgage"
                    def_rcs   = b.get("past_renewal_rcs") or []
                    sc_db_id  = None
                    sc_par    = {}
                else:
                    sc_db_match = next((s for s in db_sc_cmp if s["name"] == sc_pick), None)
                    sc_par    = sc_db_match.get("params", {}) if sc_db_match else {}
                    def_rate  = _scenario_rate(sc_par, b["annual_rate"])
                    def_amort = int(sc_par.get("amort_years", b["amort_years"]))
                    def_freq  = sc_par.get("payment_freq", b["payment_freq"])
                    def_label = sc_pick
                    def_rcs   = (b.get("past_renewal_rcs") or []) + (sc_par.get("rate_changes") or [])
                    sc_db_id  = sc_db_match["id"] if sc_db_match else None

                # ── Editable fields ────────────────────────────────────────
                lbl = st.text_input(
                    "Label", def_label, key=f"cmp_lbl_{i}",
                    help="Display name for this scenario"
                )
                rate = st.number_input(
                    "Rate (%)", 0.5, 20.0, float(def_rate), 0.01,
                    key=f"cmp_rate_{i}", format="%.2f",
                    help="Interest rate — edit to model a different rate"
                )
                amt = st.slider(
                    "Amort (yrs)", 5, 30, int(def_amort),
                    key=f"cmp_amt_{i}",
                    help="Amortization period — edit to model a different term"
                )
                # Frequency is read-only — comes from the saved scenario
                st.caption(
                    f"📅 Frequency: **{def_freq}** (from saved scenario)",
                    help="Payment frequency is inherited from the saved scenario and not overridable here"
                )

                # ── Save Changes button — only for DB scenarios ────────────
                if not is_base and sc_db_id is not None:
                    sv1, sv2 = st.columns([1, 2])
                    if sv1.button("💾 Save Changes", key=f"cmp_save_{i}",
                                  help="Persist rate/amort edits back to this DB scenario"):
                        updated_params = dict(sc_par)
                        updated_params["amort_years"] = amt
                        # Push updated rate into the last rate_change entry, or sc_rate
                        if updated_params.get("rate_changes"):
                            updated_params["rate_changes"][-1]["new_rate"] = rate
                        else:
                            updated_params["sc_rate"] = rate
                        # Rebuild summary so stored data stays consistent
                        fc_u = FREQ.get(def_freq, FREQ["Monthly"])
                        _, upd_sum = build_amortization(
                            b["principal"], rate, fc_u["n"], amt,
                            accel=fc_u["accel"], start_date=b["start_date"],
                            extra_payments=b.get("past_extra") or None,
                            rate_changes=def_rcs or None,
                        )
                        ok = db_update_scenario(
                            st.session_state.db_conn,
                            sc_db_id, lbl, updated_params, upd_sum
                        )
                        if ok:
                            sv2.success("✅ Saved")
                        else:
                            sv2.error("❌ Save failed")

                # ── Build amortization ─────────────────────────────────────
                fc_ = FREQ.get(def_freq, FREQ["Monthly"])
                ny  = fc_["n"]
                ac  = fc_["accel"]

                df_c, s_c  = build_amortization(
                    b["principal"], rate, ny, amt,
                    accel=ac, start_date=b["start_date"],
                    extra_payments=b.get("past_extra") or None,
                    rate_changes=def_rcs or None,
                )
                pmt_c      = calc_pmt(b["principal"], rate, ny, amt, ac)
                tp_c       = b["today_m"].get("period_today", 0)
                rem_c      = round((len(df_c) - tp_c) / ny, 1) if tp_c > 0 and not df_c.empty else amt
                today_bal  = b["today_m"].get("balance_today", b["principal"])
                pmt_today  = calc_pmt(today_bal, rate, ny, rem_c, ac) if rem_c > 0 else pmt_c

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
