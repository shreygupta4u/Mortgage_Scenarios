"""pages/scenario_editor.py — Shared dialogs + compute helpers.
Changes vs previous:
  #1  Variable sub-scenarios re-added (a,b,c... with date + rate each)
  #2  Auto terminal "Rest of Amortization" entry when Add Renewal clicked
  #3  misc_fees widget uses seed-once pattern (same fix as penalty)
"""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
from dateutil.relativedelta import relativedelta
import uuid

from modules import (
    FREQ, periodic_rate, calc_pmt, date_to_period, period_to_date,
    calc_remaining_years, build_amortization, calc_break_penalty,
    db_save_scenario, db_save_prepay_scenario, db_load_prepay_scenarios,
)

TERM_OPTS   = [0.5, 1, 2, 3, 4, 5, 7, 10]
LUMP_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
LUMP_MM     = {m: i+1 for i, m in enumerate(LUMP_MONTHS)}
SUB_LETTERS = "abcdefghijklmnopqrstuvwxyz"


# ── Compute helpers ───────────────────────────────────────────────

def apply_prepay_settings(settings, b, base_extras=None):
    result = list(base_extras or [])
    al,lm = float(settings.get("annual_lump",0)),int(settings.get("lump_month",1))
    sy,ny = int(settings.get("lump_start_year",1)),int(settings.get("lump_num_years",0))
    if al > 0:
        for yr in range(sy, sy+ny):
            p = max(1, int((yr-1)*b["n_py"] + lm*b["n_py"]/12))
            result.append({"period":p,"amount":al})
    inc_t = settings.get("pay_increase_type","None")
    inc_v = float(settings.get("pay_increase_val",0))
    total_p = int(b["amort_years"]*b["n_py"])
    if inc_t == "Fixed" and inc_v > 0:
        for p in range(1,total_p+1): result.append({"period":p,"amount":inc_v})
    elif inc_t in ("Pct","% increase") and inc_v > 0:
        base_pmt = calc_pmt(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],b["accel"])
        for p in range(1,total_p+1): result.append({"period":p,"amount":base_pmt*inc_v/100})
    otp,ota = int(settings.get("onetime_period",0)),float(settings.get("onetime_amount",0))
    if otp > 0 and ota > 0: result.append({"period":otp,"amount":ota})
    return result


def _get_linked_pp(sc, pps_by_dbid):
    lppid = int(sc.get("linked_pp_db_id",0))
    return pps_by_dbid.get(lppid) if lppid else None


def compute_scenario(sc, b, prepay_sc=None):
    main_rc  = []
    for rn in sc.get("renewals",[]):
        main_rc.append({"period":rn["period"],"new_rate":rn["new_rate"]})
        # Variable sub-scenarios inject additional rate changes within the term
        if rn.get("mtype") == "Variable":
            for sub in rn.get("variable_subs",[]):
                if sub.get("date_str") and sub.get("rate"):
                    sub_p = date_to_period(sub["date_str"], b["start_date"], b["n_py"])
                    if sub_p > rn["period"]:
                        main_rc.append({"period":sub_p,"new_rate":float(sub["rate"])})
    all_rcs = (b.get("past_renewal_rcs") or []) + main_rc
    sc_extra = list(b.get("past_extra") or [])
    for rn in sc.get("renewals",[]):
        if not rn.get("is_terminal"):
            amt = float(rn.get("onetime_amount",0))
            if amt > 0: sc_extra.append({"period":int(rn["period"]),"amount":amt})
    if prepay_sc:
        sc_extra = apply_prepay_settings(prepay_sc.get("settings",{}),b,sc_extra)
    df_sc,s_sc = build_amortization(
        b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
        accel=b["accel"],start_date=b["start_date"],
        extra_payments=sc_extra or None,rate_changes=all_rcs or None,
    )
    non_terminal = [rn for rn in sc.get("renewals",[]) if not rn.get("is_terminal")]
    last_rate    = non_terminal[-1]["new_rate"] if non_terminal else b["annual_rate"]
    sc_term_end_p= (int(non_terminal[-1]["period"])+int(float(non_terminal[-1]["term_years"])*b["n_py"])
                    if non_terminal else b["orig_term_end_p"])
    return df_sc,s_sc,all_rcs,sc_extra,last_rate,sc_term_end_p


