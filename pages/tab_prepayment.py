"""pages/tab_prepayment.py — Tab 3: Prepayment Analysis (standalone extra prepayments)."""
import streamlit as st
from datetime import date
from dateutil.relativedelta import relativedelta

from modules import calc_pmt, date_to_period, build_amortization, db_load_scenarios, stacked_bar_pi


def render_tab_prepayment(conn, b):
    st.subheader("💰 Prepayment Analysis")
    if not b:
        st.info("⬅️ Complete Setup & Overview first."); return

    fn = b["n_py"]
    # FIX #7: allow selecting a saved rate scenario as the base
    db_sc = db_load_scenarios(conn)
    rc_opts = ["Current Setup (base rates)"] + [s["name"] for s in db_sc]
    chosen_rc = st.selectbox("Apply on top of:", rc_opts, key="pp_rc_sel",
                             help="Select a rate change scenario as the base, then add extra prepayments")
    if chosen_rc == "Current Setup (base rates)":
        active_rc = []
    else:
        sc_match = next((s for s in db_sc if s["name"]==chosen_rc), {})
        active_rc = [{"period":rn["period"],"new_rate":rn["new_rate"]}
                     for rn in sc_match.get("renewals",[])]
    all_rcs_pp = (b.get("past_renewal_rcs") or []) + active_rc

    col1,col2 = st.columns(2)
    with col1:
        st.markdown("##### 📅 Annual Lump-Sum")
        annual_lump = st.number_input("Amount ($)",0,500_000,0,500,key="pp_al")
        MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lump_month=st.selectbox("Month",MONTHS,key="pp_lm")
        lc1,lc2=st.columns(2)
        lump_start=lc1.number_input("Start year",1,30,1,key="pp_ls")
        lump_nyrs=lc2.number_input("N years",0,30,0,key="pp_ln")
        lmm={m:i+1 for i,m in enumerate(MONTHS)}
        future_extra=[]
        if annual_lump>0:
            for yr in range(int(lump_start),int(lump_start+lump_nyrs)):
                p=max(1,int((yr-1)*fn+lmm[lump_month]*fn/12))
                future_extra.append({"period":p,"amount":float(annual_lump)})
        pp_lim=st.slider("Lender limit (%)",10,30,20,key="pp_lim")
        if annual_lump>b["principal"]*pp_lim/100:
            st.warning(f"⚠️ Exceeds {pp_lim}% limit (${b['principal']*pp_lim/100:,.0f}).")
    with col2:
        st.markdown("##### 💳 Increased Regular Payments")
        inc_t=st.radio("Increase type",["Fixed $","% increase","None"],index=2,horizontal=True,key="pp_it")
        inc_v=0.0
        if inc_t=="Fixed $":
            inc_v=float(st.number_input("Extra/payment ($)",0,10_000,200,50,key="pp_if"))
        elif inc_t=="% increase":
            inc_pct=st.slider("% increase",1,100,10,key="pp_ip")
            inc_v=calc_pmt(b["principal"],b["annual_rate"],fn,b["amort_years"],b["accel"])*inc_pct/100
        if inc_v>0:
            for p in range(1,int(b["amort_years"]*fn)+1):
                future_extra.append({"period":p,"amount":inc_v})
        st.markdown("##### 🔁 One-Time Lump Sum")
        ot_mode=st.radio("Mode",["By Date","By Period"],horizontal=True,key="pp_om")
        if ot_mode=="By Date":
            ot_d=st.date_input("Date",b["start_date"]+relativedelta(years=1),
                               min_value=b["start_date"],key="pp_od")
            ot_p=date_to_period(ot_d,b["start_date"],fn)
            st.caption(f"≈ Period {ot_p}")
        else:
            ot_p=int(st.number_input("Period #",0,int(b["amort_years"]*fn),0,key="pp_op"))
        ot_a=st.number_input("Amount ($)",0,2_000_000,0,1_000,key="pp_oa")
        if ot_a>0: future_extra.append({"period":int(ot_p),"amount":float(ot_a)})

    past_extra=b.get("past_extra",[]); all_extra=past_extra+future_extra
    df_base,s_base=build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
                                       accel=b["accel"],start_date=b["start_date"],
                                       extra_payments=past_extra or None,rate_changes=all_rcs_pp or None)
    df_pp,s_pp=build_amortization(b["principal"],b["annual_rate"],b["n_py"],b["amort_years"],
                                   accel=b["accel"],start_date=b["start_date"],
                                   extra_payments=all_extra or None,rate_changes=all_rcs_pp or None)
    tm=b["today_m"]; tp=tm.get("period_today",0)
    base_rem=tm.get("remaining_years",b["amort_years"])
    rem_base=round((len(df_base)-tp)/b["n_py"],1) if tp>0 and not df_base.empty else base_rem
    rem_pp=round((len(df_pp)-tp)/b["n_py"],1) if tp>0 and not df_pp.empty else base_rem
    int_saved=s_base.get("total_interest",0)-s_pp.get("total_interest",0)
    new_total=sum(e["amount"] for e in future_extra)

    st.divider()
    m1,m2,m3,m4,m5,m6=st.columns(6)
    m1.metric("Interest (base)",f"${s_base.get('total_interest',0):,.0f}")
    m2.metric("Interest (+prepayments)",f"${s_pp.get('total_interest',0):,.0f}",delta=f"${-int_saved:+,.0f}")
    m3.metric("Remaining (base)",f"{rem_base:.1f} yrs")
    m4.metric("Remaining (+prepayments)",f"{rem_pp:.1f} yrs",delta=f"{rem_pp-rem_base:+.1f} yrs")
    m5.metric("Total New Prepaid",f"${new_total:,.0f}")
    m6.metric("Interest ROI",f"{int_saved/new_total*100:.1f}%" if new_total>0 and int_saved>0 else "—")
    if int_saved>0:
        st.markdown(f'<div class="ok">💚 Prepayments save <b>${int_saved:,.0f}</b> · '
                    f'Shorten by <b>{rem_base-rem_pp:.1f} yrs</b></div>',unsafe_allow_html=True)
    sc_end_p=int(b["term_years"]*b["n_py"])+int(3*b["n_py"])
    if not df_pp.empty:
        fig=stacked_bar_pi(df_pp,tp,sc_end_p,f"Prepayment Impact ({chosen_rc})")
        st.plotly_chart(fig,use_container_width=True,key="ch_pp_bar")
