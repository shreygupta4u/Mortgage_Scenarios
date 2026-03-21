"""
tabs/tab_breakpenalty.py — Break Penalty Calculator tab
"""
import streamlit as st
import numpy as np
import plotly.graph_objects as go

from mortgage_math import calc_pmt, calc_break_penalty, build_amortization


def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        st.stop()


def render(tabs_list):
    with tabs_list[4]:
        st.subheader("⚠️ Mortgage Break Penalty Calculator")
        require_setup()
        b = st.session_state["base"]

        c1, c2 = st.columns(2)
        with c1:
            bp_bal = st.number_input(
                "Outstanding Balance ($)", 100, 5_000_000,
                int(b.get("principal", 500_000) * 0.85), 1_000,
                key="bp_bal",
                help="Current outstanding mortgage balance"
            )
            bp_rate = st.number_input(
                "Contract Rate (%)", 0.5, 20.0,
                float(b.get("annual_rate", 5.39)), 0.01, format="%.2f",
                key="bp_rate",
                help="Your current mortgage interest rate"
            )
            bp_mtype = st.selectbox(
                "Mortgage Type", ["Fixed", "Variable"],
                index=0 if b.get("mortgage_type", "Fixed") == "Fixed" else 1,
                key="bp_mtype_tab5",
                help="Fixed or Variable rate"
            )
            bp_mleft = st.slider(
                "Months Remaining in Term", 1, 120, 36,
                key="bp_mleft",
                help="Months left in current term"
            )
            bp_misc = st.number_input(
                "Miscellaneous Fees ($)", 0, 50_000, 500, 50,
                key="bp_misc",
                help="Admin, appraisal, legal fees"
            )

        with c2:
            if bp_mtype == "Fixed":
                st.markdown("##### IRD Inputs")
                bp_orig = st.number_input(
                    "Posted Rate at Origination (%)", 0.5, 20.0,
                    float(b.get("annual_rate", 5.39)) + 1.5, 0.01, format="%.2f",
                    key="bp_orig",
                    help="Bank's posted rate when you originally signed"
                )
                bp_curr = st.number_input(
                    "Current Posted Rate for Remaining Term (%)", 0.5, 20.0,
                    max(float(b.get("annual_rate", 5.39)) - 0.5, 0.5),
                    0.01, format="%.2f",
                    key="bp_curr",
                    help="Current posted rate for remaining term length"
                )
            else:
                bp_orig = bp_curr = float(b.get("annual_rate", 5.39))

        pen = calc_break_penalty(bp_bal, bp_rate, bp_mtype, bp_orig, bp_curr, bp_mleft)
        pen_opts = [f"3-Month Interest (${pen['3_months_interest']:,.0f})"]
        if pen["ird"] is not None:
            pen_opts.append(f"IRD (${pen['ird']:,.0f})")
        pen_opts.append("Custom value")

        bpc1, bpc2 = st.columns([3, 2])
        bp_choice = bpc1.radio(
            "Apply which penalty?", pen_opts,
            key="bp_pen_radio",
            help="Advisory estimate — your bank may calculate differently"
        )
        if "Custom" in bp_choice:
            bp_custom_str = bpc2.text_input(
                "", value=str(int(pen["calc_penalty"])), key="bp_custom_inp"
            )
            bpc2.caption("Custom value ($)")
            try:
                actual_pen = float(bp_custom_str.replace(",", "").replace("$", ""))
            except Exception:
                actual_pen = pen["calc_penalty"]
        elif "IRD" in bp_choice:
            actual_pen = pen["ird"] or 0.0
        else:
            actual_pen = pen["3_months_interest"]

        total_exit = actual_pen + bp_misc
        cc1, cc2_, cc3, cc4 = st.columns(4)
        cc1.metric("3 Months Interest", f"${pen['3_months_interest']:,.2f}",
                   help="3 months interest on outstanding balance")
        if pen["ird"] is not None:
            cc2_.metric("IRD", f"${pen['ird']:,.2f}",
                        help="Interest Rate Differential penalty")
        cc3.metric("Penalty Applied", f"${actual_pen:,.2f}",
                   help="The penalty amount you chose")
        cc4.metric("Total Exit Cost", f"${total_exit:,.2f}",
                   help="Penalty + miscellaneous fees")

        st.divider()
        new_r = st.slider(
            "New rate if you break (%)", 0.5, 15.0,
            max(float(b.get("annual_rate", 5.39)) - 1.0, 0.5), 0.05,
            key="bp_newr",
            help="What rate you expect to get at a new lender"
        )
        ar = max(bp_mleft / 12, 1)
        _, s_stay = build_amortization(bp_bal, bp_rate, 12, ar, term_periods=bp_mleft)
        _, s_brk = build_amortization(bp_bal, new_r, 12, ar, term_periods=bp_mleft)

        int_stay = s_stay.get("total_interest", 0)
        int_brk = s_brk.get("total_interest", 0) + total_exit

        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("Interest (Stay)", f"${int_stay:,.0f}",
                   help="Interest over remaining term if you stay")
        bc2.metric("Interest+Fees (Break)", f"${int_brk:,.0f}",
                   help="Interest at new rate + exit fees")
        bc3.metric(
            "Net Savings", f"${int_stay - int_brk:,.0f}",
            delta="✅ Worth breaking" if int_stay > int_brk else "❌ Not worth it",
            help="Positive = breaking saves money"
        )

        sweep = np.arange(0.5, float(b.get("annual_rate", 5.39)) + 0.11, 0.25)
        svlist = []
        for tr in sweep:
            _, st_ = build_amortization(bp_bal, tr, 12, ar, term_periods=bp_mleft)
            svlist.append(int_stay - (st_.get("total_interest", 0) + total_exit))

        fig_be = go.Figure()
        fig_be.add_scatter(
            x=list(sweep), y=svlist, mode="lines+markers",
            line=dict(color="#1a3c5e"), name="Net Savings"
        )
        fig_be.add_hline(
            y=0, line_dash="dash", line_color="red",
            annotation_text="Break-even", annotation_position="top right"
        )
        fig_be.add_vline(
            x=float(b.get("annual_rate", 5.39)),
            line_dash="dot", line_color="orange",
            annotation_text="Current rate", annotation_position="top left"
        )
        fig_be.update_layout(
            title="Net Savings vs New Rate",
            xaxis_title="New Rate (%)", yaxis_title="Net Savings ($)",
            height=320, margin=dict(t=50, b=40)
        )
        st.plotly_chart(fig_be, use_container_width=True, key="ch_bpbe")

        op = calc_pmt(bp_bal, bp_rate, 12, ar)
        np_ = calc_pmt(bp_bal, new_r, 12, ar)
        if abs(op - np_) > 1:
            st.markdown(
                f'<div class="inf">Monthly: <b>${op:,.2f}</b> → <b>${np_:,.2f}</b> '
                f'(<b>${np_-op:+,.2f}/mo</b>) · Recoup in: '
                f'<b>{total_exit/abs(op-np_):.0f} months</b></div>',
                unsafe_allow_html=True
            )
