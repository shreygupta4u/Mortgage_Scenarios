"""pages/scenario_editor.py — Shared dialogs + compute helpers."""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
from dateutil.relativedelta import relativedelta
import uuid

from modules import (
    FREQ, periodic_rate, calc_pmt, date_to_period, period_to_date,
    calc_remaining_years, build_amortization, calc_break_penalty,
    db_save_scenario, db_save_prepay_scenario,
    db_load_prepay_scenarios,
)

TERM_OPTS   = [0.5, 1, 2, 3, 4, 5, 7, 10]
LUMP_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
LUMP_MM     = {m: i+1 for i, m in enumerate(LUMP_MONTHS)}


# ── Compute helpers ───────────────────────────────────────────────

def apply_prepay_settings(settings, b, base_extras=None):
    result = list(base_extras or [])
    al, lm = float(settings.get("annual_lump",0)), int(settings.get("lump_month",1))
    sy, ny = int(settings.get("lump_start_year",1)), int(settings.get("lump_num_years",0))
    if al > 0:
        for yr in range(sy, sy + ny):
            p = max(1, int((yr-1)*b["n_py"] + lm*b["n_py"]/12))
            result.append({"period": p, "amount": al})
    inc_t = settings.get("pay_increase_type","None")
    inc_v = float(settings.get("pay_increase_val",0))
    total_p = int(b["amort_years"] * b["n_py"])
    if inc_t == "Fixed" and inc_v > 0:
        for p in range(1, total_p+1): result.append({"period": p, "amount": inc_v})
    elif inc_t in ("Pct","% increase") and inc_v > 0:
        base_pmt = calc_pmt(b["principal"], b["annual_rate"], b["n_py"], b["amort_years"], b["accel"])
        for p in range(1, total_p+1): result.append({"period": p, "amount": base_pmt*inc_v/100})
    otp, ota = int(settings.get("onetime_period",0)), float(settings.get("onetime_amount",0))
    if otp > 0 and ota > 0: result.append({"period": otp, "amount": ota})
    return result


def _get_linked_pp(sc, pps_by_dbid):
    """Return the prepayment scenario dict linked to this rate scenario, or None."""
    lppid = int(sc.get("linked_pp_db_id", 0))
    return pps_by_dbid.get(lppid) if lppid else None


def compute_scenario(sc, b, prepay_sc=None):
    main_rc  = [{"period": rn["period"], "new_rate": rn["new_rate"]} for rn in sc.get("renewals",[])]
    all_rcs  = (b.get("past_renewal_rcs") or []) + main_rc
    sc_extra = list(b.get("past_extra") or [])
    for rn in sc.get("renewals",[]):
        amt = float(rn.get("onetime_amount", 0))
        if amt > 0: sc_extra.append({"period": int(rn["period"]), "amount": amt})
    if prepay_sc:
        sc_extra = apply_prepay_settings(prepay_sc.get("settings",{}), b, sc_extra)
    df_sc, s_sc = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=sc_extra or None, rate_changes=all_rcs or None,
    )
    last_rate     = sc["renewals"][-1]["new_rate"] if sc.get("renewals") else b["annual_rate"]
    sc_term_end_p = (int(sc["renewals"][-1]["period"]) + int(float(sc["renewals"][-1]["term_years"])*b["n_py"])
                     if sc.get("renewals") else b["orig_term_end_p"])
    return df_sc, s_sc, all_rcs, sc_extra, last_rate, sc_term_end_p