def compute_adj_scenario(sc, b, prepay_sc, _unused, s_base):
    df_sc,s_sc,all_rcs,sc_extra,last_rate,sc_term_end_p = compute_scenario(sc,b,prepay_sc)
    today_bal          = b["today_m"].get("balance_today",b["principal"])
    base_remaining_yrs = b["today_m"].get("remaining_years",b["amort_years"])
    today_p            = b["today_m"].get("period_today",0)
    calc_monthly_sc    = (calc_pmt(today_bal,last_rate,b["n_py"],base_remaining_yrs,b["accel"])
                          if base_remaining_yrs > 0 else 0)
    user_pmt = float(sc.get("user_pmt",0))
    eff_pmt  = user_pmt if user_pmt > 0 else calc_monthly_sc
    if abs(eff_pmt-calc_monthly_sc) > 0.02:
        extra_pp  = max(0.0,eff_pmt-calc_monthly_sc)
        adj_extra = list(sc_extra)+[{"period":p,"amount":extra_pp}
                                     for p in range(max(today_p,1),int(b["amort_years"]*b["n_py"])+1)]
        _,s_adj = build_amortization(
            b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
            accel=b["accel"],start_date=b["start_date"],
            extra_payments=adj_extra or None,rate_changes=all_rcs or None,
        )
        adj_rem = calc_remaining_years(today_bal,last_rate,b["n_py"],eff_pmt) if eff_pmt > 0 else base_remaining_yrs
    else:
        s_adj=s_sc; adj_rem=base_remaining_yrs
    adj_end   = date.today()+relativedelta(years=int(adj_rem),months=int((adj_rem%1)*12))
    delta_yrs = round(adj_rem-base_remaining_yrs,1)
    colour    = "mc-g" if adj_rem<=base_remaining_yrs else "mc-r"
    return df_sc,s_sc,s_adj,adj_rem,adj_end,delta_yrs,colour,calc_monthly_sc,sc_term_end_p,last_rate


# ── Widget-state seed helpers (no-override pattern) ───────────────

def _seed_number(wkey, rn_val, default=0):
    """Seed session_state number widget once; ignored if key already exists."""
    if wkey not in st.session_state:
        st.session_state[wkey] = int(rn_val if rn_val is not None else default)

def _seed_text(wkey, rn_val, default="0"):
    """Seed session_state text widget once."""
    if wkey not in st.session_state:
        st.session_state[wkey] = str(int(float(rn_val or default)))


# ── Rate scenario edit dialog ─────────────────────────────────────

