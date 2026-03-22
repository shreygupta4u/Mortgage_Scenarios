"""pages/tab_scenarios.py — Rate Change Scenarios tab."""
import streamlit as st
from datetime import date
import uuid

from modules import (
    calc_pmt, build_amortization,
    db_load_scenarios, db_delete_scenario,
    db_load_prepay_scenarios,
    stacked_bar_pi,
)
from pages.scenario_editor import (
    compute_scenario, compute_adj_scenario, edit_scenario_dialog,
)


def _pct(part, total):
    return f"{part/total*100:.1f}%" if total else "—"


def render_tab_scenarios(conn, b):
    st.subheader("📈 Rate Change Scenarios")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    # ── FIX #2: value check (not key-existence check) ─────────────
    if not st.session_state.get("sc_loaded_from_db"):
        db_rows = db_load_scenarios(conn)
        rcs = {}
        for i, sc_row in enumerate(db_rows, 1):
            for rn in sc_row.get("renewals", []):
                rn.setdefault("onetime_amount", 0)
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            rcs[sc_row["_key"]] = sc_row
        st.session_state.rc_scenarios  = rcs
        st.session_state.sc_loaded_from_db = True

    if not st.session_state.get("pp_sc_loaded"):
        pp_rows = db_load_prepay_scenarios(conn)
        pp_dict = {}
        for i, sc_row in enumerate(pp_rows, 1):
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            pp_dict[sc_row["_key"]] = sc_row
        st.session_state.pp_scenarios  = pp_dict
        st.session_state.pp_sc_loaded  = True

    rcs: dict = st.session_state.rc_scenarios
    pps: dict = st.session_state.pp_scenarios

    # ── Shared base amortization ──────────────────────────────────
    _, s_base = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None,
    )
    base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
    today_p            = b["today_m"].get("period_today", 0)
    today_bal          = b["today_m"].get("balance_today", b["principal"])
    base_total_int     = s_base.get("total_interest", 0)
    base_total_princ   = b["principal"]
    base_grand_total   = base_total_int + base_total_princ

    # ── REQ #4: Show base metrics ONCE ───────────────────────────
    st.markdown("#### 📊 Base Mortgage Metrics")
    bm1, bm2, bm3, bm4 = st.columns(4)
    bm1.markdown(
        f'<div class="mc"><h3>Current Remaining Amort.</h3>'
        f'<p>{base_remaining_yrs:.1f} yrs</p></div>',
        unsafe_allow_html=True,
    )
    bm2.markdown(
        f'<div class="mc mc-r"><h3>Total Interest (base)</h3>'
        f'<p>${base_total_int:,.0f} '
        f'<span style="font-size:.8rem">({_pct(base_total_int, base_grand_total)} of total)</span>'
        f'</p></div>',
        unsafe_allow_html=True,
    )
    bm3.markdown(
        f'<div class="mc mc-b"><h3>Total Principal</h3>'
        f'<p>${base_total_princ:,.0f} '
        f'<span style="font-size:.8rem">({_pct(base_total_princ, base_grand_total)} of total)</span>'
        f'</p></div>',
        unsafe_allow_html=True,
    )
    bm4.markdown(
        f'<div class="mc"><h3>Balance Today</h3>'
        f'<p>${today_bal:,.0f}</p></div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ── REQ #9: Global prepayment scenario selector ───────────────
    pp_list  = sorted(pps.values(), key=lambda s: s.get("_seq", 999))
    pp_names = ["None (no prepayment)"] + [s["name"] for s in pp_list]
    sel_pp_name = st.selectbox(
        "🔗 Apply Prepayment Scenario to all rate scenarios:",
        pp_names, key="sc_global_pp",
        help="Combine a saved prepayment plan with every rate scenario below",
    )
    sel_pp_sc = next((s for s in pp_list if s["name"] == sel_pp_name), None)

    # ── Header ────────────────────────────────────────────────────
    hdr1, hdr2 = st.columns([5, 2])
    hdr1.caption("Click **✏️ Edit** on any scenario card to open the full editor.")
    if hdr2.button("➕ New Scenario", key="btn_new_rc", use_container_width=True,
                    help="Create a new rate-change scenario"):
        nk       = str(uuid.uuid4())[:8]
        next_seq = max((s.get("_seq", 0) for s in rcs.values()), default=0) + 1
        rcs[nk]  = {"_key": nk, "_seq": next_seq, "db_id": None,
                    "name": f"Scenario {next_seq}", "desc": "",
                    "renewals": [], "user_pmt": 0, "pp": {}}
        st.session_state["_editing_sc_id"] = nk
        st.rerun()

    sc_del     = []
    sorted_scs = sorted(rcs.items(), key=lambda kv: kv[1].get("_seq", 999))

    for sc_id, sc in sorted_scs:
        renewals   = sc.get("renewals", [])
        seq        = sc.get("_seq", "?")

        # ── Compute results for this scenario ─────────────────────
        df_sc, s_sc, s_adj, adj_rem, adj_end, delta_yrs, colour, calc_monthly_sc, sc_term_end_p, last_rate = (
            compute_adj_scenario(sc, b, sel_pp_sc, None, s_base)
        )
        eff_pmt       = float(sc.get("user_pmt", 0)) or calc_monthly_sc
        sc_total_int  = s_adj.get("total_interest", 0)
        sc_total_pmt  = s_adj.get("total_paid", base_grand_total)
        sc_total_princ = b["principal"]
        sc_grand_total = sc_total_int + sc_total_princ

        int_delta_str = (
            f"${sc_total_int - base_total_int:+,.0f} vs base"
            if abs(sc_total_int - base_total_int) > 1 else "same as base"
        )
        rem_delta_str = (
            f"{adj_rem - base_remaining_yrs:+.1f} yrs" if abs(delta_yrs) >= 0.05 else "same"
        )

        # Rates timeline string
        rates_str = " → ".join(f"{rn['new_rate']:.2f}%" for rn in renewals) or f"{b['annual_rate']:.2f}% (base)"
        lump_total = sum(float(rn.get("onetime_amount", 0)) for rn in renewals)
        pp_label   = f"  ·  PP: {sel_pp_sc['name']}" if sel_pp_sc else ""
        badge      = "🟢" if sc.get("db_id") else "🔴"

        # ── REQ #5: Concise card header + detail inside expander ──
        with st.expander(
            f"{badge} **#{seq} — {sc['name']}**  ·  "
            f"{'✓ Saved' if sc.get('db_id') else '⚠ Unsaved'}{pp_label}",
            expanded=False,
        ):
            # Scenario detail summary inside expander
            ci1, ci2 = st.columns([3, 1])
            ci1.markdown(
                f"**Rates:** {rates_str}  \n"
                f"**Renewals:** {len(renewals)}  ·  "
                f"**One-time lumps:** ${lump_total:,.0f}  \n"
                f"**Payment:** ${eff_pmt:,.2f}/period "
                f"(required: ${calc_monthly_sc:,.2f})"
            )
            if sc.get("desc"):
                ci1.caption(sc["desc"])
            btn1, btn2 = ci2.columns(2)
            if btn1.button("✏️ Edit", key=f"edit_{sc_id}", use_container_width=True,
                            help="Open the full editor popup"):
                st.session_state["_editing_sc_id"] = sc_id
            if btn2.button("🗑️ Del", key=f"del_{sc_id}", use_container_width=True,
                            help="Delete this scenario from DB and page"):
                if sc.get("db_id"):
                    db_delete_scenario(conn, sc["db_id"])
                sc_del.append(sc_id)

        # ── REQ #6: Metric tiles with % ───────────────────────────
        t1, t2, t3, t4, t5, t6 = st.columns(6)
        t1.markdown(
            f'<div class="mc {colour}"><h3>Adj. Interest</h3>'
            f'<p>${sc_total_int:,.0f}</p>'
            f'<small>{_pct(sc_total_int, sc_grand_total)} of P+I · {int_delta_str}</small></div>',
            unsafe_allow_html=True,
        )
        t2.markdown(
            f'<div class="mc mc-b"><h3>Adj. Principal</h3>'
            f'<p>${sc_total_princ:,.0f}</p>'
            f'<small>{_pct(sc_total_princ, sc_grand_total)} of P+I</small></div>',
            unsafe_allow_html=True,
        )
        t3.markdown(
            f'<div class="mc {colour}"><h3>Adj. Remaining</h3>'
            f'<p>{adj_rem:.1f} yrs</p>'
            f'<small>{rem_delta_str}</small></div>',
            unsafe_allow_html=True,
        )
        t4.markdown(
            f'<div class="mc {colour}"><h3>Mortgage-free</h3>'
            f'<p>{adj_end.strftime("%b %Y")}</p>'
            f'<small>at ${eff_pmt:,.2f}/period</small></div>',
            unsafe_allow_html=True,
        )
        t5.markdown(
            f'<div class="mc"><h3>Last Rate</h3>'
            f'<p>{last_rate:.2f}%</p>'
            f'<small>{len(renewals)} renewal(s)</small></div>',
            unsafe_allow_html=True,
        )
        t6.markdown(
            f'<div class="mc"><h3>Total Paid</h3>'
            f'<p>${sc_grand_total:,.0f}</p>'
            f'<small>{_pct(sc_total_int, sc_grand_total)} interest</small></div>',
            unsafe_allow_html=True,
        )

        # ── Charts ────────────────────────────────────────────────
        if not df_sc.empty:
            import plotly.graph_objects as go
            ch1, ch2 = st.columns([3, 2])
            with ch1:
                fig_bar = stacked_bar_pi(df_sc, today_p, sc_term_end_p,
                                          f"#{seq} {sc['name']} — P & I")
                st.plotly_chart(fig_bar, use_container_width=True, key=f"ch_sc_{sc_id}")
            with ch2:
                fig_r = go.Figure()
                fig_r.add_scatter(x=df_sc["Date"], y=df_sc["Rate (%)"],
                                   fill="tozeroy", line=dict(color="#27ae60"))
                fig_r.update_layout(title=f"#{seq} Rate over time",
                                     xaxis_title="Date", yaxis_title="%",
                                     height=300, margin=dict(t=35, b=20))
                st.plotly_chart(fig_r, use_container_width=True, key=f"ch_rt_{sc_id}")

        st.divider()

    for k in sc_del:
        del rcs[k]
    if sc_del:
        st.rerun()

    # ── Open edit dialog if triggered ─────────────────────────────
    editing_id = st.session_state.get("_editing_sc_id")
    if editing_id and editing_id in rcs:
        edit_scenario_dialog()

    with st.expander("📚 Canadian Mortgage Education"):
        st.markdown(
            "**Semi-annual compounding**: `(1 + r/200)²`  \n"
            "**CMHC**: <10% = 4.00% · 10–15% = 3.10% · 15–20% = 2.80% · ≥20% = nil  \n"
            "**Break penalty**: Variable = 3 months interest · Fixed = max(3mo, IRD)"
        )
