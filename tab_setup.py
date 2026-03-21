"""
tabs/tab_setup.py — Setup & Overview tab for Canadian Mortgage Analyzer
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
from dateutil.relativedelta import relativedelta
import uuid

from mortgage_math import (
    FREQ, calc_pmt, cmhc_premium, date_to_period,
    build_amortization, get_today_metrics, periodic_rate,
)
from mortgage_charts import stacked_bar_pi
from mortgage_db import db_save_setup
from mortgage_wireframe import generate_wireframe_docx


def render(tabs_list):
    with tabs_list[0]:
        st.subheader("Mortgage Setup")
        sd = st.session_state.setup_data or {}

        def sv(key, default):
            return sd.get("widget_state", {}).get(key, default)

        # ── SECTION A: Purchase & Down Payment ─────────────────────────────────
        st.markdown("#### 🏡 A · Purchase & Down Payment")
        a1, a2 = st.columns(2)
        purchase_price = a1.number_input(
            "Purchase Price ($)", 100_000, 5_000_000,
            int(sv("s_price", 1_030_000)), 5_000, format="%d",
            key="s_price", help="Total property purchase price"
        )
        down_pct = a2.slider(
            "Down Payment (%)", 5.0, 50.0,
            float(sv("s_dpct", 20.0)), 0.5,
            key="s_dpct", help="Down payment as % of purchase price"
        )
        down_pay = purchase_price * down_pct / 100
        a1.metric("Down Payment", f"${down_pay:,.0f}")

        cmhc, hst = cmhc_premium(purchase_price, down_pay)
        if cmhc is None:
            st.markdown(
                '<div class="warn">⚠️ CMHC not available (price >$1.5M or down <5%)</div>',
                unsafe_allow_html=True
            )
            insured_p = purchase_price - down_pay
        elif cmhc == 0:
            st.markdown(
                '<div class="ok">✅ No CMHC premium — down ≥ 20%</div>',
                unsafe_allow_html=True
            )
            insured_p = purchase_price - down_pay
        else:
            add_c = a2.checkbox(
                "Add CMHC to mortgage?", bool(sv("s_addcmhc", True)), key="s_addcmhc"
            )
            st.markdown(
                f'<div class="inf">🛡️ CMHC: <b>${cmhc:,.0f}</b> (+HST ~${hst:,.0f}) · '
                f'{cmhc/(purchase_price-down_pay)*100:.2f}%</div>',
                unsafe_allow_html=True
            )
            insured_p = (purchase_price - down_pay) + (cmhc if add_c else 0)

        st.divider()

        # ── SECTION B: Initial Mortgage Terms ──────────────────────────────────
        st.markdown("#### 💵 B · Initial Mortgage Terms")
        b1, b2 = st.columns(2)
        mortgage_type = b1.selectbox(
            "Mortgage Type", ["Fixed", "Variable"],
            index=["Fixed", "Variable"].index(sv("s_mtype", "Fixed")),
            key="s_mtype",
            help="Fixed = rate locked for term; Variable = moves with prime"
        )
        payment_freq = b2.selectbox(
            "Payment Frequency", list(FREQ.keys()),
            index=list(FREQ.keys()).index(sv("s_freq", "Monthly")),
            key="s_freq",
            help="How often you make mortgage payments"
        )
        annual_rate = b1.number_input(
            "Interest Rate (%)", 0.5, 20.0,
            float(sv("s_rate", 5.39)), 0.01, format="%.2f",
            key="s_rate",
            help="Your contracted annual interest rate (semi-annual compounding)"
        )
        amort_years = b2.slider(
            "Amortization (years)", 5, 30,
            int(sv("s_amort", 30)),
            key="s_amort",
            help="Total years to pay off the mortgage from origination"
        )
        term_opts = [0.5, 1, 2, 3, 4, 5, 7, 10]
        term_years = b1.selectbox(
            "Term (years)", term_opts,
            index=term_opts.index(sv("s_term", 3)) if sv("s_term", 3) in term_opts else 3,
            key="s_term",
            help="Rate-locked period length"
        )
        _sd_raw = sv("s_startdate", "2023-08-15")
        _sd_val = date.fromisoformat(_sd_raw) if isinstance(_sd_raw, str) else _sd_raw
        start_date_in = b2.date_input(
            "Mortgage Start Date", _sd_val, key="s_startdate",
            help="Date your mortgage originally started"
        )
        if down_pct < 20 and amort_years > 25:
            st.markdown(
                '<div class="warn">⚠️ Insured mortgages limited to 25-yr amortization.</div>',
                unsafe_allow_html=True
            )

        fc = FREQ[payment_freq]
        n_py = fc["n"]
        accel = fc["accel"]

        # ── SECTION C: Additional Renewal Terms (collapsable) ──────────────────
        st.divider()
        with st.expander("🔄 C · Additional Renewal Terms (Past Renewals)", expanded=False):
            st.caption("Add renewal terms that have already taken effect since your mortgage started.")
            if st.button("➕ Add Past Renewal", key="btn_add_rn"):
                if st.session_state.past_renewals:
                    last = st.session_state.past_renewals[-1]
                    prev_end = (
                        date.fromisoformat(last["start_date_str"])
                        + relativedelta(
                            years=int(last["term_years"]),
                            months=int((float(last["term_years"]) % 1) * 12)
                        )
                    )
                else:
                    prev_end = start_date_in + relativedelta(
                        years=int(term_years),
                        months=int((term_years % 1) * 12)
                    )
                st.session_state.past_renewals.append({
                    "id": str(uuid.uuid4())[:8],
                    "start_date_str": str(prev_end),
                    "rate": annual_rate,
                    "mtype": "Fixed",
                    "term_years": 3,
                })
                st.rerun()

            del_rn = []
            for idx, rn in enumerate(st.session_state.past_renewals):
                rr = st.columns([2, 1.5, 1.5, 1.5, 0.7])
                nsd = rr[0].date_input(
                    f"Start #{idx+1}", date.fromisoformat(rn["start_date_str"]),
                    key=f"rn_sd_{rn['id']}"
                )
                nr = rr[1].number_input(
                    f"Rate #{idx+1} (%)", 0.5, 20.0, float(rn["rate"]),
                    0.01, format="%.2f", key=f"rn_rt_{rn['id']}"
                )
                nmt = rr[2].selectbox(
                    f"Type #{idx+1}", ["Fixed", "Variable"],
                    index=0 if rn["mtype"] == "Fixed" else 1,
                    key=f"rn_mt_{rn['id']}"
                )
                nty = rr[3].selectbox(
                    f"Term #{idx+1}", term_opts,
                    index=term_opts.index(rn["term_years"]) if rn["term_years"] in term_opts else 3,
                    key=f"rn_ty_{rn['id']}"
                )
                if rr[4].button("🗑️", key=f"del_rn_{rn['id']}"):
                    del_rn.append(idx)
                end_d = date.fromisoformat(str(nsd)) + relativedelta(
                    years=int(nty), months=int((float(nty) % 1) * 12)
                )
                rr[0].caption(f"End: {end_d.strftime('%b %Y')}")
                st.session_state.past_renewals[idx].update(
                    start_date_str=str(nsd), rate=float(nr), mtype=nmt, term_years=nty
                )
            for i in sorted(del_rn, reverse=True):
                st.session_state.past_renewals.pop(i)
            if del_rn:
                st.rerun()

        # ── SECTION D: Past Prepayments (collapsable) ──────────────────────────
        with st.expander("💳 D · Past Prepayments Already Made", expanded=False):
            if st.button("➕ Add Past Prepayment", key="btn_add_pp"):
                st.session_state.past_prepayments.append({
                    "id": str(uuid.uuid4())[:8],
                    "date_str": str(start_date_in),
                    "amount": 0.0,
                })
                st.rerun()
            del_pp = []
            for idx, pp in enumerate(st.session_state.past_prepayments):
                r = st.columns([2, 2, 1])
                nd = r[0].date_input(
                    f"Date #{idx+1}", date.fromisoformat(pp["date_str"]),
                    min_value=start_date_in, max_value=date.today(),
                    key=f"ppd_{pp['id']}"
                )
                na = r[1].number_input(
                    f"Amount ($) #{idx+1}", 0, 2_000_000, int(pp["amount"]),
                    500, key=f"ppa_{pp['id']}"
                )
                if r[2].button("🗑️", key=f"del_pp_{pp['id']}"):
                    del_pp.append(idx)
                st.session_state.past_prepayments[idx].update(
                    date_str=str(nd), amount=float(na)
                )
            for i in sorted(del_pp, reverse=True):
                st.session_state.past_prepayments.pop(i)
            if del_pp:
                st.rerun()

        # ── Build schedules ────────────────────────────────────────────────────
        past_renewal_rcs = [
            {
                "period": date_to_period(rn["start_date_str"], start_date_in, n_py),
                "new_rate": float(rn["rate"]),
            }
            for rn in st.session_state.past_renewals
        ]
        past_extra = [
            {
                "period": date_to_period(pp["date_str"], start_date_in, n_py),
                "amount": float(pp["amount"]),
            }
            for pp in st.session_state.past_prepayments
            if pp["amount"] > 0
        ]

        full_df, full_sum = build_amortization(
            insured_p, annual_rate, n_py, amort_years,
            accel=accel, start_date=start_date_in,
            extra_payments=past_extra or None,
            rate_changes=past_renewal_rcs or None,
        )
        today_m = get_today_metrics(full_df, n_py)

        orig_term_end_p = int(term_years * n_py)
        _, t_sum = build_amortization(
            insured_p, annual_rate, n_py, amort_years,
            accel=accel, start_date=start_date_in,
            extra_payments=past_extra or None,
            rate_changes=past_renewal_rcs or None,
            term_periods=orig_term_end_p,
        )
        term_end_d = start_date_in + relativedelta(
            years=int(term_years), months=int((term_years % 1) * 12)
        )

        # ── KEY METRICS ────────────────────────────────────────────────────────
        st.divider()
        st.markdown("#### 📊 Key Metrics at a Glance")

        rem_y = today_m.get("remaining_years", 0)
        rem_end = today_m.get("remaining_end_date", "")
        balance_today = today_m.get("balance_today", insured_p)
        current_rate_now = (
            float(past_renewal_rcs[-1]["new_rate"])
            if past_renewal_rcs else annual_rate
        )
        curr_pmt = (
            calc_pmt(balance_today, current_rate_now, n_py, rem_y, accel)
            if rem_y > 0
            else calc_pmt(insured_p, annual_rate, n_py, amort_years, accel)
        )

        mc1, mc2, mc3 = st.columns(3)
        mc1.markdown(f"""
