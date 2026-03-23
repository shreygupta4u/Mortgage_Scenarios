"""pages/tab_comparison.py — REQ #7: per-scenario P&I charts side by side + P&I from rate scenarios."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules import FREQ, calc_pmt, build_amortization, db_load_scenarios, db_load_prepay_scenarios
from modules.mortgage_charts import stacked_bar_pi, _vline_x
from pages.scenario_editor import compute_scenario, edit_scenario_dialog, _get_linked_pp


def _pct(part, total):
    return f"{part/total*100:.1f}%" if total else "—"

def _mc(title, value, subtitle="", cls="mc", tooltip=""):
    tip = f'title="{tooltip}"' if tooltip else ""
    return (f'<div class="{cls}" {tip} style="cursor:default">'
            f'<h3>{title}</h3><p>{value}</p>'
            + (f'<small>{subtitle}</small>' if subtitle else '') + '</div>')


def render_tab_comparison(conn, b):
    st.subheader("🔄 Side-by-Side Scenario Comparison")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    db_sc_cmp = db_load_scenarios(conn)
    pp_scs    = db_load_prepay_scenarios(conn)
    sc_names  = ["Current Mortgage (base)"] + [s["name"] for s in db_sc_cmp]
    pp_names  = ["None (no prepayment)"]    + [s["name"] for s in pp_scs]

    # Ensure rc_scenarios populated for edit dialog
    if not st.session_state.get("sc_loaded_from_db"):
        import uuid
        rcs = {}
        for i, sc_row in enumerate(db_sc_cmp, 1):
            sc_row["_seq"] = i; sc_row["_key"] = str(uuid.uuid4())[:8]
            rcs[sc_row["_key"]] = sc_row
        st.session_state.rc_scenarios  = rcs
        st.session_state.sc_loaded_from_db = True

    n_sc = st.radio("Scenarios to compare:", [2,3,4], horizontal=True, key="cmp_n",
                     help="Number of side-by-side columns")

    sc_defs    = []
    cols       = st.columns(int(n_sc))
    pal        = ["#1a3c5e","#e74c3c","#27ae60","#f39c12"]
    today_p    = b["today_m"].get("period_today",0)
    today_bal  = b["today_m"].get("balance_today",b["principal"])

    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**Column {i+1}**")
            default_idx = 0 if i == 0 else min(i, len(sc_names)-1)
            sc_pick = st.selectbox("Scenario", sc_names, index=default_idx, key=f"cmp_src_{i}",
                                   help="Select a saved rate scenario or current mortgage as base")
            pp_pick = st.selectbox("Prepayment overlay", pp_names, index=0, key=f"cmp_pp_{i}",
                                   help="Optionally layer a prepayment plan on top of this scenario")

            is_base     = sc_pick == "Current Mortgage (base)"
            sc_db_match = next((s for s in db_sc_cmp if s["name"]==sc_pick), None) if not is_base else None
            sel_pp      = next((s for s in pp_scs if s["name"]==pp_pick), None)

            if is_base:
                lbl      = "Current Mortgage"
                def_rate = float(b["annual_rate"])
                def_amort= int(b["amort_years"])
                def_freq = b["payment_freq"]
                sc_dict  = {"renewals":[],"name":lbl,"user_pmt":0,"pp":{},"linked_pp_db_id":0}
                sc_db_key= None
            else:
                lbl        = sc_db_match["name"] if sc_db_match else sc_pick
                renewals   = sc_db_match["renewals"] if sc_db_match else []
                def_rate   = float(renewals[-1]["new_rate"]) if renewals else float(b["annual_rate"])
                def_amort  = int(b["amort_years"])
                def_freq   = b["payment_freq"]
                sc_dict    = sc_db_match if sc_db_match else {"renewals":[],"name":lbl,"user_pmt":0,"pp":{},"linked_pp_db_id":0}
                sc_db_key  = next((k for k,v in st.session_state.get("rc_scenarios",{}).items()
                                   if v.get("db_id")==sc_db_match.get("db_id")), None) if sc_db_match else None

            # REQ #8 (comparison read-only display)
            st.markdown(f"**{lbl}**")
            st.caption(f"Rate: **{def_rate:.2f}%** · Amort: **{def_amort} yrs** · Freq: **{def_freq}**")
            if sc_db_match and sc_db_match.get("desc"): st.caption(sc_db_match["desc"])
            if sel_pp:
                s = sel_pp.get("settings",{})
                pp_parts = []
                if s.get("annual_lump"): pp_parts.append(f"${s['annual_lump']:,.0f}/yr")
                if s.get("pay_increase_type","None")!="None": pp_parts.append(f"+{s.get('pay_increase_val',0)}% pmt")
                if s.get("onetime_amount"): pp_parts.append(f"one-time ${s['onetime_amount']:,.0f}")
                st.caption(f"💰 PP: {', '.join(pp_parts) or 'n/a'}")

            if not is_base and sc_db_key and sc_db_key in st.session_state.get("rc_scenarios",{}):
                if st.button("✏️ Edit Scenario", key=f"cmp_edit_{i}", use_container_width=True,
                              help="Open the full editor popup for this scenario"):
                    st.session_state["_editing_sc_id"] = sc_db_key

            fc_  = FREQ.get(def_freq, FREQ["Monthly"])
            ny   = fc_["n"]
            ac   = fc_["accel"]
            df_c, s_c, all_rcs, sc_extra, last_rate, sc_term_end_p = compute_scenario(sc_dict, b, sel_pp)
            pmt_c     = calc_pmt(b["principal"], def_rate, ny, def_amort, ac)
            tp_c      = b["today_m"].get("period_today",0)
            rem_c     = round((len(df_c)-tp_c)/ny,1) if tp_c>0 and not df_c.empty else def_amort
            pmt_today = calc_pmt(today_bal, def_rate, ny, rem_c, ac) if rem_c>0 else pmt_c
            sc_int    = s_c.get("total_interest",0)
            sc_total  = sc_int + b["principal"]
            sc_total_paid = s_c.get("total_paid", sc_total)

            sc_defs.append({
                "label":lbl, "rate":def_rate, "amort":def_amort, "freq":def_freq,
                "df":df_c, "summary":s_c, "payment":pmt_c, "pmt_today":pmt_today,
                "rem":rem_c, "n_py":ny, "sc_int":sc_int, "sc_total":sc_total,
                "sc_total_paid":sc_total_paid, "sc_term_end_p":sc_term_end_p,
                "color": pal[i],
            })

    # ── Comparison table ──────────────────────────────────────────
    st.divider()
    comp_rows = []
    for sc in sc_defs:
        comp_rows.append({
            "Scenario":      sc["label"],
            "Rate":          f"{sc['rate']:.2f}%",
            "Amort":         f"{sc['amort']} yrs",
            "Orig Payment":  f"${sc['payment']:,.2f}",
            "Curr Payment":  f"${sc['pmt_today']:,.2f}",
            "Remaining":     f"{sc['rem']:.1f} yrs",
            "Total Interest":f"${sc['sc_int']:,.0f}",
            "Int % of P+I":  _pct(sc['sc_int'], sc['sc_total']),
            "Total P+I":     f"${sc['sc_total']:,.0f}",
        })
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    # ── Balance comparison line chart ─────────────────────────────
    fig_c = go.Figure()
    for sc in sc_defs:
        if not sc["df"].empty:
            fig_c.add_scatter(x=sc["df"]["Date"], y=sc["df"]["Balance"],
                               name=sc["label"], line=dict(color=sc["color"]))
    full_df = b.get("full_df")
    if full_df is not None and b["today_m"].get("period_today"):
        tp = b["today_m"]["period_today"]
        if not full_df.empty and tp <= len(full_df):
            fig_c.add_vline(x=_vline_x(full_df["Date"].iloc[tp-1]),
                             line_dash="dash", line_color="#27ae60",
                             annotation_text="Today", annotation_position="top right")
    fig_c.update_layout(title="Balance Comparison", xaxis_title="Date", yaxis_title="($)",
                         height=320, margin=dict(t=55,b=40),
                         legend=dict(orientation="h",yanchor="top",y=-0.15))
    st.plotly_chart(fig_c, use_container_width=True, key="ch_cmpbal")

    # ── REQ #7: Per-scenario P&I charts side by side ──────────────
    st.markdown("#### P & I by Scenario (yearly)")
    chart_cols = st.columns(len(sc_defs))
    for i, (sc, cc) in enumerate(zip(sc_defs, chart_cols)):
        with cc:
            if not sc["df"].empty:
                fig_pi = stacked_bar_pi(sc["df"], today_p, sc["sc_term_end_p"],
                                         f"{sc['label']}")
                # Override legend to be minimal (it's already labelled by title)
                fig_pi.update_layout(height=320, margin=dict(t=45,b=70,l=30,r=10),
                                      showlegend=False,
                                      title=dict(text=sc["label"], font=dict(size=12)))
                st.plotly_chart(fig_pi, use_container_width=True, key=f"ch_pi_cmp_{i}")
            else:
                st.caption(f"No data for {sc['label']}")

    # ── Best scenario ─────────────────────────────────────────────
    best      = min(range(len(sc_defs)), key=lambda i: sc_defs[i]["sc_int"])
    worst_int = max(sc["sc_int"] for sc in sc_defs)
    st.markdown(
        f'<div class="ok">🏆 <b>{sc_defs[best]["label"]}</b> saves '
        f'${worst_int - sc_defs[best]["sc_int"]:,.0f} in interest '
        f'· Remaining: <b>{sc_defs[best]["rem"]:.1f} yrs</b></div>',
        unsafe_allow_html=True,
    )

    editing_id = st.session_state.get("_editing_sc_id")
    if editing_id and editing_id in st.session_state.get("rc_scenarios",{}) and not st.session_state.get("_dialog_shown"):
        st.session_state["_dialog_shown"] = True
        edit_scenario_dialog()
