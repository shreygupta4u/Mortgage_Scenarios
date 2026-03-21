"""
tabs/tab_scenarios.py — Rate Change Scenarios tab
FIX #1: TypeError on second save — ternary expression replaced with explicit if/else.
FIX #2: Base vs Scenario metrics shown side-by-side in green boxes.
"""
import streamlit as st
from datetime import date
from dateutil.relativedelta import relativedelta
import uuid

from mortgage_math import (
    FREQ, calc_pmt, date_to_period, period_to_date,
    build_amortization, calc_break_penalty, calc_remaining_years,
)
from mortgage_charts import stacked_bar_pi
from mortgage_db import db_load_scenarios, db_save_scenario, db_update_scenario, db_delete_scenario
import plotly.graph_objects as go


def require_setup():
    if not st.session_state.get("base"):
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        st.stop()


def render(tabs_list):
    with tabs_list[1]:
        st.subheader("📈 Rate Change / Renewal Scenarios")
        require_setup()
        b = st.session_state["base"]

        st.info(
            "Create named renewal scenarios. "
            "Early renewals auto-calculate break penalty (advisory). "
            "Variable renewals support sub-scenarios a/b/c."
        )

        db_scenarios = db_load_scenarios(st.session_state.db_conn)
        db_sc_by_name = {s["name"]: s for s in db_scenarios}

        rcs: dict = st.session_state.rc_scenarios
        if st.button("➕ New Scenario", key="btn_new_rc"):
            nid = str(uuid.uuid4())[:8]
            rcs[nid] = {"name": f"Scenario {len(rcs)+1}", "desc": "", "renewals": []}
            st.rerun()
        if not rcs:
            st.markdown(
                '<div class="inf">Click ➕ New Scenario to begin.</div>',
                unsafe_allow_html=True
            )

        orig_term_end_p_sc = b["orig_term_end_p"]
        df_base_ref, s_base_ref = build_amortization(
            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=b.get("past_extra") or None,
            rate_changes=b.get("past_renewal_rcs") or None,
        )
        today_p_ref = b["today_m"].get("period_today", 0)
        base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
        term_opts_sc = [0.5, 1, 2, 3, 4, 5, 7, 10]
        sc_del = []

        for sc_id, sc in rcs.items():
            with st.expander(
                f"📋 {sc['name']}" + (f" — {sc['desc'][:60]}" if sc["desc"] else ""),
                expanded=True
            ):
                h1, h2, h3 = st.columns([2, 3, 1])
                sc["name"] = h1.text_input("Name", sc["name"], key=f"rcn_{sc_id}")
                sc["desc"] = h2.text_area(
                    "Description", sc["desc"], height=80,
                    placeholder=(
                        "Describe this scenario — e.g. Early renewal at lower rate "
                        "anticipating Bank of Canada cuts by mid-2025..."
                    ),
                    key=f"rcd_{sc_id}"
                )
                if h3.button("🗑️ Delete", key=f"del_sc_{sc_id}"):
                    sc_del.append(sc_id)

                # ── Quick Templates ────────────────────────────────────────────
                show_tpl = st.checkbox("🚀 Quick templates", False, key=f"tpl_cb_{sc_id}")
                if show_tpl:
                    ren_p = orig_term_end_p_sc + 1
                    ren_d = str(period_to_date(ren_p, b["start_date"], b["n_py"]))
                    tpls = {
                        "+1% at renewal":   [{"date": ren_d, "rate": b["annual_rate"]+1, "mtype": "Fixed",    "term": 3}],
                        "+2% at renewal":   [{"date": ren_d, "rate": b["annual_rate"]+2, "mtype": "Fixed",    "term": 3}],
                        "-1% at renewal":   [{"date": ren_d, "rate": b["annual_rate"]-1, "mtype": "Fixed",    "term": 3}],
                        "-2% at renewal":   [{"date": ren_d, "rate": b["annual_rate"]-2, "mtype": "Fixed",    "term": 3}],
                        "Variable at renewal": [{"date": ren_d, "rate": b["annual_rate"]-0.5, "mtype": "Variable", "term": 3}],
                        "BoC hike then cut": [
                            {"date": str(period_to_date(ren_p//2, b["start_date"], b["n_py"])), "rate": b["annual_rate"]+2, "mtype": "Fixed", "term": 1},
                            {"date": ren_d, "rate": b["annual_rate"]+1, "mtype": "Fixed", "term": 3},
                        ],
                        "Rate stays flat": [{"date": ren_d, "rate": b["annual_rate"], "mtype": "Fixed", "term": 3}],
                    }
                    tc1, tc2 = st.columns([3, 1])
                    tpl_s = tc1.selectbox("Template", list(tpls.keys()), key=f"tpl_sel_{sc_id}")
                    if tc2.button("Apply", key=f"tpl_ap_{sc_id}"):
                        sc["renewals"] = [
                            {
                                "id": str(uuid.uuid4())[:8],
                                "mode": "By Date",
                                "date_str": t["date"],
                                "period": date_to_period(t["date"], b["start_date"], b["n_py"]),
                                "new_rate": t["rate"],
                                "mtype": t["mtype"],
                                "term_years": t["term"],
                                "actual_penalty": 0,
                                "misc_fees": 250,
                                "orig_posted": t["rate"] + 1.5,
                                "curr_posted": t["rate"] - 0.5,
                                "variable_subs": {},
                            }
                            for t in tpls[tpl_s]
                        ]
                        st.rerun()

                st.markdown("---")
                if st.button("➕ Add Renewal Entry", key=f"add_ren_{sc_id}"):
                    dd = str(
                        b["start_date"]
                        + relativedelta(
                            years=int(b["term_years"]),
                            months=int((b["term_years"] % 1) * 12)
                        )
                        if isinstance(b["start_date"], date)
                        else date.fromisoformat(str(b["start_date"]))
                        + relativedelta(
                            years=int(b["term_years"]),
                            months=int((b["term_years"] % 1) * 12)
                        )
                    )
                    sc["renewals"].append({
                        "id": str(uuid.uuid4())[:8],
                        "mode": "By Date",
                        "date_str": dd,
                        "period": date_to_period(dd, b["start_date"], b["n_py"]),
                        "new_rate": b["annual_rate"],
                        "mtype": "Fixed",
                        "term_years": 3,
                        "actual_penalty": 0,
                        "misc_fees": 250,
                        "orig_posted": b["annual_rate"] + 1.5,
                        "curr_posted": b["annual_rate"] - 0.5,
                        "variable_subs": {},
                    })
                    st.rerun()

                prev_term_end_p = orig_term_end_p_sc
                ren_del = []

                for ri, rn in enumerate(sc["renewals"]):
                    rid = rn["id"]
                    st.markdown(f"**Renewal {ri+1}**")
                    rc1, rc2, rc3, rc4, rc5 = st.columns([1.5, 1.8, 1.5, 1.5, 0.7])

                    rn["mode"] = rc1.radio(
                        "Mode", ["By Date", "By Period"],
                        index=0 if rn.get("mode", "By Date") == "By Date" else 1,
                        horizontal=True, key=f"rm_{sc_id}_{rid}"
                    )
                    if rn["mode"] == "By Date":
                        pd_v = rc2.date_input(
                            "Effective date",
                            date.fromisoformat(rn.get("date_str", str(b["start_date"]))),
                            key=f"rd_{sc_id}_{rid}"
                        )
                        rn["date_str"] = str(pd_v)
                        rn["period"] = date_to_period(pd_v, b["start_date"], b["n_py"])
                        rc2.caption(f"≈ Period {rn['period']}")
                    else:
                        mx = int(b["amort_years"] * b["n_py"])
                        rn["period"] = int(rc2.number_input(
                            "Period #", 1, mx,
                            int(rn.get("period", orig_term_end_p_sc + 1)),
                            key=f"rp_{sc_id}_{rid}"
                        ))
                        rc2.caption(
                            f"≈ {period_to_date(rn['period'], b['start_date'], b['n_py']).strftime('%b %Y')}"
                        )

                    rn["mtype"] = rc3.selectbox(
                        "Type", ["Fixed", "Variable"],
                        index=0 if rn.get("mtype", "Fixed") == "Fixed" else 1,
                        key=f"rmt_{sc_id}_{rid}"
                    )
                    rn["new_rate"] = float(rc4.number_input(
                        "Rate (%)", 0.5, 20.0,
                        float(rn.get("new_rate", b["annual_rate"])),
                        0.01, format="%.2f",
                        key=f"rrt_{sc_id}_{rid}",
                        help="New interest rate at this renewal"
                    ))
                    if rc5.button("🗑️", key=f"delren_{sc_id}_{rid}"):
                        ren_del.append(ri)

                    rn["term_years"] = st.selectbox(
                        f"Term (years) — Renewal {ri+1}", term_opts_sc,
                        index=term_opts_sc.index(rn.get("term_years", 3))
                        if rn.get("term_years", 3) in term_opts_sc else 3,
                        key=f"rty_{sc_id}_{rid}"
                    )
                    rn_start_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
                    rn_end_d = rn_start_d + relativedelta(
                        years=int(rn["term_years"]),
                        months=int((float(rn["term_years"]) % 1) * 12)
                    )
                    st.caption(
                        f"📅 Term: **{rn_start_d.strftime('%b %d, %Y')}** → "
                        f"**{rn_end_d.strftime('%b %d, %Y')}**"
                    )

                    is_early = rn["period"] < prev_term_end_p
                    months_left_at = (
                        max(int((prev_term_end_p - rn["period"]) / b["n_py"] * 12), 1)
                        if is_early else 0
                    )
                    if is_early:
                        ren_df = df_base_ref[df_base_ref["Period"] <= rn["period"]]
                        bal_ren = float(ren_df["Balance"].iloc[-1]) if not ren_df.empty else b["principal"]
                        rate_ren = float(ren_df["Rate (%)"].iloc[-1]) if not ren_df.empty else b["annual_rate"]
                        st.markdown(
                            f'<div class="warn">⚡ <b>Early Renewal</b> — '
                            f'{months_left_at} months remain · Balance: <b>${bal_ren:,.0f}</b></div>',
                            unsafe_allow_html=True
                        )
                        bp1, bp2 = st.columns(2)
                        rn["orig_posted"] = float(bp1.number_input(
                            "Original posted rate (%)", 0.5, 20.0,
                            float(rn.get("orig_posted", rate_ren + 1.5)),
                            0.01, format="%.2f",
                            key=f"op_{sc_id}_{rid}",
                            help="Bank's posted rate at origination"
                        ))
                        rn["curr_posted"] = float(bp2.number_input(
                            "Current posted rate (%)", 0.5, 20.0,
                            float(rn.get("curr_posted", max(rate_ren - 0.5, 0.5))),
                            0.01, format="%.2f",
                            key=f"cp_{sc_id}_{rid}",
                            help="Current posted rate for remaining term"
                        ))
                        adv = calc_break_penalty(
                            bal_ren, rate_ren, rn["mtype"],
                            rn["orig_posted"], rn["curr_posted"], months_left_at
                        )
                        pen_opts = [f"3-Month Interest (${adv['3_months_interest']:,.0f})"]
                        if adv["ird"] is not None:
                            pen_opts.append(f"IRD (${adv['ird']:,.0f})")
                        pen_opts.append("Custom value")
                        pc1, pc2 = st.columns([3, 2])
                        pen_choice = pc1.radio(
                            "Apply which penalty?", pen_opts,
                            key=f"pen_radio_{sc_id}_{rid}",
                            help="Advisory — your bank may charge differently"
                        )
                        if "Custom" in pen_choice:
                            custom_str = pc2.text_input(
                                "", value=str(int(adv["calc_penalty"])),
                                key=f"cpen_{sc_id}_{rid}"
                            )
                            pc2.caption("Custom value ($)")
                            try:
                                rn["actual_penalty"] = float(
                                    custom_str.replace(",", "").replace("$", "")
                                )
                            except Exception:
                                rn["actual_penalty"] = adv["calc_penalty"]
                        elif "IRD" in pen_choice:
                            rn["actual_penalty"] = adv["ird"] or 0.0
                        else:
                            rn["actual_penalty"] = adv["3_months_interest"]

                        rn["misc_fees"] = float(st.number_input(
                            "Misc fees ($)", 0, 50_000,
                            int(rn.get("misc_fees", 500)), 50,
                            key=f"mf_{sc_id}_{rid}"
                        ))
                        total_exit = rn["actual_penalty"] + rn["misc_fees"]
                        old_pmt = calc_pmt(
                            bal_ren, rate_ren, 12,
                            max(b["amort_years"] - rn["period"] / b["n_py"], 1)
                        )
                        new_pmt = calc_pmt(
                            bal_ren, rn["new_rate"], 12,
                            max(b["amort_years"] - rn["period"] / b["n_py"], 1)
                        )
                        st.markdown(
                            f'<div class="pen">💸 Penalty applied: <b>${rn["actual_penalty"]:,.0f}</b> · '
                            f'Misc: <b>${rn["misc_fees"]:,.0f}</b> · '
                            f'<b>Total: ${total_exit:,.0f}</b></div>',
                            unsafe_allow_html=True
                        )
                        if abs(old_pmt - new_pmt) > 1:
                            rec = total_exit / abs(old_pmt - new_pmt)
                            st.caption(
                                f"Monthly saving: ${abs(old_pmt-new_pmt):,.0f} → "
                                f"Break-even: **{rec:.0f} months** ({rec/12:.1f} yrs)"
                            )
                    else:
                        rn["misc_fees"] = float(st.number_input(
                            "Misc fees ($)", 0, 50_000,
                            int(rn.get("misc_fees", 250)), 50,
                            key=f"mf2_{sc_id}_{rid}"
                        ))
                        rn["actual_penalty"] = 0

                    # Variable sub-scenarios
                    if rn["mtype"] == "Variable":
                        if "variable_subs" not in rn:
                            rn["variable_subs"] = {}
                        vsubs = rn["variable_subs"]
                        if st.button("➕ Add Sub-Scenario", key=f"add_vsub_{sc_id}_{rid}"):
                            letter = chr(ord('a') + len(vsubs))
                            vsubs[letter] = {"name": f"Sub {letter}", "n_changes": 1, "changes": []}
                            st.rerun()
                        vsub_del = []
                        for sub_k, sub in vsubs.items():
                            vsa, vsb, vsc_ = st.columns([2, 1, 1])
                            sub["name"] = vsa.text_input(
                                "Name", sub["name"], key=f"vsn_{sc_id}_{rid}_{sub_k}"
                            )
                            sub["n_changes"] = int(vsb.number_input(
                                "# changes", 1, 12, int(sub.get("n_changes", 1)), 1,
                                key=f"vsnc_{sc_id}_{rid}_{sub_k}"
                            ))
                            if vsc_.button("🗑️", key=f"del_vsub_{sc_id}_{rid}_{sub_k}"):
                                vsub_del.append(sub_k)
                            while len(sub["changes"]) < sub["n_changes"]:
                                sub["changes"].append({
                                    "id": str(uuid.uuid4())[:8],
                                    "date_str": rn["date_str"],
                                    "new_rate": rn["new_rate"],
                                })
                            sub["changes"] = sub["changes"][:sub["n_changes"]]
                            for ci_, chg in enumerate(sub["changes"]):
                                vc1, vc2 = st.columns(2)
                                chg_d = vc1.date_input(
                                    f"Change {ci_+1} — date",
                                    value=date.fromisoformat(chg.get("date_str", rn["date_str"])),
                                    key=f"vcd_{sc_id}_{rid}_{sub_k}_{ci_}"
                                )
                                chg["date_str"] = str(chg_d)
                                chg["period"] = date_to_period(chg_d, b["start_date"], b["n_py"])
                                chg["new_rate"] = float(vc2.number_input(
                                    f"Rate (%) — {ci_+1}", 0.5, 20.0,
                                    float(chg.get("new_rate", rn["new_rate"])),
                                    0.01, format="%.2f",
                                    key=f"vcr_{sc_id}_{rid}_{sub_k}_{ci_}"
                                ))
                        for sk in vsub_del:
                            del vsubs[sk]
                        if vsub_del:
                            st.rerun()

                    prev_term_end_p = int(rn["period"]) + int(float(rn["term_years"]) * b["n_py"])
                    st.markdown("---")

                for ri in sorted(ren_del, reverse=True):
                    sc["renewals"].pop(ri)
                if ren_del:
                    st.rerun()

                # ── Build scenario schedule ────────────────────────────────────
                if sc["renewals"]:
                    last_rn = sc["renewals"][-1]
                    sc_term_end_p = int(last_rn["period"]) + int(float(last_rn["term_years"]) * b["n_py"])
                else:
                    sc_term_end_p = orig_term_end_p_sc

                main_rc = [
                    {"period": rn["period"], "new_rate": rn["new_rate"]}
                    for rn in sc["renewals"]
                ]
                all_rcs_sc = (b.get("past_renewal_rcs") or []) + main_rc
                df_sc, s_sc = build_amortization(
                    b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                    accel=b["accel"], start_date=b["start_date"],
                    extra_payments=b.get("past_extra") or None,
                    rate_changes=all_rcs_sc or None,
                )

                tm = b["today_m"]
                today_p_sc = tm.get("period_today", 0)
                rem_sc = (
                    round((len(df_sc) - today_p_sc) / b["n_py"], 1)
                    if today_p_sc > 0 and not df_sc.empty
                    else b["amort_years"]
                )
                last_rate = sc["renewals"][-1]["new_rate"] if sc["renewals"] else b["annual_rate"]
                today_bal = tm.get("balance_today", b["principal"])
                calc_monthly_sc = (
                    calc_pmt(today_bal, last_rate, b["n_py"], rem_sc, b["accel"])
                    if rem_sc > 0 else 0
                )

                # ── FIX #2: Parallel Base | Scenario metrics in green boxes ───
                st.markdown("##### 📊 Base vs Scenario Metrics")
                _base_int = s_base_ref.get("total_interest", 0)
                _sc_int = s_sc.get("total_interest", 0)
                _sc_int_delta = _sc_int - _base_int
                _delta_colour = "mc-g" if _sc_int_delta < 0 else ("mc-r" if _sc_int_delta > 0 else "")
                _arrow = "▼" if _sc_int_delta < 0 else ("▲" if _sc_int_delta > 0 else "—")

                col_base, col_sc = st.columns(2)
                col_base.markdown(f"""
<div class="mc mc-b" style="margin-bottom:.5rem;">
  <h3>Current Interest (base)</h3>
  <p>${_base_int:,.0f}</p>
</div>
<div class="mc mc-b" style="margin-bottom:.5rem;">
  <h3>Current Remaining</h3>
  <p>{base_remaining_yrs:.1f} yrs</p>
</div>
<div class="mc mc-b">
  <h3>Required Monthly Payment (base)</h3>
  <p>${calc_pmt(today_bal, b["annual_rate"], b["n_py"], base_remaining_yrs, b["accel"]):,.2f}</p>
</div>
""", unsafe_allow_html=True)

                col_sc.markdown(f"""
<div class="mc {_delta_colour}" style="margin-bottom:.5rem;">
  <h3>Adjusted Interest (scenario) &nbsp;{_arrow} ${abs(_sc_int_delta):,.0f}</h3>
  <p>${_sc_int:,.0f}</p>
</div>
<div class="mc mc-g" style="margin-bottom:.5rem;">
  <h3>Adjusted Remaining</h3>
  <p>{rem_sc:.1f} yrs</p>
</div>
<div class="mc mc-g">
  <h3>Required Monthly Payment (scenario)</h3>
  <p>${calc_monthly_sc:,.2f}</p>
</div>
""", unsafe_allow_html=True)

                # ── Payment override ───────────────────────────────────────────
                st.markdown("##### 💳 Payment & Amortization Impact")
                min_pmt = float(max(today_bal * (last_rate / 100 / b["n_py"]) + 1, 100))
                pay1, pay2, pay3 = st.columns(3)
                user_pmt = pay1.number_input(
                    "Monthly payment ($)",
                    min_value=min_pmt,
                    max_value=float(today_bal),
                    value=round(calc_monthly_sc, 2),
                    step=50.0, format="%.2f",
                    key=f"user_pmt_{sc_id}",
                    help=(
                        f"Calculated payment to maintain current amortization: "
                        f"${calc_monthly_sc:,.2f}. "
                        "Increase to pay off faster, decrease to extend."
                    )
                )
                pay1.caption(f"To keep current amortization: **${calc_monthly_sc:,.2f}**")

                adj_rem = (
                    calc_remaining_years(today_bal, last_rate, b["n_py"], user_pmt)
                    if user_pmt > 0 and today_bal > 0
                    else rem_sc
                )
                adj_end = date.today() + relativedelta(
                    years=int(adj_rem), months=int((adj_rem % 1) * 12)
                )
                pmt_changed = abs(user_pmt - calc_monthly_sc) > 0.5

                if pmt_changed:
                    colour = "mc-g" if user_pmt > calc_monthly_sc else "mc-r"
                    pay2.markdown(
                        f'<div class="mc {colour}"><h3>Adjusted Remaining</h3>'
                        f'<p>{adj_rem:.1f} yrs</p></div>',
                        unsafe_allow_html=True
                    )
                    pay3.markdown(
                        f'<div class="mc {colour}"><h3>Mortgage-free by</h3>'
                        f'<p>{adj_end.strftime("%b %Y")}</p></div>',
                        unsafe_allow_html=True
                    )
                else:
                    pay2.markdown(
                        f'<div class="mc mc-g"><h3>Adjusted Remaining</h3>'
                        f'<p>{adj_rem:.1f} yrs</p></div>',
                        unsafe_allow_html=True
                    )
                    pay3.markdown(
                        f'<div class="mc mc-g"><h3>Mortgage-free by</h3>'
                        f'<p>{adj_end.strftime("%b %Y")}</p></div>',
                        unsafe_allow_html=True
                    )

                # ── Charts ────────────────────────────────────────────────────
                if not df_sc.empty:
                    fig_sc_bar = stacked_bar_pi(
                        df_sc, today_p_sc, sc_term_end_p,
                        f"{sc['name']} — P & I Breakdown"
                    )
                    st.plotly_chart(fig_sc_bar, use_container_width=True, key=f"ch_sc_bar_{sc_id}")

                    fig_rr = go.Figure()
                    fig_rr.add_scatter(
                        x=df_sc["Date"], y=df_sc["Rate (%)"],
                        fill="tozeroy", name="Rate", line=dict(color="#27ae60")
                    )
                    fig_rr.update_layout(
                        title="Rate over time", xaxis_title="Date",
                        yaxis_title="%", height=200, margin=dict(t=40, b=30)
                    )
                    st.plotly_chart(fig_rr, use_container_width=True, key=f"ch_rate_{sc_id}")

                # Variable sub-scenario charts
                for rn in sc["renewals"]:
                    if rn["mtype"] != "Variable":
                        continue
                    for sub_k, sub in rn.get("variable_subs", {}).items():
                        sub_rc = all_rcs_sc + [
                            {"period": chg["period"], "new_rate": chg["new_rate"]}
                            for chg in sub["changes"]
                        ]
                        df_sub, s_sub = build_amortization(
                            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                            accel=b["accel"], start_date=b["start_date"],
                            extra_payments=b.get("past_extra") or None,
                            rate_changes=sub_rc or None,
                        )
                        if df_sub.empty:
                            continue
                        sub_id = f"{sc['name']}{sub_k}"
                        rem_sub = (
                            round((len(df_sub) - today_p_sc) / b["n_py"], 1)
                            if today_p_sc > 0 else b["amort_years"]
                        )
                        st.markdown(f"**Sub-scenario {sub_id}: {sub['name']}**")
                        s1, s2, s3 = st.columns(3)
                        s1.metric(
                            "Interest", f"${s_sub.get('total_interest', 0):,.0f}",
                            delta=f"${s_sub.get('total_interest', 0)-s_base_ref.get('total_interest', 0):+,.0f}"
                        )
                        s2.metric("Remaining", f"{rem_sub:.1f} yrs")
                        s3.metric("End Balance", f"${s_sub.get('end_balance', 0):,.0f}")
                        fig_sub_ = stacked_bar_pi(
                            df_sub, today_p_sc, sc_term_end_p,
                            f"Sub {sub_id}: {sub['name']}"
                        )
                        st.plotly_chart(
                            fig_sub_, use_container_width=True,
                            key=f"ch_sub_{sc_id}_{rn['id']}_{sub_k}"
                        )

                # ── FIX #1: Save scenario — explicit if/else (no ternary) ─────
                st.markdown("---")
                sav_n = st.text_input("Save as", sc["name"], key=f"sc_save_name_{sc_id}")
                sc_col1, sc_col2 = st.columns(2)

                if sc_col1.button("💾 Save scenario", key=f"save_rc_{sc_id}"):
                    if not sav_n or not sav_n.strip():
                        sc_col2.error("❌ Scenario name cannot be empty.")
                    elif not sc["renewals"]:
                        sc_col2.error("❌ Add at least one renewal entry before saving.")
                    else:
                        sc_params = {
                            **{k: v for k, v in b.items() if k not in ("full_df",)},
                            "start_date": str(b["start_date"]),
                            "rate_changes": main_rc,
                            "sc_name": sc["name"],
                            "sc_desc": sc["desc"],
                        }
                        sc_summary = {k: v for k, v in s_sc.items()}
                        existing = db_sc_by_name.get(sav_n.strip())
                        if existing:
                            ok = db_update_scenario(
                                st.session_state.db_conn,
                                existing["id"],
                                sav_n.strip(),
                                sc_params,
                                sc_summary,
                            )
                            # FIX #1 — explicit if/else instead of ternary
                            if ok:
                                sc_col2.success(f"✅ Updated in DB: {sav_n}")
                            else:
                                sc_col2.error("❌ Update failed — check DB connection.")
                        else:
                            ok = db_save_scenario(
                                st.session_state.db_conn,
                                sav_n.strip(),
                                sc_params,
                                sc_summary,
                            )
                            # FIX #1 — explicit if/else instead of ternary
                            if ok:
                                sc_col2.success(f"✅ Saved to DB: {sav_n}")
                            else:
                                sc_col2.error("❌ Save failed — check DB connection.")

        for sc_id in sc_del:
            del rcs[sc_id]
        if sc_del:
            st.rerun()

        # ── Saved scenarios from DB ────────────────────────────────────────────
        db_scenarios_fresh = db_load_scenarios(st.session_state.db_conn)
        if db_scenarios_fresh:
            st.markdown("---")
            st.markdown("##### 📂 Previously Saved Scenarios (click to view / edit / delete)")
            for sc_db in db_scenarios_fresh:
                already_editing = any(s["name"] == sc_db["name"] for s in rcs.values())
                if already_editing:
                    continue
                s = sc_db["summary"]
                with st.expander(
                    f"💾 {sc_db['name']}  ·  saved {sc_db['created_at'][:16]}",
                    expanded=False
                ):
                    cc_ = st.columns(4)
                    cc_[0].metric("Scenario Interest", f"${s.get('total_interest', 0):,.0f}",
                                  help="Total interest under this scenario")
                    cc_[1].metric("Payoff", f"{s.get('payoff_years', 0):.1f} yrs",
                                  help="Full amortization payoff period")
                    cc_[2].metric("Payment", f"${s.get('payment', 0):,.2f}",
                                  help="Regular payment amount")
                    cc_[3].metric("End Balance", f"${s.get('end_balance', 0):,.0f}",
                                  help="Balance at end of amortization")
                    ea, eb, ec = st.columns(3)
                    if ea.button("✏️ Load for Editing", key=f"edit_db_{sc_db['id']}"):
                        rcs_list = sc_db["params"].get("rate_changes", [])
                        nid = str(uuid.uuid4())[:8]
                        rcs[nid] = {
                            "name": sc_db["name"],
                            "desc": sc_db["params"].get("sc_desc", ""),
                            "renewals": [
                                {
                                    "id": str(uuid.uuid4())[:8],
                                    "mode": "By Period",
                                    "date_str": str(period_to_date(rc["period"], b["start_date"], b["n_py"])),
                                    "period": rc["period"],
                                    "new_rate": rc["new_rate"],
                                    "mtype": "Fixed",
                                    "term_years": 3,
                                    "actual_penalty": 0,
                                    "misc_fees": 250,
                                    "orig_posted": rc["new_rate"] + 1.5,
                                    "curr_posted": rc["new_rate"] - 0.5,
                                    "variable_subs": {},
                                }
                                for rc in rcs_list
                            ],
                        }
                        st.success(f"Loaded '{sc_db['name']}' into editor above — scroll up.")
                        st.rerun()
                    if eb.button("🗑️ Delete", key=f"del_db_{sc_db['id']}"):
                        db_delete_scenario(st.session_state.db_conn, sc_db["id"])
                        st.success("Deleted.")
                        st.rerun()
                    show_raw = ec.checkbox("Raw params", False, key=f"raw_{sc_db['id']}")
                    if show_raw:
                        st.json(sc_db.get("params", {}))

        # Education
        st.divider()
        with st.expander("📚 Canadian Mortgage Education"):
            st.markdown(
                "**Semi-annual compounding** (Interest Act): Rate 5.39% → "
                "Eff. annual `(1+0.0539/2)²=5.463%` · Monthly `1.05463^(1/12)-1=0.4453%`\n\n"
                "**CMHC**: <10%=4%, 10-15%=3.1%, 15-20%=2.8%, ≥20%=0%. Price ≤$1.5M required.\n\n"
                "**Break penalty**: Variable=3 months interest · "
                "Fixed=max(3 months interest, IRD) where IRD=(orig posted−curr posted)×bal×remaining yrs"
            )
