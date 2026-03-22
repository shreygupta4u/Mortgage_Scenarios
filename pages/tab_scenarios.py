"""pages/tab_scenarios.py
Tab 2: Rate Change + Prepayment Scenarios (combined).
Fixes applied:
  #2  Adjusted Remaining = calc_remaining_years from today at scenario rate
      with explicit equality-guard when payment unchanged
  #3  Single numbered list, no separate "saved" section
  #4  Penalty persisted in session_state keyed by scenario+renewal
  #5  Custom penalty always-visible textbox
  #7  Prepayment options embedded in each scenario
  #8  (tab order handled in app.py)
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
from dateutil.relativedelta import relativedelta
import uuid

from modules import (
    FREQ, periodic_rate, calc_pmt, date_to_period, period_to_date,
    calc_remaining_years, build_amortization, calc_break_penalty,
    db_save_scenario, db_load_scenarios, db_delete_scenario,
    stacked_bar_pi,
)

# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
LUMP_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
LUMP_MM = {m:i+1 for i,m in enumerate(LUMP_MONTHS)}

def _make_extra_payments(pp, n_py, base_principal, past_extra):
    """Build extra_payments list from scenario prepayment settings."""
    future = []
    al = pp.get("annual_lump", 0)
    if al > 0:
        lm = pp.get("lump_month", 1)
        for yr in range(int(pp.get("lump_start_year",1)),
                        int(pp.get("lump_start_year",1)) + int(pp.get("lump_num_years",0))):
            p = max(1, int((yr-1)*n_py + lm*n_py/12))
            future.append({"period": p, "amount": float(al)})
    inc_t = pp.get("pay_increase_type", "None")
    inc_v = pp.get("pay_increase_val", 0)
    if inc_t == "Fixed" and inc_v > 0:
        for p in range(1, int(30*n_py)+1):
            future.append({"period": p, "amount": float(inc_v)})
    elif inc_t == "Pct" and inc_v > 0:
        base_pmt = calc_pmt(base_principal, 5.39, n_py, 30)  # placeholder, overridden later
        for p in range(1, int(30*n_py)+1):
            future.append({"period": p, "amount": base_pmt * inc_v / 100})
    otp = pp.get("onetime_period", 0)
    ota = pp.get("onetime_amount", 0)
    if otp > 0 and ota > 0:
        future.append({"period": int(otp), "amount": float(ota)})
    return (past_extra or []) + future


def _penalty_key(sc_id, rid):
    return f"_penalty_{sc_id}_{rid}"


def render_tab_scenarios(conn, b):
    st.subheader("📈 Rate Change + Prepayment Scenarios")
    if not b:
        st.info("⬅️ Complete Setup & Overview first."); return

    # ── Load DB scenarios into session state (once per connect) ──
    if "sc_loaded_from_db" not in st.session_state:
        db_rows = db_load_scenarios(conn)
        rcs = {}
        for i, sc in enumerate(db_rows, 1):
            sc["_seq"] = i
            sc["_key"] = str(uuid.uuid4())[:8]
            rcs[sc["_key"]] = sc
        st.session_state.rc_scenarios = rcs
        st.session_state.sc_loaded_from_db = True
    rcs: dict = st.session_state.rc_scenarios

    # ── New scenario button ───────────────────────────────────────
    if st.button("➕ New Scenario", key="btn_new_rc"):
        nk = str(uuid.uuid4())[:8]
        next_seq = max((s.get("_seq", 0) for s in rcs.values()), default=0) + 1
        rcs[nk] = {"_key": nk, "_seq": next_seq, "db_id": None,
                   "name": f"Scenario {next_seq}", "desc": "",
                   "renewals": [], "pp": _default_pp()}
        st.rerun()

    # ── Shared data ───────────────────────────────────────────────
    orig_term_end_p = b["orig_term_end_p"]
    df_base, s_base = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None)
    base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
    today_p = b["today_m"].get("period_today", 0)
    today_bal = b["today_m"].get("balance_today", b["principal"])
    term_opts = [0.5, 1, 2, 3, 4, 5, 7, 10]
    sc_del = []

    # Sort scenarios by _seq for consistent numbering
    sorted_scs = sorted(rcs.items(), key=lambda kv: kv[1].get("_seq", 999))

    for sc_id, sc in sorted_scs:
        seq = sc.get("_seq", "?")
        label = f"**Scenario {seq}** — {sc['name']}" + (f"  ·  _{sc['desc'][:55]}_" if sc["desc"] else "")
        with st.expander(label, expanded=(sc.get("db_id") is None)):
            _render_scenario(conn, sc_id, sc, b, df_base, s_base, base_remaining_yrs,
                             today_p, today_bal, orig_term_end_p, term_opts, sc_del)

    for k in sc_del: del rcs[k]
    if sc_del: st.rerun()

    # Education
    st.divider()
    with st.expander("📚 Canadian Mortgage Education"):
        st.markdown("**Semi-annual compounding**: `(1+r/200)²` · **CMHC**: <10%=4%, 10–15%=3.1%, "
                    "15–20%=2.8%, ≥20%=0% · **Break penalty**: Variable=3mo interest; Fixed=max(3mo, IRD)")


def _default_pp():
    return {"annual_lump": 0, "lump_month": 1, "lump_start_year": 1, "lump_num_years": 0,
            "pay_increase_type": "None", "pay_increase_val": 0.0,
            "onetime_period": 0, "onetime_amount": 0}


def _render_scenario(conn, sc_id, sc, b, df_base, s_base, base_remaining_yrs,
                     today_p, today_bal, orig_term_end_p, term_opts, sc_del):
    # ── Name / desc ───────────────────────────────────────────────
    hn1, hn2, hn3 = st.columns([2, 3, 1])
    sc["name"] = hn1.text_input("Name", sc["name"], key=f"rcn_{sc_id}")
    sc["desc"] = hn2.text_area("Description", sc["desc"], height=72,
                                placeholder="Describe the scenario (rate renewal plan, prepayments, etc.)",
                                key=f"rcd_{sc_id}")
    if hn3.button("🗑️ Delete", key=f"del_sc_{sc_id}"): sc_del.append(sc_id)

    # ── Renewals ──────────────────────────────────────────────────
    st.markdown("##### 🔄 Rate Renewals")
    if st.button("➕ Add Renewal", key=f"add_ren_{sc_id}"):
        dd = str(b["start_date"] + relativedelta(years=int(b["term_years"]),
                                                  months=int((b["term_years"]%1)*12)))
        sc["renewals"].append({"id": str(uuid.uuid4())[:8], "mode": "By Date",
            "date_str": dd, "period": date_to_period(dd, b["start_date"], b["n_py"]),
            "new_rate": b["annual_rate"], "mtype": "Fixed", "term_years": 3,
            "actual_penalty": 0, "misc_fees": 250,
            "orig_posted": b["annual_rate"]+1.5, "curr_posted": b["annual_rate"]-0.5,
            "variable_subs": {}})
        st.rerun()

    prev_term_end_p = orig_term_end_p
    ren_del = []
    for ri, rn in enumerate(sc["renewals"]):
        rid = rn["id"]
        pkey = _penalty_key(sc_id, rid)
        st.markdown(f"**Renewal {ri+1}**")
        c1, c2, c3, c4, c5 = st.columns([1.5,1.8,1.5,1.5,0.7])
        rn["mode"] = c1.radio("Mode", ["By Date","By Period"],
                               index=0 if rn.get("mode","By Date")=="By Date" else 1,
                               horizontal=True, key=f"rm_{sc_id}_{rid}")
        if rn["mode"] == "By Date":
            pd_v = c2.date_input("Effective date",
                                  date.fromisoformat(rn.get("date_str", str(b["start_date"]))),
                                  key=f"rd_{sc_id}_{rid}")
            rn["date_str"] = str(pd_v)
            rn["period"] = date_to_period(pd_v, b["start_date"], b["n_py"])
            c2.caption(f"≈ Period {rn['period']}")
        else:
            mx = int(b["amort_years"]*b["n_py"])
            rn["period"] = int(c2.number_input("Period #", 1, mx,
                               int(rn.get("period", orig_term_end_p+1)), key=f"rp_{sc_id}_{rid}"))
            c2.caption(f"≈ {period_to_date(rn['period'],b['start_date'],b['n_py']).strftime('%b %Y')}")
        rn["mtype"] = c3.selectbox("Type",["Fixed","Variable"],
                                    index=0 if rn.get("mtype","Fixed")=="Fixed" else 1,
                                    key=f"rmt_{sc_id}_{rid}")
        rn["new_rate"] = float(c4.number_input("Rate (%)",0.5,20.0,
                                float(rn.get("new_rate",b["annual_rate"])),0.01,
                                format="%.2f",key=f"rrt_{sc_id}_{rid}"))
        if c5.button("🗑️", key=f"delren_{sc_id}_{rid}"): ren_del.append(ri)
        rn["term_years"] = st.selectbox(f"Term — Renewal {ri+1}", term_opts,
                                         index=term_opts.index(rn.get("term_years",3)) if rn.get("term_years",3) in term_opts else 3,
                                         key=f"rty_{sc_id}_{rid}")
        rns_d = period_to_date(rn["period"],b["start_date"],b["n_py"])
        rne_d = rns_d+relativedelta(years=int(rn["term_years"]),months=int((float(rn["term_years"])%1)*12))
        st.caption(f"📅 {rns_d.strftime('%b %d, %Y')} → {rne_d.strftime('%b %d, %Y')}")

        # Early renewal penalty
        is_early = rn["period"] < prev_term_end_p
        if is_early:
            months_left = max(int((prev_term_end_p-rn["period"])/b["n_py"]*12),1)
            rf = df_base[df_base["Period"]<=rn["period"]]
            bal_ren = float(rf["Balance"].iloc[-1]) if not rf.empty else b["principal"]
            rate_ren = float(rf["Rate (%)"].iloc[-1]) if not rf.empty else b["annual_rate"]
            adv = calc_break_penalty(bal_ren, rate_ren, rn["mtype"],
                                     rn.get("orig_posted", rate_ren+1.5),
                                     rn.get("curr_posted", max(rate_ren-0.5,0.5)),
                                     months_left)
            st.markdown(f'<div class="warn">⚡ <b>Early Renewal</b> — {months_left} mo remain · '
                        f'Balance: <b>${bal_ren:,.0f}</b></div>', unsafe_allow_html=True)
            bp1, bp2 = st.columns(2)
            rn["orig_posted"] = float(bp1.number_input("Orig posted rate (%)", 0.5,20.0,
                                      float(rn.get("orig_posted",rate_ren+1.5)),0.01,
                                      format="%.2f",key=f"op_{sc_id}_{rid}",
                                      help="Bank's posted rate when you originally signed"))
            rn["curr_posted"] = float(bp2.number_input("Curr posted rate (%)",0.5,20.0,
                                      float(rn.get("curr_posted",max(rate_ren-0.5,0.5))),0.01,
                                      format="%.2f",key=f"cp_{sc_id}_{rid}",
                                      help="Current posted rate for remaining term"))
            adv = calc_break_penalty(bal_ren,rate_ren,rn["mtype"],rn["orig_posted"],rn["curr_posted"],months_left)

            # FIX #5: advisory values + always-visible penalty textbox
            pa1,pa2,pa3 = st.columns(3)
            pa1.metric("3-Month Interest",f"${adv['3_months_interest']:,.0f}",
                       help="3 months of interest on outstanding balance")
            if adv["ird"] is not None:
                pa2.metric("IRD",f"${adv['ird']:,.0f}",help="Interest Rate Differential")
            pa3.metric("Auto Max",f"${adv['calc_penalty']:,.0f}",help="Max of 3-mo and IRD")

            # FIX #4+5: always visible text box; initialise from session_state, not rn dict
            if pkey not in st.session_state:
                st.session_state[pkey] = str(int(adv["calc_penalty"]))
            pen_str = st.text_input(
                f"Penalty to apply ($) — advisory max ${adv['calc_penalty']:,.0f}",
                value=st.session_state[pkey],
                key=f"pen_txt_{sc_id}_{rid}",
                help="Edit to set the actual penalty charged by your bank")
            # Persist to session state and rn
            st.session_state[pkey] = pen_str
            try:
                rn["actual_penalty"] = float(pen_str.replace(",","").replace("$",""))
            except Exception:
                rn["actual_penalty"] = adv["calc_penalty"]

            rn["misc_fees"] = float(st.number_input("Misc fees ($)",0,50_000,
                                    int(rn.get("misc_fees",500)),50,key=f"mf_{sc_id}_{rid}",
                                    help="Admin, appraisal, legal fees"))
            total_exit = rn["actual_penalty"]+rn["misc_fees"]
            old_p = calc_pmt(bal_ren,rate_ren,12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
            new_p = calc_pmt(bal_ren,rn["new_rate"],12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
            st.markdown(f'<div class="pen">💸 Total exit: <b>${total_exit:,.0f}</b>'
                        f'{"  ·  Break-even: <b>{:.0f} months</b>".format(total_exit/abs(old_p-new_p)) if abs(old_p-new_p)>1 else ""}'
                        f'</div>', unsafe_allow_html=True)
        else:
            rn["misc_fees"] = float(st.number_input("Misc fees ($)",0,50_000,
                                    int(rn.get("misc_fees",250)),50,key=f"mf2_{sc_id}_{rid}"))
            rn["actual_penalty"] = 0

        prev_term_end_p = int(rn["period"])+int(float(rn["term_years"])*b["n_py"])
        st.markdown("---")

    for ri in sorted(ren_del,reverse=True): sc["renewals"].pop(ri)
    if ren_del: st.rerun()

    # ── Prepayment settings (embedded in scenario) ────────────────
    st.markdown("##### 💰 Prepayment Settings")
    if "pp" not in sc or sc["pp"] is None: sc["pp"] = _default_pp()
    pp = sc["pp"]
    pp1,pp2,pp3 = st.columns(3)
    pp["annual_lump"] = float(pp1.number_input("Annual lump-sum ($)",0,500_000,
                              int(pp.get("annual_lump",0)),500,key=f"pp_al_{sc_id}",
                              help="Additional principal each year"))
    if pp["annual_lump"]>0:
        lm_idx = max(0, int(pp.get("lump_month",1))-1)
        pp["lump_month"] = LUMP_MM[pp2.selectbox("Month",LUMP_MONTHS,index=lm_idx,key=f"pp_lm_{sc_id}")]
        pp3c,pp4c = st.columns(2)
        pp["lump_start_year"] = int(pp3c.number_input("Start year",1,30,int(pp.get("lump_start_year",1)),key=f"pp_ls_{sc_id}"))
        pp["lump_num_years"] = int(pp4c.number_input("For N years",0,30,int(pp.get("lump_num_years",0)),key=f"pp_ln_{sc_id}"))
    inc_opts = ["None","Fixed $","% increase"]
    inc_cur = pp.get("pay_increase_type","None")
    if inc_cur == "Fixed": inc_cur = "Fixed $"
    if inc_cur not in inc_opts: inc_cur = "None"
    inc_t = pp1.radio("Payment increase",inc_opts,index=inc_opts.index(inc_cur),horizontal=True,key=f"pp_it_{sc_id}")
    pp["pay_increase_type"] = "Fixed" if inc_t=="Fixed $" else inc_t
    if inc_t=="Fixed $":
        pp["pay_increase_val"]=float(pp2.number_input("Extra $/payment",0,10_000,int(pp.get("pay_increase_val",0)),50,key=f"pp_if_{sc_id}"))
    elif inc_t=="% increase":
        pp["pay_increase_val"]=float(pp2.slider("% increase",1,100,int(pp.get("pay_increase_val",10)),key=f"pp_ip_{sc_id}"))
    else:
        pp["pay_increase_val"]=0
    pp3.markdown("**One-time lump ($)**")
    ot_mode = pp3.radio("Mode",["By Date","By Period"],horizontal=True,key=f"pp_om_{sc_id}")
    if ot_mode=="By Date":
        ot_d=pp3.date_input("Date",b["start_date"]+relativedelta(years=1),
                            min_value=b["start_date"],key=f"pp_od_{sc_id}")
        pp["onetime_period"]=date_to_period(ot_d,b["start_date"],b["n_py"])
    else:
        pp["onetime_period"]=int(pp3.number_input("Period #",0,int(b["amort_years"]*b["n_py"]),
                                                   int(pp.get("onetime_period",0)),key=f"pp_op_{sc_id}"))
    pp["onetime_amount"]=float(pp3.number_input("Amount ($)",0,2_000_000,int(pp.get("onetime_amount",0)),1_000,key=f"pp_oa_{sc_id}"))

    # ── Build and display scenario ────────────────────────────────
    main_rc=[{"period":rn["period"],"new_rate":rn["new_rate"]} for rn in sc["renewals"]]
    all_rcs=(b.get("past_renewal_rcs") or [])+main_rc
    sc_extra=_make_extra_payments(pp,b["n_py"],b["principal"],b.get("past_extra"))
    # For pct increase: replace placeholder with actual calc_pmt
    if pp.get("pay_increase_type")=="Pct" and pp.get("pay_increase_val",0)>0:
        base_pmt=calc_pmt(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],b["accel"])
        sc_extra=[e for e in sc_extra if e.get("period") not in range(1,int(b["amort_years"]*b["n_py"])+1)] + \
                 [{"period":p,"amount":base_pmt*pp["pay_increase_val"]/100}
                  for p in range(1,int(b["amort_years"]*b["n_py"])+1)]

    df_sc,s_sc=build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
                                   accel=b["accel"],start_date=b["start_date"],
                                   extra_payments=sc_extra or None,rate_changes=all_rcs or None)
    last_rate=sc["renewals"][-1]["new_rate"] if sc["renewals"] else b["annual_rate"]
    if sc["renewals"]:
        sc_term_end_p=int(sc["renewals"][-1]["period"])+int(float(sc["renewals"][-1]["term_years"])*b["n_py"])
    else:
        sc_term_end_p=orig_term_end_p

    # FIX #2: calc_monthly_sc = payment to maintain EXACTLY base_remaining_yrs at scenario rate
    calc_monthly_sc=calc_pmt(today_bal,last_rate,b["n_py"],base_remaining_yrs,b["accel"]) if base_remaining_yrs>0 else 0

    # Payment input
    st.markdown("##### 💳 Payment Adjustment")
    min_pmt=float(max(today_bal*periodic_rate(last_rate,b["n_py"])+1,100))
    pay1,pay2,pay3=st.columns([2,2,2])
    user_pmt=pay1.number_input(
        "Monthly payment ($)",
        min_value=min_pmt,max_value=float(today_bal),
        value=round(calc_monthly_sc,2),step=50.0,format="%.2f",
        key=f"user_pmt_{sc_id}",
        help=f"Required to keep current amortization at {last_rate}%: ${calc_monthly_sc:,.2f}")
    pay1.caption(f"Required to keep current amortization: **${calc_monthly_sc:,.2f}**")

    # FIX #2: adj_rem from today with explicit equality guard
    if abs(user_pmt-calc_monthly_sc)<0.02:
        adj_rem=base_remaining_yrs           # exact equality guard
    else:
        adj_rem=calc_remaining_years(today_bal,last_rate,b["n_py"],user_pmt) if user_pmt>0 else base_remaining_yrs
    adj_end=date.today()+relativedelta(years=int(adj_rem),months=int((adj_rem%1)*12))
    delta_yrs=round(adj_rem-base_remaining_yrs,1)
    colour="mc-g" if adj_rem<=base_remaining_yrs else "mc-r"

    # Metrics row 1: static
    st.markdown("**Current baseline:**")
    m1,m2,m3=st.columns(3)
    m1.metric("Base Interest",f"${s_base.get('total_interest',0):,.0f}",help="Total interest with no changes")
    m2.metric("Current Remaining",f"{base_remaining_yrs:.1f} yrs",help="Remaining amortization from today at current rate")
    m3.metric("Required Payment",f"${calc_monthly_sc:,.2f}",help=f"Payment to maintain {base_remaining_yrs:.1f} yr amortization at {last_rate}%")

    # Metrics row 2: adjusted (green boxes)
    st.markdown("**Scenario impact:**")
    a1,a2,a3=st.columns(3)
    sc_int=s_sc.get("total_interest",0)
    sc_int_d=sc_int-s_base.get("total_interest",0)
    a1.markdown(f'<div class="mc {colour}"><h3>Adjusted Interest</h3>'
                f'<p>${sc_int:,.0f} <span style="font-size:.8rem">({sc_int_d:+,.0f})</span></p></div>',
                unsafe_allow_html=True)
    a2.markdown(f'<div class="mc {colour}"><h3>Adjusted Remaining</h3>'
                f'<p>{adj_rem:.1f} yrs <span style="font-size:.8rem">'
                f'({"same" if abs(delta_yrs)<0.05 else f"{delta_yrs:+.1f} yrs"})</span></p></div>',
                unsafe_allow_html=True)
    a3.markdown(f'<div class="mc {colour}"><h3>Mortgage-free by</h3>'
                f'<p>{adj_end.strftime("%b %Y")}</p></div>',unsafe_allow_html=True)

    # Charts
    if not df_sc.empty:
        fig_bar=stacked_bar_pi(df_sc,today_p,sc_term_end_p,f"{sc['name']} — P & I")
        st.plotly_chart(fig_bar,use_container_width=True,key=f"ch_sc_{sc_id}")
        fig_r=go.Figure()
        fig_r.add_scatter(x=df_sc["Date"],y=df_sc["Rate (%)"],fill="tozeroy",
                          name="Rate",line=dict(color="#27ae60"))
        fig_r.update_layout(title="Rate over time",xaxis_title="Date",yaxis_title="%",
                             height=180,margin=dict(t=30,b=20))
        st.plotly_chart(fig_r,use_container_width=True,key=f"ch_rt_{sc_id}")

    # Save
    sn=st.text_input("Save as",sc["name"],key=f"sc_sn_{sc_id}")
    sc_c1,sc_c2=st.columns(2)
    if sc_c1.button("💾 Save",key=f"sv_{sc_id}"):
        if not sn.strip():
            sc_c2.error("❌ Name cannot be empty.")
        else:
            db_id=db_save_scenario(conn, sc.get("db_id"), sn.strip(), sc.get("desc",""),
                                   sc["renewals"], pp)
            if db_id:
                sc["db_id"]=db_id; sc["name"]=sn.strip()
                sc_c2.success(f"✅ Saved: {sn}")
            else:
                sc_c2.error("❌ Save failed — check DB connection.")
