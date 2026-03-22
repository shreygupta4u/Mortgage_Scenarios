"""pages/tab_comparison.py — REQ #8: Read-only comparison; Edit opens same popup as scenarios tab."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules import (
    FREQ, calc_pmt, build_amortization,
    db_load_scenarios, db_load_prepay_scenarios,
)
from modules.mortgage_charts import _vline_x
from pages.scenario_editor import compute_scenario, edit_scenario_dialog


def _pct(part, total):
    return f"{part/total*100:.1f}%" if total else "—"


def render_tab_comparison(conn, b):
    st.subheader("🔄 Side-by-Side Scenario Comparison")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    # Load scenarios & prepay scenarios
    db_sc_cmp = db_load_scenarios(conn)
    pp_scs    = db_load_prepay_scenarios(conn)
    sc_names  = ["Current Mortgage (base)"] + [s["name"] for s in db_sc_cmp]
    pp_names  = ["None (no prepayment)"]    + [s["name"] for s in pp_scs]

    # Ensure rc_scenarios is populated (needed for edit dialog)
    if not st.session_state.get("sc_loaded_from_db"):
        import uuid
        rcs = {}
        for i, sc_row in enumerate(db_sc_cmp, 1):
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            rcs[sc_row["_key"]] = sc_row
        st.session_state.rc_scenarios  = rcs
        st.session_state.sc_loaded_from_db = True

    n_sc = st.radio("Scenarios to compare:", [2, 3, 4], horizontal=True, key="cmp_n")

    sc_defs = []
    cols    = st.columns(int(n_sc))

    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**Column {i+1}**")
            default_idx = 0 if i == 0 else min(i, len(sc_names)-1)
            sc_pick = st.selectbox("Scenario", sc_names, index=default_idx,
                                   key=f"cmp_src_{i}", help="Choose a saved scenario")
            pp_pick = st.selectbox("Prepayment", pp_names, index=0,
                                   key=f"cmp_pp_{i}", help="Optionally layer a prepayment plan")

            is_base      = (sc_pick == "Current Mortgage (base)")
            sc_db_match  = next((s for s in db_sc_cmp if s["name"] == sc_pick), None) if not is_base else None
            sel_pp       = next((s for s in pp_scs if s["name"] == pp_pick), None)

            if is_base:
                lbl       = "Current Mortgage"
                def_rate  = float(b["annual_rate"])
                def_amort = int(b["amort_years"])
                def_freq  = b["payment_freq"]
                def_rcs   = b.get("past_renewal_rcs") or []
                sc_dict   = {"renewals": [], "name": lbl, "user_pmt": 0, "pp": {}}
                sc_db_key = None
            else:
                lbl        = sc_db_match["name"] if sc_db_match else sc_pick
                renewals   = sc_db_match["renewals"] if sc_db_match else []
                def_rate   = float(renewals[-1]["new_rate"]) if renewals else float(b["annual_rate"])
                def_amort  = int(b["amort_years"])
                def_freq   = b["payment_freq"]
                def_rcs    = (b.get("past_renewal_rcs") or []) + [
                    {"period": rn["period"], "new_rate": rn["new_rate"]} for rn in renewals
                ]
                sc_dict    = sc_db_match if sc_db_match else {"renewals": [], "name": lbl, "user_pmt": 0, "pp": {}}
                # Find _key in rc_scenarios for edit dialog
                sc_db_key  = next((k for k, v in st.session_state.get("rc_scenarios", {}).items()
                                   if v.get("db_id") == sc_db_match.get("db_id")), None) if sc_db_match else None

            # REQ #8: Read-only display
            st.markdown(f"**{lbl}**")
            st.caption(
                f"Rate: **{def_rate:.2f}%** · Amort: **{def_amort} yrs** · Freq: **{def_freq}**"
            )
            if sc_db_match and sc_db_match.get("desc"):
                st.caption(sc_db_match["desc"])
            if pp_pick != "None (no prepayment)" and sel_pp:
                s = sel_pp.get("settings", {})
                pp_parts = []
                if s.get("annual_lump"): pp_parts.append(f"${s['annual_lump']:,.0f}/yr")
                if s.get("pay_increase_type","None") != "None": pp_parts.append(f"+{s.get('pay_increase_val',0)}% pmt")
                if s.get("onetime_amount"): pp_parts.append(f"one-time ${s['onetime_amount']:,.0f}")
                st.caption(f"💰 PP: {', '.join(pp_parts) or 'n/a'}")

            # Edit button opens the shared dialog
            if not is_base and sc_db_key and sc_db_key in st.session_state.get("rc_scenarios", {}):
                if st.button("✏️ Edit Scenario", key=f"cmp_edit_{i}", use_container_width=True,
                              help="Opens the full edit popup"):
                    st.session_state["_editing_sc_id"] = sc_db_key

            # Build amortization
            fc_  = FREQ.get(def_freq, FREQ["Monthly"])
            ny   = fc_["n"]
            ac   = fc_["accel"]
            df_c, s_c, all_rcs, sc_extra, last_rate, _ = compute_scenario(sc_dict, b, sel_pp)
            pmt_c     = calc_pmt(b["principal"], def_rate, ny, def_amort, ac)
            tp_c      = b["today_m"].get("period_today", 0)
            rem_c     = round((len(df_c) - tp_c) / ny, 1) if tp_c > 0 and not df_c.empty else def_amort
            today_bal = b["today_m"].get("balance_today", b["principal"])
            pmt_today = calc_pmt(today_bal, def_rate, ny, rem_c, ac) if rem_c > 0 else pmt_c
            sc_int    = s_c.get("total_interest", 0)
            sc_total  = sc_int + b["principal"]

            sc_defs.append({
                "label": lbl, "rate": def_rate, "amort": def_amort, "freq": def_freq,
                "df": df_c, "summary": s_c, "payment": pmt_c, "pmt_today": pmt_today,
                "rem": rem_c, "n_py": ny, "sc_int": sc_int, "sc_total": sc_total,
            })

    # ── Comparison table ──────────────────────────────────────────
    st.divider()
    comp_rows = []
    for sc in sc_defs:
        s = sc["summary"]
        comp_rows.append({
            "Scenario":       sc["label"],
            "Rate":           f"{sc['rate']:.2f}%",
            "Amort":          f"{sc['amort']} yrs",
            "Orig Payment":   f"${sc['payment']:,.2f}",
            "Curr Payment":   f"${sc['pmt_today']:,.2f}",
            "Remaining":      f"{sc['rem']:.1f} yrs",
            "Total Interest": f"${sc['sc_int']:,.0f}",
            "Int % of P+I":   _pct(sc['sc_int'], sc['sc_total']),
            "Total P+I":      f"${sc['sc_total']:,.0f}",
        })
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    # ── Balance chart ─────────────────────────────────────────────
    pal = ["#1a3c5e", "#e74c3c", "#27ae60", "#f39c12"]
    fig_c = go.Figure()
    for i, sc in enumerate(sc_defs):
        if not sc["df"].empty:
            fig_c.add_scatter(x=sc["df"]["Date"], y=sc["df"]["Balance"],
                               name=sc["label"], line=dict(color=pal[i]))
    full_df = b.get("full_df")
    if full_df is not None and b["today_m"].get("period_today"):
        tp = b["today_m"]["period_today"]
        if not full_df.empty and tp <= len(full_df):
            fig_c.add_vline(x=_vline_x(full_df["Date"].iloc[tp-1]),
                             line_dash="dash", line_color="#27ae60",
                             annotation_text="Today", annotation_position="top right")
    fig_c.update_layout(title="Balance Comparison", xaxis_title="Date", yaxis_title="($)",
                         height=340, margin=dict(t=60, b=40))
    st.plotly_chart(fig_c, use_container_width=True, key="ch_cmpbal")

    # ── Annual P&I bar ────────────────────────────────────────────
    fig_bar = go.Figure()
    for i, sc in enumerate(sc_defs):
        if sc["df"].empty: continue
        g = sc["df"].groupby("CalYear").agg(
            Principal=("Principal","sum"), Interest=("Interest","sum")).reset_index()
        fig_bar.add_bar(x=g["CalYear"].astype(str), y=g["Principal"],
                         name=f"{sc['label']} P", marker_color=pal[i], opacity=0.9,
                         legendgroup=sc["label"])
        fig_bar.add_bar(x=g["CalYear"].astype(str), y=g["Interest"],
                         name=f"{sc['label']} I", marker_color=pal[i], opacity=0.5,
                         legendgroup=sc["label"])
    fig_bar.update_layout(barmode="stack", title="Annual P & I by Scenario",
                           xaxis_title="Year", yaxis_title="($)", height=360,
                           margin=dict(t=70, b=40),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(fig_bar, use_container_width=True, key="ch_cmpbar")

    # ── Best scenario ─────────────────────────────────────────────
    best      = min(range(len(sc_defs)), key=lambda i: sc_defs[i]["sc_int"])
    worst_int = max(sc["sc_int"] for sc in sc_defs)
    st.markdown(
        f'<div class="ok">🏆 <b>{sc_defs[best]["label"]}</b> saves '
        f'${worst_int - sc_defs[best]["sc_int"]:,.0f} in interest '
        f'· Remaining: <b>{sc_defs[best]["rem"]:.1f} yrs</b></div>',
        unsafe_allow_html=True,
    )

    # ── Open edit dialog if triggered ─────────────────────────────
    editing_id = st.session_state.get("_editing_sc_id")
    if editing_id and editing_id in st.session_state.get("rc_scenarios", {}):
        edit_scenario_dialog()
