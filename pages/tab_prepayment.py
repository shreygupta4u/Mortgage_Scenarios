"""pages/tab_prepayment.py — Prepayment Scenarios tab (REQ #7: full save/load like rate scenarios)."""
import streamlit as st
from datetime import date
import uuid

from modules import (
    calc_pmt, build_amortization,
    db_load_scenarios, db_load_prepay_scenarios, db_delete_prepay_scenario,
    stacked_bar_pi,
)
from pages.scenario_editor import apply_prepay_settings, edit_prepay_dialog


def _pct(part, total):
    return f"{part/total*100:.1f}%" if total else "—"


def _default_settings():
    return {"annual_lump": 0, "lump_month": 1, "lump_start_year": 1, "lump_num_years": 0,
            "pay_increase_type": "None", "pay_increase_val": 0.0,
            "onetime_period": 0, "onetime_amount": 0}


def render_tab_prepayment(conn, b):
    st.subheader("💰 Prepayment Scenarios")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    # ── Load prepayment scenarios from DB (once) ──────────────────
    if not st.session_state.get("pp_sc_loaded"):
        pp_rows = db_load_prepay_scenarios(conn)
        pp_dict = {}
        for i, sc_row in enumerate(pp_rows, 1):
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            pp_dict[sc_row["_key"]] = sc_row
        st.session_state.pp_scenarios  = pp_dict
        st.session_state.pp_sc_loaded  = True

    pps: dict = st.session_state.pp_scenarios

    # ── Base amortization (no extra payments) ─────────────────────
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

    # ── Rate scenario selector (base for prepayment analysis) ──────
    rate_scs  = db_load_scenarios(conn)
    rc_opts   = ["Current Setup (base rates)"] + [s["name"] for s in rate_scs]
    chosen_rc = st.selectbox(
        "Apply prepayments on top of:",
        rc_opts, key="pp_rc_base",
        help="Pick a rate scenario as the interest rate baseline, then add prepayments on top"
    )
    if chosen_rc == "Current Setup (base rates)":
        active_rcs = b.get("past_renewal_rcs") or []
    else:
        sc_match  = next((s for s in rate_scs if s["name"] == chosen_rc), None)
        active_rcs = (b.get("past_renewal_rcs") or []) + [
            {"period": rn["period"], "new_rate": rn["new_rate"]}
            for rn in (sc_match["renewals"] if sc_match else [])
        ]

    st.divider()
    # ── REQ #4-style: show base metrics for this rate base ─────────
    bm1, bm2, bm3 = st.columns(3)
    bm1.markdown(
        f'<div class="mc"><h3>Remaining (base, no prepayment)</h3>'
        f'<p>{base_remaining_yrs:.1f} yrs</p></div>', unsafe_allow_html=True)
    bm2.markdown(
        f'<div class="mc mc-r"><h3>Total Interest (base)</h3>'
        f'<p>${base_total_int:,.0f} '
        f'<span style="font-size:.8rem">({_pct(base_total_int, base_grand_total)} of P+I)</span>'
        f'</p></div>', unsafe_allow_html=True)
    bm3.markdown(
        f'<div class="mc mc-b"><h3>Total Principal</h3>'
        f'<p>${base_total_princ:,.0f} '
        f'<span style="font-size:.8rem">({_pct(base_total_princ, base_grand_total)} of P+I)</span>'
        f'</p></div>', unsafe_allow_html=True)

    st.divider()
    # ── Prepayment scenario list ───────────────────────────────────
    hdr1, hdr2 = st.columns([5, 2])
    hdr1.caption("Save prepayment strategies and compare their impact on the mortgage above.")
    if hdr2.button("➕ New Prepayment Scenario", key="btn_new_pp", use_container_width=True,
                    help="Create a new prepayment scenario"):
        nk       = str(uuid.uuid4())[:8]
        next_seq = max((s.get("_seq", 0) for s in pps.values()), default=0) + 1
        pps[nk]  = {"_key": nk, "_seq": next_seq, "db_id": None,
                    "name": f"Prepayment {next_seq}", "desc": "", "settings": _default_settings()}
        st.session_state["_editing_pp_sc_id"] = nk
        st.rerun()

    sc_del     = []
    sorted_pps = sorted(pps.values(), key=lambda s: s.get("_seq", 999))

    for sc in sorted_pps:
        sc_id    = sc["_key"]
        settings = sc.get("settings", _default_settings())
        seq      = sc.get("_seq", "?")
        badge    = "🟢" if sc.get("db_id") else "🔴"

        # Summary string
        parts = []
        if settings.get("annual_lump", 0) > 0:
            parts.append(f"${settings['annual_lump']:,.0f}/yr × {settings.get('lump_num_years',0)} yrs")
        inc_t = settings.get("pay_increase_type", "None")
        if inc_t not in ("None", ""):
            inc_v = settings.get("pay_increase_val", 0)
            parts.append(f"+{'${:.0f}'.format(inc_v) if inc_t=='Fixed' else f'{inc_v}%'}/pmt")
        if settings.get("onetime_amount", 0) > 0:
            parts.append(f"one-time ${settings['onetime_amount']:,.0f} @p{settings.get('onetime_period',0)}")
        summary_str = "  ·  ".join(parts) if parts else "No prepayments configured"

        # Compute this scenario's results
        sc_extras = apply_prepay_settings(settings, b, b.get("past_extra") or [])
        _, s_sc = build_amortization(
            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=sc_extras or None,
            rate_changes=active_rcs or None,
        )
        sc_int         = s_sc.get("total_interest", 0)
        sc_grand       = sc_int + b["principal"]
        int_saved      = base_total_int - sc_int
        sc_payoff_p    = s_sc.get("payoff_periods", len(sc_extras) or int(b["amort_years"] * b["n_py"]))
        sc_rem_yrs     = round((sc_payoff_p - today_p) / b["n_py"], 1) if today_p > 0 else base_remaining_yrs
        new_total_pp   = sum(e["amount"] for e in sc_extras if e not in (b.get("past_extra") or []))

        with st.expander(
            f"{badge} **#{seq} — {sc['name']}**  ·  {summary_str}",
            expanded=False,
        ):
            ci1, ci2 = st.columns([3, 1])
            ci1.markdown(
                f"**Strategy:** {summary_str}  \n"
                f"**Interest saved:** ${int_saved:,.0f}  ·  "
                f"**Yrs saved:** {base_remaining_yrs - sc_rem_yrs:.1f}"
            )
            if sc.get("desc"):
                ci1.caption(sc["desc"])
            btn1, btn2 = ci2.columns(2)
            if btn1.button("✏️ Edit", key=f"pp_edit_{sc_id}", use_container_width=True):
                st.session_state["_editing_pp_sc_id"] = sc_id
            if btn2.button("🗑️ Del", key=f"pp_del_{sc_id}", use_container_width=True):
                if sc.get("db_id"):
                    db_delete_prepay_scenario(conn, sc["db_id"])
                sc_del.append(sc_id)

        # REQ #6: Metric tiles with %
        t1, t2, t3, t4, t5, t6 = st.columns(6)
        colour = "mc-g" if sc_int <= base_total_int else "mc-r"
        t1.markdown(
            f'<div class="mc {colour}"><h3>Interest (+prepay)</h3>'
            f'<p>${sc_int:,.0f}</p>'
            f'<small>{_pct(sc_int, sc_grand)} of P+I</small></div>', unsafe_allow_html=True)
        t2.markdown(
            f'<div class="mc mc-b"><h3>Principal</h3>'
            f'<p>${b["principal"]:,.0f}</p>'
            f'<small>{_pct(b["principal"], sc_grand)} of P+I</small></div>', unsafe_allow_html=True)
        t3.markdown(
            f'<div class="mc {colour}"><h3>Interest Saved</h3>'
            f'<p>${int_saved:,.0f}</p>'
            f'<small>vs base</small></div>', unsafe_allow_html=True)
        t4.markdown(
            f'<div class="mc {colour}"><h3>Remaining (adj)</h3>'
            f'<p>{sc_rem_yrs:.1f} yrs</p>'
            f'<small>{base_remaining_yrs - sc_rem_yrs:.1f} yrs saved</small></div>', unsafe_allow_html=True)
        t5.markdown(
            f'<div class="mc"><h3>Total Prepaid</h3>'
            f'<p>${new_total_pp:,.0f}</p>'
            f'<small>new payments</small></div>', unsafe_allow_html=True)
        roi_str = (f"{int_saved/new_total_pp*100:.1f}%" if new_total_pp > 0 and int_saved > 0 else "—")
        t6.markdown(
            f'<div class="mc"><h3>Interest ROI</h3>'
            f'<p>{roi_str}</p>'
            f'<small>interest saved per $ prepaid</small></div>', unsafe_allow_html=True)

        # Charts
        df_sc, _ = build_amortization(
            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=sc_extras or None, rate_changes=active_rcs or None,
        )
        sc_term_end_p = int(b["term_years"] * b["n_py"]) + int(3 * b["n_py"])
        if not df_sc.empty:
            fig = stacked_bar_pi(df_sc, today_p, sc_term_end_p, f"#{seq} {sc['name']} — P & I")
            st.plotly_chart(fig, use_container_width=True, key=f"ch_pp_{sc_id}")

        st.divider()

    for k in sc_del:
        del pps[k]
    if sc_del:
        st.rerun()

    # ── Open edit dialog ──────────────────────────────────────────
    editing_id = st.session_state.get("_editing_pp_sc_id")
    if editing_id and editing_id in pps:
        edit_prepay_dialog()