def compute_adj_scenario(sc, b, prepay_sc, _df_base_unused, s_base):
    df_sc, s_sc, all_rcs, sc_extra, last_rate, sc_term_end_p = compute_scenario(sc, b, prepay_sc)
    today_bal          = b["today_m"].get("balance_today", b["principal"])
    base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
    today_p            = b["today_m"].get("period_today", 0)
    calc_monthly_sc    = (calc_pmt(today_bal, last_rate, b["n_py"], base_remaining_yrs, b["accel"])
                          if base_remaining_yrs > 0 else 0)
    user_pmt = float(sc.get("user_pmt", 0))
    eff_pmt  = user_pmt if user_pmt > 0 else calc_monthly_sc
    if abs(eff_pmt - calc_monthly_sc) > 0.02:
        extra_pp  = max(0.0, eff_pmt - calc_monthly_sc)
        adj_extra = list(sc_extra) + [
            {"period": p, "amount": extra_pp}
            for p in range(max(today_p,1), int(b["amort_years"]*b["n_py"])+1)
        ]
        _, s_adj = build_amortization(
            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=adj_extra or None, rate_changes=all_rcs or None,
        )
        adj_rem = calc_remaining_years(today_bal, last_rate, b["n_py"], eff_pmt) if eff_pmt > 0 else base_remaining_yrs
    else:
        s_adj   = s_sc
        adj_rem = base_remaining_yrs
    adj_end   = date.today() + relativedelta(years=int(adj_rem), months=int((adj_rem%1)*12))
    delta_yrs = round(adj_rem - base_remaining_yrs, 1)
    colour    = "mc-g" if adj_rem <= base_remaining_yrs else "mc-r"
    return df_sc, s_sc, s_adj, adj_rem, adj_end, delta_yrs, colour, calc_monthly_sc, sc_term_end_p, last_rate


# ── Rate scenario edit dialog ─────────────────────────────────────

