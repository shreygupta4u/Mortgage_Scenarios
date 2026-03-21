"""
tabs/tab_prepayment.py — Prepayment Analysis tab
"""
import streamlit as st
from dateutil.relativedelta import relativedelta

from mortgage_math import calc_pmt, date_to_period, build_amortization
from mortgage_charts import stacked_bar_pi
from mortgage_db import db_load_scenarios, db_save_scenario


def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        st.stop()


def render(tabs_list):
    with tabs_list[3]:
        st.subheader("💰 Prepayment Analysis")
        require_setup()
        b = st.session_state["base"]
        fn = b["n_py"]

        db_sc_pp = db_load_scenarios(st.session_state.db_conn)
        chosen_rc = st.selectbox(
            "Rate scenario base",
            ["Base Rate — no rate changes"] + [s["name"] for s in db_sc_pp],
            key="pp_rc_sel",
            help="Select a saved rate scenario to combine with prepayments"
        )
        if chosen_rc == "Base Rate — no rate changes":
            active_rc = []
        else:
            saved_p = next((s["params"] for s in db_sc_pp if s["name"] == chosen_rc), {})
            active_rc = saved_p.get("rate_changes", [])
        all_rcs_pp = (b.get("past_renewal_rcs") or []) + active_rc

        col_pp1, col_pp2 = st.columns(2)
        future_extra = []

        with col_pp1:
            st.markdown("##### 📅 Annual Lump-Sum Prepayments")
            annual_lump = st.number_input(
                "Annual lump-sum ($)", 0, 500_000, 10_000, 500,
                key="pp_al",
                help="Additional principal paid each year on top of regular payments"
            )
            lump_month = st.selectbox(
                "Month each year",
                ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                key="pp_lm",
                help="Which month of the year to apply the lump-sum"
            )
            lump_start = st.number_input(
                "Starting year", 1, 30, 1, key="pp_ls",
                help="Year number (from mortgage start) to begin lump-sum payments"
            )
            lump_nyrs = st.number_input(
                "For how many years?", 1, 30, 5, key="pp_ln",
                help="Number of years to continue making the annual lump-sum"
            )
            lmm = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                   "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
            if annual_lump > 0:
                for yr in range(int(lump_start), int(lump_start + lump_nyrs)):
                    p = max(1, int((yr - 1) * fn + lmm[lump_month] * fn / 12))
                    future_extra.append({"period": p, "amount": float(annual_lump)})
            pp_lim = st.slider(
                "Lender prepayment limit (%)", 10, 30, 20, key="pp_lim",
                help="Annual lump-sum cap as % of original principal (typically 10–20%)"
            )
            if annual_lump > b["principal"] * pp_lim / 100:
                st.warning(f"⚠️ Exceeds {pp_lim}% limit (${b['principal']*pp_lim/100:,.0f}).")

        with col_pp2:
            st.markdown("##### 💳 Increased Regular Payments")
            inc_t = st.radio(
                "Increase type", ["Fixed $", "% increase", "None"],
                index=2, horizontal=True, key="pp_it",
                help="Boost each regular payment by a fixed dollar amount or percentage"
            )
            inc_v = 0.0
            if inc_t == "Fixed $":
                inc_v = float(st.number_input(
                    "Extra/payment ($)", 0, 10_000, 200, 50, key="pp_if",
                    help="Extra principal added to every scheduled payment"
                ))
            elif inc_t == "% increase":
                inc_pct = st.slider("% increase", 1, 100, 10, key="pp_ip")
                inc_v = calc_pmt(
                    b["principal"], b["annual_rate"], fn,
                    b["amort_years"], b["accel"]
                ) * inc_pct / 100
            if inc_v > 0:
                for p in range(1, int(b["amort_years"] * fn) + 1):
                    future_extra.append({"period": p, "amount": inc_v})

            st.markdown("##### 🔁 One-Time Lump Sum")
            ot_mode = st.radio(
                "Mode", ["By Date", "By Period"], horizontal=True, key="pp_om"
            )
            if ot_mode == "By Date":
                ot_d = st.date_input(
                    "Date",
                    b["start_date"] + relativedelta(years=1),
                    min_value=b["start_date"],
                    key="pp_od"
                )
                ot_p = date_to_period(ot_d, b["start_date"], fn)
                st.caption(f"≈ Period {ot_p}")
            else:
                ot_p = int(st.number_input(
                    "Period #", 1, int(b["amort_years"] * fn), fn, key="pp_op"
                ))
            ot_a = st.number_input(
                "Amount ($)", 0, 2_000_000, 0, 1_000, key="pp_oa",
                help="One-time additional principal payment"
            )
            if ot_a > 0:
                future_extra.append({"period": int(ot_p), "amount": float(ot_a)})

        past_extra = b.get("past_extra", [])
        all_extra = past_extra + future_extra

        df_rsc, s_rsc = build_amortization(
            b["principal"], b["annual_rate"], fn, b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=past_extra or None,
            rate_changes=all_rcs_pp or None,
        )
        df_pp, s_pp = build_amortization(
            b["principal"], b["annual_rate"], fn, b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=all_extra or None,
            rate_changes=all_rcs_pp or None,
        )

        tm_pp = b["today_m"]
        today_p_pp = tm_pp.get("period_today", 0)
        rem_rsc = (
            round((len(df_rsc) - today_p_pp) / fn, 1)
            if today_p_pp > 0 and not df_rsc.empty
            else b["amort_years"]
        )
        rem_pp_v = (
            round((len(df_pp) - today_p_pp) / fn, 1)
            if today_p_pp > 0 and not df_pp.empty
            else b["amort_years"]
        )
        int_saved = s_rsc.get("total_interest", 0) - s_pp.get("total_interest", 0)
        new_total = sum(e["amount"] for e in future_extra)

        st.divider()
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Interest (rate sc.)", f"${s_rsc.get('total_interest', 0):,.0f}",
                  help="Total interest under selected rate scenario (no prepayments)")
        m2.metric("Interest (+ prepayments)", f"${s_pp.get('total_interest', 0):,.0f}",
                  delta=f"${-int_saved:+,.0f}",
                  help="Total interest with your prepayment strategy")
        m3.metric("Remaining (rate sc.)", f"{rem_rsc:.1f} yrs",
                  help="Remaining amortization from today under rate scenario")
        m4.metric("Remaining (+ prepayments)", f"{rem_pp_v:.1f} yrs",
                  delta=f"{rem_pp_v-rem_rsc:+.1f} yrs",
                  help="Remaining with prepayments applied")
        m5.metric("Total New Prepaid", f"${new_total:,.0f}",
                  help="Total additional prepayments planned")
        m6.metric(
            "Interest ROI",
            f"{int_saved/new_total*100:.1f}%" if new_total > 0 and int_saved > 0 else "—",
            help="Interest saved per $100 prepaid"
        )
        if int_saved > 0:
            st.markdown(
                f'<div class="ok">💚 Prepayments save <b>${int_saved:,.0f}</b> · '
                f'Shorten by <b>{rem_rsc-rem_pp_v:.1f} yrs</b></div>',
                unsafe_allow_html=True
            )

        sc_end_p = int(b["term_years"] * fn) + int(3 * fn)
        if not df_pp.empty:
            fig_pp_bar = stacked_bar_pi(
                df_pp, today_p_pp, sc_end_p,
                f"Prepayment Impact — P & I ({chosen_rc})"
            )
            st.plotly_chart(fig_pp_bar, use_container_width=True, key="ch_pp_bar")

        sc_np = st.text_input(
            "Save as", "Prepayment Scenario", key="pp_scname",
            help="Name to save this prepayment scenario to the database"
        )
        if st.button("💾 Save", key="btn_save_pp"):
            sc_p = {
                "rate_changes": active_rc,
                "extra_payments": len(all_extra),
                "sc_type": "prepayment",
            }
            ok = db_save_scenario(st.session_state.db_conn, sc_np, sc_p, s_pp)
            if ok:
                st.success("✅ Saved to DB")
            else:
                st.error("❌ Save failed")