<div class="mc"><h3>Initial Mortgage Principal</h3><p>${insured_p:,.0f}</p></div>
<div class="mc mc-b"><h3>Expected Balance at Term End ({term_end_d.strftime('%b %Y')})</h3><p>${t_sum.get('end_balance', insured_p):,.0f}</p></div>
<div class="mc"><h3>Original Amortization Period</h3><p>{amort_years} years</p></div>
""", unsafe_allow_html=True)

        mc2.markdown(f"""
<div class="mc mc-g"><h3>🟢 Balance as of Today ({today_m.get('as_of_date', '')})</h3><p>${balance_today:,.0f}</p></div>
<div class="mc mc-g"><h3>🟢 Principal Paid to Date</h3><p>${today_m.get('principal_paid_today', 0):,.0f}</p></div>
<div class="mc mc-g"><h3>🟢 Interest Paid to Date</h3><p>${today_m.get('interest_paid_today', 0):,.0f}</p></div>
""", unsafe_allow_html=True)

        mc3.markdown(f"""
<div class="mc"><h3>⏳ Current Remaining Amortization</h3>
<p>{amort_years} yrs original &nbsp;·&nbsp; {rem_y:.1f} yrs more &nbsp;·&nbsp; ends {rem_end}</p></div>
<div class="mc mc-r"><h3>Total Interest (full remaining amortization)</h3><p>${full_sum.get('total_interest', 0):,.0f}</p></div>
<div class="mc"><h3>📆 Current Monthly Payment</h3><p>${curr_pmt:,.2f}</p></div>
""", unsafe_allow_html=True)

        # ── Charts ─────────────────────────────────────────────────────────────
        st.divider()
        cc1, cc2 = st.columns(2)
        with cc1:
            fig_d = go.Figure(go.Pie(
                labels=["Principal", "Total Interest"],
                values=[insured_p, full_sum.get("total_interest", 0)],
                hole=0.55,
                marker_colors=["#1a3c5e", "#e74c3c"],
                textinfo="label+percent",
            ))
            fig_d.update_layout(
                title="Principal vs Total Interest", height=300,
                margin=dict(t=40, b=5)
            )
            st.plotly_chart(fig_d, use_container_width=True, key="ch_donut")

        with cc2:
            today_p_g = today_m.get("period_today", 0)
            fig_pi = stacked_bar_pi(
                full_df, today_p_g, orig_term_end_p,
                "Yearly P & I — 3 Segments"
            )
            st.plotly_chart(fig_pi, use_container_width=True, key="ch_pi")

        # ── Save Setup + Wireframe export ──────────────────────────────────────
        st.divider()
        sv1, sv2, sv3 = st.columns([1, 1, 2])
        if sv1.button("💾 Save Setup to DB", key="btn_ss",
                      help="Persist current mortgage settings to the database"):
            payload = {
                "widget_state": {
                    "s_price": purchase_price, "s_dpct": down_pct,
                    "s_mtype": mortgage_type, "s_freq": payment_freq,
                    "s_rate": annual_rate, "s_amort": amort_years,
                    "s_term": term_years, "s_startdate": str(start_date_in),
                    "s_addcmhc": True,
                },
                "past_renewals": st.session_state.past_renewals,
                "past_prepayments": st.session_state.past_prepayments,
                "summary": full_sum,
                "today_metrics": today_m,
            }
            if db_save_setup(st.session_state.db_conn, payload):
                st.session_state.setup_data = payload
                st.session_state.setup_loaded = True
                sv2.success("✅ Saved to database.")
            else:
                sv2.error("❌ Failed to save.")

        if sv2.button("📄 Export App Wireframe (.docx)", key="btn_wire",
                      help="Download a layout reference document (no financial data)"):
            with st.spinner("Generating wireframe…"):
                try:
                    st.session_state["wire_bytes"] = generate_wireframe_docx({})
                except Exception as ex_:
                    st.error(f"❌ Wireframe failed: {ex_}")

        if st.session_state.get("wire_bytes"):
            sv3.download_button(
                "⬇️ Download Wireframe (.docx)",
                data=st.session_state["wire_bytes"],
                file_name="mortgage_analyzer_wireframe.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="btn_wire_dl",
            )

        # ── Store base state for other tabs ────────────────────────────────────
        st.session_state["base"] = dict(
            principal=insured_p, annual_rate=annual_rate,
            n_py=n_py, amort_years=amort_years,
            accel=accel, start_date=start_date_in,
            mortgage_type=mortgage_type, term_years=term_years,
            payment_freq=payment_freq, purchase_price=purchase_price,
            down_payment=down_pay, past_extra=past_extra,
            past_renewal_rcs=past_renewal_rcs, today_m=today_m,
            orig_term_end_p=orig_term_end_p, current_rate=current_rate_now,
            curr_pmt=curr_pmt, term_end_d=term_end_d, full_sum=full_sum,
            full_df=full_df,  # stored for comparison tab
        )