@st.dialog("✏️ Edit Rate Scenario", width="large")
def edit_scenario_dialog():
    sc_id = st.session_state.get("_editing_sc_id")
    rcs   = st.session_state.get("rc_scenarios", {})
    sc    = rcs.get(sc_id)
    b     = st.session_state.get("base")
    conn  = st.session_state.get("db_conn")
    if sc is None or b is None:
        st.error("Cannot open editor — missing data.")
        if st.button("Close"): st.session_state["_editing_sc_id"] = None; st.rerun()
        return

    d1, d2 = st.columns([2,3])
    sc["name"] = d1.text_input("Scenario Name", sc["name"], key=f"dlg_name_{sc_id}",
                                help="Used as the save key — must be unique")
    sc["desc"] = d2.text_area("Description", sc.get("desc",""), height=68,
                               key=f"dlg_desc_{sc_id}", placeholder="Describe this scenario…")

    # ── REQ #1: Per-scenario prepayment selector ──────────────────
    st.divider()
    pp_list_db = db_load_prepay_scenarios(conn)
    pp_opts    = ["None (no prepayment)"] + [s["name"] for s in pp_list_db]
    cur_lppid  = int(sc.get("linked_pp_db_id", 0))
    cur_pp_nm  = next((s["name"] for s in pp_list_db if s["db_id"] == cur_lppid), "None (no prepayment)")
    pp_idx     = pp_opts.index(cur_pp_nm) if cur_pp_nm in pp_opts else 0
    sel_pp_nm  = st.selectbox("🔗 Linked Prepayment Scenario", pp_opts, index=pp_idx,
                               key=f"dlg_pp_{sc_id}",
                               help="Choose a prepayment plan to layer on top of this rate scenario")
    sel_pp_rec = next((s for s in pp_list_db if s["name"] == sel_pp_nm), None)
    sc["linked_pp_db_id"] = sel_pp_rec["db_id"] if sel_pp_rec else 0

    # ── Monthly payment ───────────────────────────────────────────
    st.divider()
    today_bal          = b["today_m"].get("balance_today", b["principal"])
    base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
    last_rate = sc["renewals"][-1]["new_rate"] if sc.get("renewals") else b["annual_rate"]
    calc_monthly_sc = (calc_pmt(today_bal, last_rate, b["n_py"], base_remaining_yrs, b["accel"])
                       if base_remaining_yrs > 0 else 0)
    min_pmt   = float(max(today_bal * periodic_rate(last_rate, b["n_py"]) + 1, 100))
    saved_pmt = float(sc.get("user_pmt", 0))
    init_pmt  = round(saved_pmt if saved_pmt > 0 else calc_monthly_sc, 2)

    st.markdown("##### 💳 Monthly Payment")
    pc1, pc2 = st.columns([2,3])
    user_pmt = pc1.number_input("Amount ($)", min_value=min_pmt, max_value=float(today_bal),
                                 value=max(init_pmt, min_pmt), step=50.0, format="%.2f",
                                 key=f"dlg_pmt_{sc_id}",
                                 help=f"Required to maintain current amort at {last_rate:.2f}%: ${calc_monthly_sc:,.2f}")
    sc["user_pmt"] = user_pmt
    pc2.info(f"Required (baseline): **${calc_monthly_sc:,.2f}**  \n"
             f"Δ vs required: **${user_pmt - calc_monthly_sc:+,.2f}/period**")

    # ── Renewals ──────────────────────────────────────────────────
    st.divider()
    st.markdown("##### 🔄 Rate Renewals")
    df_base, _ = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None,
    )
    orig_term_end_p = b["orig_term_end_p"]
    if st.button("➕ Add Renewal", key=f"dlg_add_ren_{sc_id}"):
        dd = str(b["start_date"] + relativedelta(years=int(b["term_years"]),
                                                  months=int((b["term_years"]%1)*12)))
        sc["renewals"].append({
            "id": str(uuid.uuid4())[:8], "mode": "By Date", "date_str": dd,
            "period": date_to_period(dd, b["start_date"], b["n_py"]),
            "new_rate": b["annual_rate"], "mtype": "Fixed", "term_years": 3,
            "actual_penalty": 0, "misc_fees": 250,
            "orig_posted": b["annual_rate"]+1.5, "curr_posted": max(b["annual_rate"]-0.5,0.5),
            "onetime_amount": 0, "variable_subs": {},
        })

    prev_term_end_p = orig_term_end_p
    ren_del = []
    for ri, rn in enumerate(sc.get("renewals",[])):
        rid  = rn["id"]
        with st.container(border=True):
            c1,c2,c3,c4,c5,c6 = st.columns([1.4,1.8,1.4,1.4,1.4,0.6])
            rn["mode"] = c1.radio("Mode",["By Date","By Period"],
                                   index=0 if rn.get("mode","By Date")=="By Date" else 1,
                                   horizontal=True, key=f"dlg_rm_{sc_id}_{rid}",
                                   help="Set renewal by calendar date or payment period number")
            if rn["mode"] == "By Date":
                pd_v = c2.date_input("Effective Date",
                                      date.fromisoformat(rn.get("date_str") or str(b["start_date"])),
                                      key=f"dlg_rd_{sc_id}_{rid}",
                                      help="Calendar date this renewal term begins")
                rn["date_str"] = str(pd_v)
                rn["period"]   = date_to_period(pd_v, b["start_date"], b["n_py"])
                c2.caption(f"≈ Period {rn['period']}")
            else:
                mx = int(b["amort_years"]*b["n_py"])
                rn["period"] = int(c2.number_input("Period #",1,mx,
                                   int(rn.get("period", orig_term_end_p+1)),
                                   key=f"dlg_rp_{sc_id}_{rid}",
                                   help="Payment period number when renewal starts"))
                c2.caption(f"≈ {period_to_date(rn['period'],b['start_date'],b['n_py']).strftime('%b %Y')}")
            rn["mtype"]    = c3.selectbox("Type",["Fixed","Variable"],
                                           index=0 if rn.get("mtype","Fixed")=="Fixed" else 1,
                                           key=f"dlg_rmt_{sc_id}_{rid}",
                                           help="Fixed locks rate for term; Variable floats with prime")
            rn["new_rate"] = float(c4.number_input("Rate (%)",0.5,20.0,
                                   float(rn.get("new_rate",b["annual_rate"])),
                                   0.01, format="%.2f", key=f"dlg_rrt_{sc_id}_{rid}",
                                   help="Annual interest rate for this renewal term (semi-annual compounding)"))
            rn["term_years"] = c5.selectbox("Term (yrs)", TERM_OPTS,
                                             index=TERM_OPTS.index(rn.get("term_years",3))
                                                   if rn.get("term_years",3) in TERM_OPTS else 3,
                                             key=f"dlg_rty_{sc_id}_{rid}",
                                             help="Length of this renewal term in years")
            if c6.button("🗑️", key=f"dlg_delren_{sc_id}_{rid}", help="Remove this renewal entry"):
                ren_del.append(ri)

            rns_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
            rne_d = rns_d + relativedelta(years=int(rn["term_years"]),
                                           months=int((float(rn["term_years"])%1)*12))
            l1, l2 = st.columns([2,3])
            rn["onetime_amount"] = float(l1.number_input(
                "💰 One-time lump at term start ($)", 0, 2_000_000,
                int(rn.get("onetime_amount",0)), 1_000, key=f"dlg_ota_{sc_id}_{rid}",
                help=f"Principal prepayment applied at period {rn['period']} ({rns_d.strftime('%b %Y')}) — start of this term"))
            l2.caption(f"📅 **{rns_d.strftime('%b %d, %Y')}** → **{rne_d.strftime('%b %d, %Y')}**")

            is_early = rn["period"] < prev_term_end_p
            if is_early:
                months_left = max(int((prev_term_end_p-rn["period"])/b["n_py"]*12),1)
                rf       = df_base[df_base["Period"]<=rn["period"]]
                bal_ren  = float(rf["Balance"].iloc[-1]) if not rf.empty else b["principal"]
                rate_ren = float(rf["Rate (%)"].iloc[-1]) if not rf.empty else b["annual_rate"]
                st.markdown(f'<div class="warn">⚡ <b>Early Renewal</b> — {months_left} mo remain · '
                            f'Balance: <b>${bal_ren:,.0f}</b></div>', unsafe_allow_html=True)
                bp1,bp2 = st.columns(2)
                rn["orig_posted"] = float(bp1.number_input("Orig posted rate (%)",0.5,20.0,
                                          float(rn.get("orig_posted",rate_ren+1.5)),0.01,format="%.2f",
                                          key=f"dlg_op_{sc_id}_{rid}",
                                          help="Bank's posted rate when you originally signed"))
                rn["curr_posted"] = float(bp2.number_input("Curr posted rate (%)",0.5,20.0,
                                          float(rn.get("curr_posted",max(rate_ren-0.5,0.5))),0.01,format="%.2f",
                                          key=f"dlg_cp_{sc_id}_{rid}",
                                          help="Current posted rate for the remaining term length"))
                adv = calc_break_penalty(bal_ren,rate_ren,rn["mtype"],rn["orig_posted"],rn["curr_posted"],months_left)
                pa1,pa2,pa3 = st.columns(3)
                pa1.metric("3-Month Interest",f"${adv['3_months_interest']:,.0f}",
                           help="3 months of interest on the outstanding balance at that point")
                if adv["ird"] is not None:
                    pa2.metric("IRD",f"${adv['ird']:,.0f}",
                               help="Interest Rate Differential — based on posted rate spread × balance × remaining months")
                pa3.metric("Auto Max",f"${adv['calc_penalty']:,.0f}",
                           help="Greater of 3-month interest and IRD — lenders take the higher amount")
                wkey = f"dlg_pen_txt_{sc_id}_{rid}"
                # Seed widget once from the saved renewal value (or advisory default on first create).
                # NO value= parameter — lets Streamlit own the state cleanly.
                if wkey not in st.session_state:
                    saved = rn.get("actual_penalty", adv["calc_penalty"])
                    st.session_state[wkey] = str(int(saved))
                st.text_input(f"Penalty to apply ($) — advisory: ${adv['calc_penalty']:,.0f}",
                               key=wkey,
                               help="Type the actual penalty your bank quoted and click 💾 Save to DB. "
                                    "Value persists across dialog opens once saved.")
                try:
                    rn["actual_penalty"] = float(st.session_state[wkey].replace(",","").replace("$",""))
                except Exception:
                    rn["actual_penalty"] = adv["calc_penalty"]
                rn["misc_fees"] = float(st.number_input("Misc fees ($)",0,50_000,
                                        int(rn.get("misc_fees",500)),50,key=f"dlg_mf_{sc_id}_{rid}",
                                        help="Admin, appraisal, and legal fees for early break"))
                total_exit = rn["actual_penalty"] + rn["misc_fees"]
                old_p = calc_pmt(bal_ren,rate_ren,12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
                new_p = calc_pmt(bal_ren,rn["new_rate"],12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
                be_str = (f"  ·  Break-even: <b>{total_exit/abs(old_p-new_p):.0f} months</b>"
                          if abs(old_p-new_p)>1 else "")
                st.markdown(f'<div class="pen">💸 Total exit: <b>${total_exit:,.0f}</b>{be_str}</div>',
                            unsafe_allow_html=True)
            else:
                rn["misc_fees"]      = float(st.number_input("Misc fees ($)",0,50_000,
                                             int(rn.get("misc_fees",250)),50,key=f"dlg_mf2_{sc_id}_{rid}",
                                             help="Admin and legal fees at normal renewal"))
                rn["actual_penalty"] = 0
            prev_term_end_p = int(rn["period"]) + int(float(rn["term_years"])*b["n_py"])

    for ri in sorted(ren_del, reverse=True): sc["renewals"].pop(ri)

    st.divider()
    ba1, ba2 = st.columns(2)
    if ba1.button("💾 Save to DB", key=f"dlg_save_{sc_id}",
                   use_container_width=True, type="primary",
                   help="Saves using the Scenario Name above as the identifier"):
        if not sc["name"].strip():
            st.error("❌ Scenario Name cannot be empty.")
        else:
            pp_settings = {"annual_lump":0,"lump_month":1,"lump_start_year":1,"lump_num_years":0,
                           "pay_increase_type":"None","pay_increase_val":0,"onetime_period":0,"onetime_amount":0}
            db_id = db_save_scenario(conn, sc.get("db_id"), sc["name"].strip(), sc.get("desc",""),
                                     sc["renewals"], pp_settings,
                                     sc.get("user_pmt",0), sc.get("linked_pp_db_id",0))
            if db_id:
                sc["db_id"] = db_id
                st.session_state["_editing_sc_id"] = None
                st.rerun()
            else:
                st.error("❌ Save failed — check DB connection.")
    if ba2.button("✕ Close without saving", key=f"dlg_close_{sc_id}", use_container_width=True):
        st.session_state["_editing_sc_id"] = None
        st.rerun()


# ── Prepayment scenario edit dialog ──────────────────────────────

@st.dialog("✏️ Edit Prepayment Scenario", width="large")
def edit_prepay_dialog():
    sc_id = st.session_state.get("_editing_pp_sc_id")
    pps   = st.session_state.get("pp_scenarios", {})
    sc    = pps.get(sc_id)
    b     = st.session_state.get("base")
    conn  = st.session_state.get("db_conn")
    if sc is None or b is None:
        st.error("Missing data.")
        if st.button("Close"): st.session_state["_editing_pp_sc_id"]=None; st.rerun()
        return

    d1,d2 = st.columns([2,3])
    sc["name"] = d1.text_input("Scenario Name", sc["name"], key=f"pp_dlg_name_{sc_id}",
                                help="Name to identify and save this prepayment strategy")
    sc["desc"] = d2.text_area("Description", sc.get("desc",""), height=68,
                               key=f"pp_dlg_desc_{sc_id}", placeholder="Describe this prepayment plan…")
    st.divider()
    s = sc["settings"]

    st.markdown("##### 📅 Annual Lump-Sum")
    al1,al2 = st.columns(2)
    s["annual_lump"] = float(al1.number_input("Amount ($)",0,500_000,int(s.get("annual_lump",0)),500,
                              key=f"pp_dlg_al_{sc_id}",
                              help="Amount of extra principal to prepay each year"))
    if s["annual_lump"] > 0:
        lm_idx = max(0, int(s.get("lump_month",1))-1)
        s["lump_month"] = LUMP_MM[al2.selectbox("Month each year",LUMP_MONTHS,index=lm_idx,
                                                  key=f"pp_dlg_lm_{sc_id}",
                                                  help="Month of the year to apply the annual lump sum")]
        lc1,lc2 = st.columns(2)
        s["lump_start_year"] = int(lc1.number_input("Start year #",1,30,int(s.get("lump_start_year",1)),
                                                      key=f"pp_dlg_ls_{sc_id}",
                                                      help="Mortgage year (from start) when annual lumps begin"))
        s["lump_num_years"]  = int(lc2.number_input("For N years",0,30,int(s.get("lump_num_years",0)),
                                                      key=f"pp_dlg_ln_{sc_id}",
                                                      help="Number of consecutive years to apply the lump sum"))

    st.markdown("##### 💳 Payment Increase")
    inc_opts = ["None","Fixed $","% increase"]
    inc_cur  = s.get("pay_increase_type","None")
    if inc_cur == "Fixed": inc_cur = "Fixed $"
    if inc_cur not in inc_opts: inc_cur = "None"
    inc_t = st.radio("Type",inc_opts,index=inc_opts.index(inc_cur),horizontal=True,
                      key=f"pp_dlg_it_{sc_id}",
                      help="Choose how to increase your regular payment above the minimum required")
    s["pay_increase_type"] = "Fixed" if inc_t=="Fixed $" else inc_t
    if inc_t=="Fixed $":
        s["pay_increase_val"] = float(st.number_input("Extra per payment ($)",0,10_000,
                                      int(s.get("pay_increase_val",0)),50,key=f"pp_dlg_if_{sc_id}",
                                      help="Fixed dollar amount added to each regular payment"))
    elif inc_t=="% increase":
        s["pay_increase_val"] = float(st.slider("% increase",1,100,
                                      int(s.get("pay_increase_val",10)),key=f"pp_dlg_ip_{sc_id}",
                                      help="Percentage of required payment to add on top"))
    else:
        s["pay_increase_val"] = 0

    st.markdown("##### 🔁 One-Time Lump Sum")
    ot1,ot2 = st.columns(2)
    s["onetime_period"] = int(ot1.number_input("Period #",0,int(b["amort_years"]*b["n_py"]),
                              int(s.get("onetime_period",0)),key=f"pp_dlg_op_{sc_id}",
                              help="Payment period number for the one-time lump (0 = disabled)"))
    s["onetime_amount"] = float(ot2.number_input("Amount ($)",0,2_000_000,
                                int(s.get("onetime_amount",0)),1_000,key=f"pp_dlg_oa_{sc_id}",
                                help="One-time principal prepayment amount"))
    if s["onetime_period"] > 0:
        d_ot = period_to_date(s["onetime_period"], b["start_date"], b["n_py"])
        st.caption(f"Applies at period {s['onetime_period']} ≈ **{d_ot.strftime('%b %Y')}**")

    st.divider()
    ba1,ba2 = st.columns(2)
    if ba1.button("💾 Save to DB",key=f"pp_dlg_save_{sc_id}",use_container_width=True,type="primary",
                   help="Save this prepayment strategy to the database"):
        if not sc["name"].strip():
            st.error("❌ Name cannot be empty.")
        else:
            db_id = db_save_prepay_scenario(conn, sc.get("db_id"), sc["name"].strip(), sc.get("desc",""), s)
            if db_id:
                sc["db_id"] = db_id
                st.session_state["_editing_pp_sc_id"] = None
                st.rerun()
            else:
                st.error("❌ Save failed.")
    if ba2.button("✕ Close",key=f"pp_dlg_close_{sc_id}",use_container_width=True):
        st.session_state["_editing_pp_sc_id"]=None; st.rerun()