@st.dialog("✏️ Edit Rate Scenario", width="large")
def edit_scenario_dialog():
    sc_id = st.session_state.get("_editing_sc_id")
    rcs   = st.session_state.get("rc_scenarios",{})
    sc    = rcs.get(sc_id)
    b     = st.session_state.get("base")
    conn  = st.session_state.get("db_conn")
    if sc is None or b is None:
        st.error("Cannot open editor — missing data.")
        if st.button("Close"): st.session_state["_editing_sc_id"]=None; st.rerun()
        return

    d1,d2 = st.columns([2,3])
    sc["name"] = d1.text_input("Scenario Name",sc["name"],key=f"dlg_name_{sc_id}",
                                help="Used as the save key — must be unique")
    sc["desc"] = d2.text_area("Description",sc.get("desc",""),height=68,key=f"dlg_desc_{sc_id}",
                               placeholder="Describe this scenario…")

    # Per-scenario prepayment link
    st.divider()
    pp_list_db = db_load_prepay_scenarios(conn)
    pp_opts    = ["None (no prepayment)"]+[s["name"] for s in pp_list_db]
    cur_lppid  = int(sc.get("linked_pp_db_id",0))
    cur_pp_nm  = next((s["name"] for s in pp_list_db if s["db_id"]==cur_lppid),"None (no prepayment)")
    pp_idx     = pp_opts.index(cur_pp_nm) if cur_pp_nm in pp_opts else 0
    sel_pp_nm  = st.selectbox("🔗 Linked Prepayment Scenario",pp_opts,index=pp_idx,key=f"dlg_pp_{sc_id}",
                               help="Choose a prepayment plan to layer on top of this rate scenario")
    sel_pp_rec = next((s for s in pp_list_db if s["name"]==sel_pp_nm),None)
    sc["linked_pp_db_id"] = sel_pp_rec["db_id"] if sel_pp_rec else 0

    # Monthly payment
    st.divider()
    today_bal          = b["today_m"].get("balance_today",b["principal"])
    base_remaining_yrs = b["today_m"].get("remaining_years",b["amort_years"])
    non_terminal       = [rn for rn in sc.get("renewals",[]) if not rn.get("is_terminal")]
    last_rate          = non_terminal[-1]["new_rate"] if non_terminal else b["annual_rate"]
    calc_monthly_sc    = (calc_pmt(today_bal,last_rate,b["n_py"],base_remaining_yrs,b["accel"])
                          if base_remaining_yrs > 0 else 0)
    min_pmt   = float(max(today_bal*periodic_rate(last_rate,b["n_py"])+1,100))
    saved_pmt = float(sc.get("user_pmt",0))
    init_pmt  = round(saved_pmt if saved_pmt > 0 else calc_monthly_sc,2)

    st.markdown("##### 💳 Monthly Payment")
    pc1,pc2 = st.columns([2,3])
    user_pmt = pc1.number_input("Amount ($)",min_value=min_pmt,max_value=float(today_bal),
                                 value=max(init_pmt,min_pmt),step=50.0,format="%.2f",
                                 key=f"dlg_pmt_{sc_id}",
                                 help=f"Required to maintain current amort at {last_rate:.2f}%: ${calc_monthly_sc:,.2f}")
    sc["user_pmt"] = user_pmt
    pc2.info(f"Required (baseline): **${calc_monthly_sc:,.2f}**  \nΔ vs required: **${user_pmt-calc_monthly_sc:+,.2f}/period**")

    # Renewals
    st.divider()
    st.markdown("##### 🔄 Rate Renewals")
    df_base,_ = build_amortization(
        b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
        accel=b["accel"],start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None,
    )
    orig_term_end_p = b["orig_term_end_p"]

    if st.button("➕ Add Renewal",key=f"dlg_add_ren_{sc_id}",
                  help="Adds a renewal + a terminal 'Rest of Amortization' entry"):
        # Default date: end of last non-terminal renewal, or end of original term
        nt = [rn for rn in sc.get("renewals",[]) if not rn.get("is_terminal")]
        if nt:
            last_rn = nt[-1]
            dd_date = period_to_date(last_rn["period"],b["start_date"],b["n_py"]) + \
                      relativedelta(years=int(last_rn["term_years"]),months=int((float(last_rn["term_years"])%1)*12))
            def_rate_new = last_rn["new_rate"]
        else:
            dd_date  = b["start_date"] + relativedelta(years=int(b["term_years"]),months=int((b["term_years"]%1)*12))
            def_rate_new = b["annual_rate"]
        dd = str(dd_date)
        new_period = date_to_period(dd,b["start_date"],b["n_py"])
        new_term   = 3.0
        new_rn     = {"id":str(uuid.uuid4())[:8],"mode":"By Date","date_str":dd,
                      "period":new_period,"new_rate":def_rate_new,"mtype":"Fixed",
                      "term_years":new_term,"actual_penalty":0,"misc_fees":250,
                      "orig_posted":def_rate_new+1.5,"curr_posted":max(def_rate_new-0.5,0.5),
                      "onetime_amount":0,"is_terminal":False,"variable_subs":[]}
        # Remove existing terminal entry (will be re-added)
        sc["renewals"] = [rn for rn in sc.get("renewals",[]) if not rn.get("is_terminal")]
        sc["renewals"].append(new_rn)
        # Auto-add terminal "rest of amortization" entry
        term_start_p = new_period + int(new_term*b["n_py"])
        term_date    = period_to_date(term_start_p,b["start_date"],b["n_py"])
        sc["renewals"].append({"id":str(uuid.uuid4())[:8],"mode":"By Date",
                                "date_str":str(term_date),"period":term_start_p,
                                "new_rate":def_rate_new,"mtype":"Fixed","term_years":30,
                                "actual_penalty":0,"misc_fees":0,
                                "orig_posted":0,"curr_posted":0,
                                "onetime_amount":0,"is_terminal":True,"variable_subs":[]})

    prev_term_end_p = orig_term_end_p
    ren_del = []

    for ri, rn in enumerate(sc.get("renewals",[])):
        rid = rn["id"]

        # ── Terminal "Rest of Amortization" entry ─────────────────
        if rn.get("is_terminal"):
            # Keep period in sync with the end of the previous renewal
            nt = [r for r in sc.get("renewals",[]) if not r.get("is_terminal")]
            if nt:
                last_nt = nt[-1]
                auto_p  = int(last_nt["period"])+int(float(last_nt["term_years"])*b["n_py"])
                rn["period"] = auto_p
            with st.container(border=True):
                st.markdown("📎 **Rest of Amortization** — average rate for remaining term")
                tc1,tc2,tc3 = st.columns([3,2,1])
                # Auto-show the effective start date
                auto_d = period_to_date(rn["period"],b["start_date"],b["n_py"])
                tc1.caption(f"Starts: **{auto_d.strftime('%b %d, %Y')}** (period {rn['period']})")
                # Single key — widget key = seed key (no mismatch)
                wkey_term = f"dlg_trate_{sc_id}_{rid}"
                if wkey_term not in st.session_state:
                    # Seed from saved rn value (loaded from DB); fallback to last renewal rate
                    st.session_state[wkey_term] = float(rn.get("new_rate", b["annual_rate"]))
                rn["new_rate"] = float(tc2.number_input(
                    "Avg Rate (%) for rest of amort", 0.5, 20.0,
                    value=st.session_state[wkey_term],
                    step=0.01, format="%.2f",
                    key=wkey_term,
                    help="Average interest rate for the remainder of amortization after the last renewal. "
                         "Seeded from last saved value; never auto-reset."))
                # keep term_years large to cover rest
                rn["term_years"] = b["amort_years"]
                if tc3.button("🗑️",key=f"dlg_del_term_{sc_id}_{rid}",help="Remove terminal entry"):
                    ren_del.append(ri)
            continue

        # ── Regular renewal entry ─────────────────────────────────
        with st.container(border=True):
            c1,c2,c3,c4,c5,c6 = st.columns([1.4,1.8,1.4,1.4,1.4,0.6])
            rn["mode"] = c1.radio("Mode",["By Date","By Period"],
                                   index=0 if rn.get("mode","By Date")=="By Date" else 1,
                                   horizontal=True,key=f"dlg_rm_{sc_id}_{rid}",
                                   help="Set renewal by calendar date or payment period number")
            if rn["mode"] == "By Date":
                pd_v = c2.date_input("Effective Date",
                                      date.fromisoformat(rn.get("date_str") or str(b["start_date"])),
                                      key=f"dlg_rd_{sc_id}_{rid}",
                                      help="Calendar date this renewal term begins")
                rn["date_str"] = str(pd_v)
                rn["period"]   = date_to_period(pd_v,b["start_date"],b["n_py"])
                c2.caption(f"≈ Period {rn['period']}")
            else:
                mx = int(b["amort_years"]*b["n_py"])
                rn["period"] = int(c2.number_input("Period #",1,mx,
                                   int(rn.get("period",orig_term_end_p+1)),
                                   key=f"dlg_rp_{sc_id}_{rid}",
                                   help="Payment period number when renewal starts"))
                c2.caption(f"≈ {period_to_date(rn['period'],b['start_date'],b['n_py']).strftime('%b %Y')}")
            rn["mtype"] = c3.selectbox("Type",["Fixed","Variable"],
                                        index=0 if rn.get("mtype","Fixed")=="Fixed" else 1,
                                        key=f"dlg_rmt_{sc_id}_{rid}",
                                        help="Fixed locks rate for term; Variable floats with prime rate")
            rn["new_rate"] = float(c4.number_input("Rate (%)",0.5,20.0,
                                   float(rn.get("new_rate",b["annual_rate"])),
                                   0.01,format="%.2f",key=f"dlg_rrt_{sc_id}_{rid}",
                                   help="Annual interest rate for this renewal term"))
            rn["term_years"] = c5.selectbox("Term (yrs)",TERM_OPTS,
                                             index=TERM_OPTS.index(rn.get("term_years",3))
                                                   if rn.get("term_years",3) in TERM_OPTS else 3,
                                             key=f"dlg_rty_{sc_id}_{rid}",
                                             help="Length of this renewal term")
            if c6.button("🗑️",key=f"dlg_delren_{sc_id}_{rid}",help="Remove this renewal"):
                ren_del.append(ri)

            rns_d = period_to_date(rn["period"],b["start_date"],b["n_py"])
            rne_d = rns_d+relativedelta(years=int(rn["term_years"]),months=int((float(rn["term_years"])%1)*12))
            l1,l2 = st.columns([2,3])
            rn["onetime_amount"] = float(l1.number_input(
                "💰 One-time lump at term start ($)",0,2_000_000,
                int(rn.get("onetime_amount",0)),1_000,key=f"dlg_ota_{sc_id}_{rid}",
                help=f"Principal prepayment at period {rn['period']} ({rns_d.strftime('%b %Y')})"))
            l2.caption(f"📅 **{rns_d.strftime('%b %d, %Y')}** → **{rne_d.strftime('%b %d, %Y')}**")

            # ── Variable sub-scenarios ────────────────────────────
            if rn.get("mtype") == "Variable":
                st.markdown(f"<small>📉 **Variable Rate — Sub-Scenarios** (rate changes within this term)</small>",
                            unsafe_allow_html=True)
                vsubs = rn.get("variable_subs",[])
                n_subs_key = f"dlg_nsubs_{sc_id}_{rid}"
                if n_subs_key not in st.session_state:
                    st.session_state[n_subs_key] = len(vsubs)
                n_subs = st.number_input(
                    f"How many rate changes within this variable term?",
                    0, 10, key=n_subs_key,
                    help="Bank may announce rate changes during the variable term. Each sub-scenario (a, b, c…) records an exact date and new rate.")
                # Resize vsubs list to match n_subs
                while len(vsubs) < n_subs:
                    vsubs.append({"id":str(uuid.uuid4())[:8],
                                  "date_str":str(rns_d+relativedelta(months=len(vsubs)+1)),
                                  "rate":rn["new_rate"]})
                if n_subs < len(vsubs):
                    vsubs = vsubs[:n_subs]
                rn["variable_subs"] = vsubs

                for si, sub in enumerate(vsubs):
                    letter = SUB_LETTERS[si] if si < len(SUB_LETTERS) else str(si)
                    sc1,sc2,sc3 = st.columns([0.5,2,2])
                    sc1.markdown(f"**{letter})**")
                    sub_d = sc2.date_input(
                        f"Rate change date ({letter})",
                        date.fromisoformat(sub.get("date_str") or str(rns_d+relativedelta(months=si+1))),
                        min_value=rns_d, max_value=rne_d,
                        key=f"dlg_sub_d_{sc_id}_{rid}_{si}",
                        help=f"Date from which rate '{letter}' becomes effective (must be within the term {rns_d.strftime('%b %Y')}–{rne_d.strftime('%b %Y')})")
                    sub["date_str"] = str(sub_d)
                    sub["rate"] = float(sc3.number_input(
                        f"Rate % ({letter})",0.5,20.0,float(sub.get("rate",rn["new_rate"])),0.01,
                        format="%.2f",key=f"dlg_sub_r_{sc_id}_{rid}_{si}",
                        help=f"Annual variable rate effective from {sub_d.strftime('%b %d, %Y')}"))

            # ── Early renewal / Misc fees ─────────────────────────
            is_early = rn["period"] < prev_term_end_p
            if is_early:
                months_left = max(int((prev_term_end_p-rn["period"])/b["n_py"]*12),1)
                rf       = df_base[df_base["Period"]<=rn["period"]]
                bal_ren  = float(rf["Balance"].iloc[-1]) if not rf.empty else b["principal"]
                rate_ren = float(rf["Rate (%)"].iloc[-1]) if not rf.empty else b["annual_rate"]
                st.markdown(f'<div class="warn">⚡ <b>Early Renewal</b> — {months_left} mo remain · Balance: <b>${bal_ren:,.0f}</b></div>',unsafe_allow_html=True)
                bp1,bp2 = st.columns(2)
                rn["orig_posted"] = float(bp1.number_input("Orig posted rate (%)",0.5,20.0,
                                          float(rn.get("orig_posted",rate_ren+1.5)),0.01,format="%.2f",
                                          key=f"dlg_op_{sc_id}_{rid}",
                                          help="Bank's posted rate when you originally signed"))
                rn["curr_posted"] = float(bp2.number_input("Curr posted rate (%)",0.5,20.0,
                                          float(rn.get("curr_posted",max(rate_ren-0.5,0.5))),0.01,format="%.2f",
                                          key=f"dlg_cp_{sc_id}_{rid}",
                                          help="Current posted rate for remaining term length"))
                adv = calc_break_penalty(bal_ren,rate_ren,rn["mtype"],rn["orig_posted"],rn["curr_posted"],months_left)
                pa1,pa2,pa3 = st.columns(3)
                pa1.metric("3-Month Interest",f"${adv['3_months_interest']:,.0f}",
                           help="3 months of interest on outstanding balance at renewal point")
                if adv["ird"] is not None:
                    pa2.metric("IRD",f"${adv['ird']:,.0f}",
                               help="Interest Rate Differential — posted rate spread × balance × remaining months")
                pa3.metric("Auto Max",f"${adv['calc_penalty']:,.0f}",
                           help="Greater of 3-month interest and IRD — lender takes the higher")

                # Penalty — seed-once pattern
                wkey = f"dlg_pen_txt_{sc_id}_{rid}"
                _seed_text(wkey, rn.get("actual_penalty", adv["calc_penalty"]))
                st.text_input(f"Penalty to apply ($) — advisory: ${adv['calc_penalty']:,.0f}",
                               key=wkey,
                               help="Type the actual penalty your bank quoted. Seeded from saved value; never auto-overridden.")
                try: rn["actual_penalty"] = float(st.session_state[wkey].replace(",","").replace("$",""))
                except Exception: rn["actual_penalty"] = adv["calc_penalty"]

                # Misc fees — seed-once pattern (FIX #3)
                mf_key = f"dlg_mf_{sc_id}_{rid}"
                _seed_number(mf_key, rn.get("misc_fees",500))
                rn["misc_fees"] = float(st.number_input(
                    "Misc fees ($)",0,50_000,
                    value=st.session_state[mf_key],step=50,
                    key=mf_key,
                    help="Admin, appraisal, and legal fees for early break. Saved value reloads on dialog open."))

                total_exit = rn["actual_penalty"]+rn["misc_fees"]
                old_p = calc_pmt(bal_ren,rate_ren,12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
                new_p = calc_pmt(bal_ren,rn["new_rate"],12,max(b["amort_years"]-rn["period"]/b["n_py"],1))
                be_str = (f"  ·  Break-even: <b>{total_exit/abs(old_p-new_p):.0f} months</b>" if abs(old_p-new_p)>1 else "")
                st.markdown(f'<div class="pen">💸 Total exit: <b>${total_exit:,.0f}</b>{be_str}</div>',unsafe_allow_html=True)
            else:
                # Normal renewal misc fees — seed-once pattern
                mf_key2 = f"dlg_mf2_{sc_id}_{rid}"
                _seed_number(mf_key2, rn.get("misc_fees",250))
                rn["misc_fees"] = float(st.number_input(
                    "Misc fees ($)",0,50_000,
                    value=st.session_state[mf_key2],step=50,
                    key=mf_key2,
                    help="Admin and legal fees at normal renewal. Saved value reloads on dialog open."))
                rn["actual_penalty"] = 0

            prev_term_end_p = int(rn["period"])+int(float(rn["term_years"])*b["n_py"])

    for ri in sorted(ren_del,reverse=True): sc["renewals"].pop(ri)
    # Update terminal period after any deletions
    nt2 = [r for r in sc.get("renewals",[]) if not r.get("is_terminal")]
    for rn in sc.get("renewals",[]):
        if rn.get("is_terminal") and nt2:
            last_nt2 = nt2[-1]
            # Only update period (geometry), NEVER overwrite user's rate
            rn["period"] = int(last_nt2["period"])+int(float(last_nt2["term_years"])*b["n_py"])
            # Seed the widget default to last renewal rate only if widget hasn't been touched
            rid_t = rn["id"]
            wkey_t = f"dlg_trate_{sc_id}_{rid_t}"
            if wkey_t not in st.session_state:
                st.session_state[wkey_t] = float(rn.get("new_rate", last_nt2["new_rate"]))

    st.divider()
    ba1,ba2 = st.columns(2)
    if ba1.button("💾 Save to DB",key=f"dlg_save_{sc_id}",use_container_width=True,type="primary",
                   help="Saves using the Scenario Name above"):
        if not sc["name"].strip():
            st.error("❌ Scenario Name cannot be empty.")
        else:
            pp_settings={"annual_lump":0,"lump_month":1,"lump_start_year":1,"lump_num_years":0,
                         "pay_increase_type":"None","pay_increase_val":0,"onetime_period":0,"onetime_amount":0}
            db_id = db_save_scenario(conn,sc.get("db_id"),sc["name"].strip(),sc.get("desc",""),
                                     sc["renewals"],pp_settings,sc.get("user_pmt",0),sc.get("linked_pp_db_id",0))
            if db_id:
                sc["db_id"]=db_id
                st.session_state["_editing_sc_id"]=None
                st.rerun()
            else:
                st.error("❌ Save failed — check DB connection.")
    if ba2.button("✕ Close without saving",key=f"dlg_close_{sc_id}",use_container_width=True):
        st.session_state["_editing_sc_id"]=None; st.rerun()


# ── Prepayment scenario edit dialog ──────────────────────────────

@st.dialog("✏️ Edit Prepayment Scenario", width="large")
def edit_prepay_dialog():
    sc_id = st.session_state.get("_editing_pp_sc_id")
    pps   = st.session_state.get("pp_scenarios",{})
    sc    = pps.get(sc_id)
    b     = st.session_state.get("base")
    conn  = st.session_state.get("db_conn")
    if sc is None or b is None:
        st.error("Missing data.")
        if st.button("Close"): st.session_state["_editing_pp_sc_id"]=None; st.rerun()
        return
    d1,d2 = st.columns([2,3])
    sc["name"] = d1.text_input("Scenario Name",sc["name"],key=f"pp_dlg_name_{sc_id}",
                                help="Name to identify and save this prepayment strategy")
    sc["desc"] = d2.text_area("Description",sc.get("desc",""),height=68,key=f"pp_dlg_desc_{sc_id}",
                               placeholder="Describe this prepayment plan…")
    st.divider()
    s = sc["settings"]
    st.markdown("##### 📅 Annual Lump-Sum")
    al1,al2 = st.columns(2)
    s["annual_lump"] = float(al1.number_input("Amount ($)",0,500_000,int(s.get("annual_lump",0)),500,
                              key=f"pp_dlg_al_{sc_id}",help="Amount of extra principal to prepay each year"))
    if s["annual_lump"] > 0:
        lm_idx = max(0,int(s.get("lump_month",1))-1)
        s["lump_month"] = LUMP_MM[al2.selectbox("Month each year",LUMP_MONTHS,index=lm_idx,key=f"pp_dlg_lm_{sc_id}",help="Month to apply the annual lump sum")]
        lc1,lc2 = st.columns(2)
        s["lump_start_year"] = int(lc1.number_input("Start year #",1,30,int(s.get("lump_start_year",1)),key=f"pp_dlg_ls_{sc_id}",help="Mortgage year when annual lumps begin"))
        s["lump_num_years"]  = int(lc2.number_input("For N years",0,30,int(s.get("lump_num_years",0)),key=f"pp_dlg_ln_{sc_id}",help="Number of consecutive years to apply"))
    st.markdown("##### 💳 Payment Increase")
    inc_opts = ["None","Fixed $","% increase"]
    inc_cur  = s.get("pay_increase_type","None")
    if inc_cur=="Fixed": inc_cur="Fixed $"
    if inc_cur not in inc_opts: inc_cur="None"
    inc_t = st.radio("Type",inc_opts,index=inc_opts.index(inc_cur),horizontal=True,key=f"pp_dlg_it_{sc_id}",
                      help="How to increase regular payments above the minimum")
    s["pay_increase_type"] = "Fixed" if inc_t=="Fixed $" else inc_t
    if inc_t=="Fixed $":
        s["pay_increase_val"] = float(st.number_input("Extra per payment ($)",0,10_000,int(s.get("pay_increase_val",0)),50,key=f"pp_dlg_if_{sc_id}",help="Fixed extra $ added each payment"))
    elif inc_t=="% increase":
        s["pay_increase_val"] = float(st.slider("% increase",1,100,int(s.get("pay_increase_val",10)),key=f"pp_dlg_ip_{sc_id}",help="% of required payment to add on top"))
    else:
        s["pay_increase_val"] = 0
    st.markdown("##### 🔁 One-Time Lump Sum")
    ot1,ot2 = st.columns(2)
    s["onetime_period"] = int(ot1.number_input("Period #",0,int(b["amort_years"]*b["n_py"]),int(s.get("onetime_period",0)),key=f"pp_dlg_op_{sc_id}",help="Payment period for the one-time lump (0 = disabled)"))
    s["onetime_amount"] = float(ot2.number_input("Amount ($)",0,2_000_000,int(s.get("onetime_amount",0)),1_000,key=f"pp_dlg_oa_{sc_id}",help="One-time lump sum amount"))
    if s["onetime_period"] > 0:
        d_ot = period_to_date(s["onetime_period"],b["start_date"],b["n_py"])
        st.caption(f"Applies at period {s['onetime_period']} ≈ **{d_ot.strftime('%b %Y')}**")
    st.divider()
    ba1,ba2 = st.columns(2)
    if ba1.button("💾 Save to DB",key=f"pp_dlg_save_{sc_id}",use_container_width=True,type="primary",help="Save this prepayment strategy"):
        if not sc["name"].strip():
            st.error("❌ Name cannot be empty.")
        else:
            db_id = db_save_prepay_scenario(conn,sc.get("db_id"),sc["name"].strip(),sc.get("desc",""),s)
            if db_id:
                sc["db_id"]=db_id; st.session_state["_editing_pp_sc_id"]=None; st.rerun()
            else:
                st.error("❌ Save failed.")
    if ba2.button("✕ Close",key=f"pp_dlg_close_{sc_id}",use_container_width=True):
        st.session_state["_editing_pp_sc_id"]=None; st.rerun()
