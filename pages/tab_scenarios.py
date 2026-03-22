"""pages/tab_scenarios.py
Tab 2 — Rate Change Scenarios

Changes vs previous version:
  #1  Prepayment section REMOVED from scenarios (moved to dedicated Prepayment tab)
  #2  DB persistence bug FIXED:
        - _init_db no longer drops tables on every connect (root cause)
        - sc_loaded_from_db check fixed: value check, not key-existence check
  #3  One-time lump sum moved per-renewal (no date needed — period = term start)
  #4  Adjusted Interest recomputed live when monthly payment changes
  #5  All inputs in a @st.dialog popup; card shows compact summary
  #6  Metrics tiles + charts outside the expander (always visible)
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

TERM_OPTS = [0.5, 1, 2, 3, 4, 5, 7, 10]

# ── Helpers ───────────────────────────────────────────────────────

def _penalty_key(sc_id, rid):
    return f"_penalty_{sc_id}_{rid}"


def _build_sc_extra(sc, b):
    """Build extra_payments from per-renewal one-time lump sums only.
    Replaces the old _make_extra_payments which also handled annual lumps
    and payment increases — those are now in the Prepayment tab."""
    extras = list(b.get("past_extra") or [])
    for rn in sc.get("renewals", []):
        amt = float(rn.get("onetime_amount", 0))
        if amt > 0:
            extras.append({"period": int(rn["period"]), "amount": amt})
    return extras


def _compute_scenario(sc, b):
    """Build amortization for a scenario. Returns tuple of all needed values."""
    main_rc = [{"period": rn["period"], "new_rate": rn["new_rate"]}
               for rn in sc.get("renewals", [])]
    all_rcs = (b.get("past_renewal_rcs") or []) + main_rc
    sc_extra = _build_sc_extra(sc, b)

    df_sc, s_sc = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=sc_extra or None,
        rate_changes=all_rcs or None,
    )
    last_rate = sc["renewals"][-1]["new_rate"] if sc.get("renewals") else b["annual_rate"]
    sc_term_end_p = (
        int(sc["renewals"][-1]["period"])
        + int(float(sc["renewals"][-1]["term_years"]) * b["n_py"])
        if sc.get("renewals") else b["orig_term_end_p"]
    )
    return df_sc, s_sc, all_rcs, sc_extra, last_rate, sc_term_end_p


# ── Edit dialog (module-level required for @st.dialog) ────────────

@st.dialog("✏️ Edit Scenario", width="large")
def _edit_scenario_dialog():
    """All scenario inputs in a modal popup."""
    sc_id = st.session_state.get("_editing_sc_id")
    rcs   = st.session_state.get("rc_scenarios", {})
    sc    = rcs.get(sc_id)
    b     = st.session_state.get("base")
    conn  = st.session_state.get("db_conn")

    if sc is None or b is None:
        st.error("Cannot open editor — missing scenario or base data.")
        if st.button("Close", key="dlg_err_close"):
            st.session_state["_editing_sc_id"] = None
            st.rerun()
        return

    # ── Name + description ────────────────────────────────────────
    d1, d2 = st.columns([2, 3])
    sc["name"] = d1.text_input(
        "Scenario Name", sc["name"], key=f"dlg_name_{sc_id}",
        help="Short label shown on the scenario card"
    )
    sc["desc"] = d2.text_area(
        "Description", sc.get("desc", ""), height=68,
        placeholder="Describe this scenario…", key=f"dlg_desc_{sc_id}"
    )

    st.divider()
    st.markdown("##### 🔄 Rate Renewals")

    # Base amortization for early-renewal penalty calculations
    df_base, _ = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None,
    )
    orig_term_end_p = b["orig_term_end_p"]

    if st.button("➕ Add Renewal", key=f"dlg_add_ren_{sc_id}",
                  help="Add a rate renewal entry to this scenario"):
        dd = str(b["start_date"] + relativedelta(
            years=int(b["term_years"]),
            months=int((b["term_years"] % 1) * 12),
        ))
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
            "curr_posted": max(b["annual_rate"] - 0.5, 0.5),
            "onetime_amount": 0,
            "variable_subs": {},
        })
        # No st.rerun() — @st.dialog auto-reruns on button interaction

    prev_term_end_p = orig_term_end_p
    ren_del = []

    for ri, rn in enumerate(sc.get("renewals", [])):
        rid  = rn["id"]
        pkey = _penalty_key(sc_id, rid)

        with st.container(border=True):
            # ── Core renewal fields ───────────────────────────────
            c1, c2, c3, c4, c5, c6 = st.columns([1.4, 1.8, 1.4, 1.4, 1.4, 0.6])

            rn["mode"] = c1.radio(
                "Mode", ["By Date", "By Period"],
                index=0 if rn.get("mode", "By Date") == "By Date" else 1,
                horizontal=True, key=f"dlg_rm_{sc_id}_{rid}",
                help="Set renewal by calendar date or period number"
            )
            if rn["mode"] == "By Date":
                pd_v = c2.date_input(
                    "Effective Date",
                    date.fromisoformat(rn.get("date_str") or str(b["start_date"])),
                    key=f"dlg_rd_{sc_id}_{rid}",
                    help="Date when this renewal takes effect"
                )
                rn["date_str"] = str(pd_v)
                rn["period"]   = date_to_period(pd_v, b["start_date"], b["n_py"])
                c2.caption(f"≈ Period {rn['period']}")
            else:
                mx = int(b["amort_years"] * b["n_py"])
                rn["period"] = int(c2.number_input(
                    "Period #", 1, mx,
                    int(rn.get("period", orig_term_end_p + 1)),
                    key=f"dlg_rp_{sc_id}_{rid}",
                    help="Payment period number when renewal starts"
                ))
                c2.caption(
                    f"≈ {period_to_date(rn['period'], b['start_date'], b['n_py']).strftime('%b %Y')}"
                )

            rn["mtype"] = c3.selectbox(
                "Type", ["Fixed", "Variable"],
                index=0 if rn.get("mtype", "Fixed") == "Fixed" else 1,
                key=f"dlg_rmt_{sc_id}_{rid}",
                help="Fixed locks rate for term; Variable floats with prime"
            )
            rn["new_rate"] = float(c4.number_input(
                "Rate (%)", 0.5, 20.0,
                float(rn.get("new_rate", b["annual_rate"])),
                0.01, format="%.2f", key=f"dlg_rrt_{sc_id}_{rid}",
                help="Annual interest rate for this renewal term"
            ))
            rn["term_years"] = c5.selectbox(
                "Term (yrs)", TERM_OPTS,
                index=TERM_OPTS.index(rn.get("term_years", 3))
                      if rn.get("term_years", 3) in TERM_OPTS else 3,
                key=f"dlg_rty_{sc_id}_{rid}",
                help="Length of this renewal term"
            )
            if c6.button("🗑️", key=f"dlg_delren_{sc_id}_{rid}",
                          help="Remove this renewal"):
                ren_del.append(ri)

            # ── One-time lump sum + date range ────────────────────
            rns_d = period_to_date(rn["period"], b["start_date"], b["n_py"])
            rne_d = rns_d + relativedelta(
                years=int(rn["term_years"]),
                months=int((float(rn["term_years"]) % 1) * 12),
            )
            l1, l2 = st.columns([2, 3])
            rn["onetime_amount"] = float(l1.number_input(
                "💰 One-time lump at term start ($)", 0, 2_000_000,
                int(rn.get("onetime_amount", 0)), 1_000,
                key=f"dlg_ota_{sc_id}_{rid}",
                help=(f"Lump-sum principal prepayment applied at period {rn['period']} "
                      f"({rns_d.strftime('%b %Y')}) — the start of this term")
            ))
            l2.caption(
                f"📅 **{rns_d.strftime('%b %d, %Y')}** → **{rne_d.strftime('%b %d, %Y')}**"
            )

            # ── Early renewal penalty ─────────────────────────────
            is_early = rn["period"] < prev_term_end_p
            if is_early:
                months_left = max(
                    int((prev_term_end_p - rn["period"]) / b["n_py"] * 12), 1
                )
                rf       = df_base[df_base["Period"] <= rn["period"]]
                bal_ren  = float(rf["Balance"].iloc[-1]) if not rf.empty else b["principal"]
                rate_ren = float(rf["Rate (%)"].iloc[-1]) if not rf.empty else b["annual_rate"]

                st.markdown(
                    f'<div class="warn">⚡ <b>Early Renewal</b> — {months_left} mo remain · '
                    f'Balance: <b>${bal_ren:,.0f}</b></div>',
                    unsafe_allow_html=True,
                )
                bp1, bp2 = st.columns(2)
                rn["orig_posted"] = float(bp1.number_input(
                    "Orig posted rate (%)", 0.5, 20.0,
                    float(rn.get("orig_posted", rate_ren + 1.5)), 0.01,
                    format="%.2f", key=f"dlg_op_{sc_id}_{rid}",
                    help="Bank's posted rate when you originally signed"
                ))
                rn["curr_posted"] = float(bp2.number_input(
                    "Curr posted rate (%)", 0.5, 20.0,
                    float(rn.get("curr_posted", max(rate_ren - 0.5, 0.5))), 0.01,
                    format="%.2f", key=f"dlg_cp_{sc_id}_{rid}",
                    help="Current posted rate for remaining term length"
                ))
                adv = calc_break_penalty(
                    bal_ren, rate_ren, rn["mtype"],
                    rn["orig_posted"], rn["curr_posted"], months_left,
                )
                pa1, pa2, pa3 = st.columns(3)
                pa1.metric("3-Month Interest", f"${adv['3_months_interest']:,.0f}",
                           help="3 months of interest on outstanding balance")
                if adv["ird"] is not None:
                    pa2.metric("IRD", f"${adv['ird']:,.0f}",
                               help="Interest Rate Differential")
                pa3.metric("Auto Max", f"${adv['calc_penalty']:,.0f}",
                           help="Greater of 3-month interest and IRD")

                if pkey not in st.session_state:
                    st.session_state[pkey] = str(int(adv["calc_penalty"]))
                pen_str = st.text_input(
                    f"Penalty to apply ($) — advisory max ${adv['calc_penalty']:,.0f}",
                    value=st.session_state[pkey],
                    key=f"dlg_pen_txt_{sc_id}_{rid}",
                    help="Edit to enter the actual penalty your bank quoted"
                )
                st.session_state[pkey] = pen_str
                try:
                    rn["actual_penalty"] = float(pen_str.replace(",", "").replace("$", ""))
                except Exception:
                    rn["actual_penalty"] = adv["calc_penalty"]

                rn["misc_fees"] = float(st.number_input(
                    "Misc fees ($)", 0, 50_000, int(rn.get("misc_fees", 500)), 50,
                    key=f"dlg_mf_{sc_id}_{rid}",
                    help="Admin, appraisal, and legal fees"
                ))
                total_exit  = rn["actual_penalty"] + rn["misc_fees"]
                old_p = calc_pmt(bal_ren, rate_ren, 12,
                                  max(b["amort_years"] - rn["period"] / b["n_py"], 1))
                new_p = calc_pmt(bal_ren, rn["new_rate"], 12,
                                  max(b["amort_years"] - rn["period"] / b["n_py"], 1))
                be_str = (
                    f"  ·  Break-even: <b>{total_exit / abs(old_p - new_p):.0f} months</b>"
                    if abs(old_p - new_p) > 1 else ""
                )
                st.markdown(
                    f'<div class="pen">💸 Total exit cost: <b>${total_exit:,.0f}</b>{be_str}</div>',
                    unsafe_allow_html=True,
                )
            else:
                rn["misc_fees"] = float(st.number_input(
                    "Misc fees ($)", 0, 50_000, int(rn.get("misc_fees", 250)), 50,
                    key=f"dlg_mf2_{sc_id}_{rid}",
                    help="Admin, appraisal, and legal fees at normal renewal"
                ))
                rn["actual_penalty"] = 0

            prev_term_end_p = (
                int(rn["period"]) + int(float(rn["term_years"]) * b["n_py"])
            )

    # Apply deletions (dialog auto-reruns on button click — no st.rerun() needed)
    for ri in sorted(ren_del, reverse=True):
        sc["renewals"].pop(ri)

    st.divider()

    # ── Bottom action bar ─────────────────────────────────────────
    ba1, ba2, ba3 = st.columns([2, 2, 3])
    save_name = ba3.text_input(
        "", sc["name"], key=f"dlg_sn_{sc_id}",
        placeholder="Scenario name…", label_visibility="collapsed",
        help="Name to save under — defaults to scenario name above"
    )

    if ba1.button("💾 Save to DB", key=f"dlg_save_{sc_id}",
                   use_container_width=True, type="primary",
                   help="Persist this scenario to SQL Server"):
        nm    = save_name.strip() or sc["name"]
        pp_settings = {
            "annual_lump": 0, "lump_month": 1, "lump_start_year": 1,
            "lump_num_years": 0, "pay_increase_type": "None",
            "pay_increase_val": 0, "onetime_period": 0, "onetime_amount": 0,
        }
        db_id = db_save_scenario(
            conn, sc.get("db_id"), nm, sc.get("desc", ""),
            sc["renewals"], pp_settings,
        )
        if db_id:
            sc["db_id"] = db_id
            sc["name"]  = nm
            st.session_state["_editing_sc_id"] = None
            st.rerun()
        else:
            st.error("❌ Save failed — check DB connection.")

    if ba2.button("✕ Close", key=f"dlg_close_{sc_id}",
                   use_container_width=True,
                   help="Close without saving"):
        st.session_state["_editing_sc_id"] = None
        st.rerun()


# ── Main render ───────────────────────────────────────────────────

def render_tab_scenarios(conn, b):
    st.subheader("📈 Rate Change Scenarios")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    # ── FIX #2: check VALUE, not key existence ────────────────────
    # Previously: `if "sc_loaded_from_db" not in st.session_state:`
    # That was always False because app.py initialises the key to False,
    # so DB was NEVER loaded. Fixed below.
    if not st.session_state.get("sc_loaded_from_db"):
        db_rows = db_load_scenarios(conn)
        rcs = {}
        for i, sc_row in enumerate(db_rows, 1):
            for rn in sc_row.get("renewals", []):
                rn.setdefault("onetime_amount", 0)   # backward compat
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            rcs[sc_row["_key"]] = sc_row
        st.session_state.rc_scenarios  = rcs
        st.session_state.sc_loaded_from_db = True

    rcs: dict = st.session_state.rc_scenarios

    # ── Shared base amortization ──────────────────────────────────
    df_base, s_base = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=b.get("past_extra") or None,
        rate_changes=b.get("past_renewal_rcs") or None,
    )
    base_remaining_yrs = b["today_m"].get("remaining_years", b["amort_years"])
    today_p            = b["today_m"].get("period_today", 0)
    today_bal          = b["today_m"].get("balance_today", b["principal"])

    # ── Header ────────────────────────────────────────────────────
    hdr1, hdr2 = st.columns([5, 2])
    hdr1.caption(
        "Each card shows a compact summary. "
        "Click **✏️ Edit** to open the full editor in a popup."
    )
    if hdr2.button("➕ New Scenario", key="btn_new_rc",
                    use_container_width=True,
                    help="Create a new rate-change scenario"):
        nk       = str(uuid.uuid4())[:8]
        next_seq = max((s.get("_seq", 0) for s in rcs.values()), default=0) + 1
        rcs[nk]  = {
            "_key": nk, "_seq": next_seq, "db_id": None,
            "name": f"Scenario {next_seq}", "desc": "",
            "renewals": [], "pp": {},
        }
        st.session_state["_editing_sc_id"] = nk
        st.rerun()

    sc_del       = []
    sorted_scs   = sorted(rcs.items(), key=lambda kv: kv[1].get("_seq", 999))

    for sc_id, sc in sorted_scs:
        renewals   = sc.get("renewals", [])
        seq        = sc.get("_seq", "?")

        # Build display strings
        rates_str  = (
            " → ".join(f"{rn['new_rate']:.2f}%" for rn in renewals)
            or f"{b['annual_rate']:.2f}% (base, no renewals)"
        )
        lump_total = sum(float(rn.get("onetime_amount", 0)) for rn in renewals)
        lump_str   = f"  ·  💰 ${lump_total:,.0f} lump" if lump_total > 0 else ""
        badge      = "🟢" if sc.get("db_id") else "🔴"

        # ── #5: Compact card (collapsable) ─────────────────────────
        with st.expander(
            f"{badge} **#{seq} — {sc['name']}**  ·  "
            f"{len(renewals)} renewal(s)  ·  {rates_str}{lump_str}",
            expanded=False,
        ):
            ci1, ci2, ci3, ci4 = st.columns([4, 0.1, 1.5, 1.5])
            ci1.caption(sc.get("desc", "") or "No description")
            # invisible spacer
            if ci3.button("✏️ Edit", key=f"edit_{sc_id}",
                           use_container_width=True,
                           help="Open full editor popup"):
                st.session_state["_editing_sc_id"] = sc_id
            if ci4.button("🗑️ Delete", key=f"del_{sc_id}",
                           use_container_width=True,
                           help="Delete this scenario (also removes from DB)"):
                if sc.get("db_id"):
                    db_delete_scenario(conn, sc["db_id"])
                sc_del.append(sc_id)

        # ── #6: Results OUTSIDE expander — always visible ──────────
        df_sc, s_sc, all_rcs, sc_extra, last_rate, sc_term_end_p = (
            _compute_scenario(sc, b)
        )
        calc_monthly_sc = (
            calc_pmt(today_bal, last_rate, b["n_py"], base_remaining_yrs, b["accel"])
            if base_remaining_yrs > 0 else 0
        )
        min_pmt = float(max(today_bal * periodic_rate(last_rate, b["n_py"]) + 1, 100))

        res_l, res_r = st.columns([2, 5])
        with res_l:
            user_pmt = st.number_input(
                f"💳 Monthly Payment — #{seq}",
                min_value=min_pmt,
                max_value=float(today_bal),
                value=round(calc_monthly_sc, 2),
                step=50.0, format="%.2f",
                key=f"user_pmt_{sc_id}",
                help=(
                    f"Required payment to maintain current amortization "
                    f"at {last_rate:.2f}%: ${calc_monthly_sc:,.2f}\n"
                    "Raise to pay off sooner; lower to extend."
                ),
            )
            st.caption(f"Required: **${calc_monthly_sc:,.2f}**")

        # ── #4: Recompute interest & remaining with adjusted payment ──
        if abs(user_pmt - calc_monthly_sc) > 0.02:
            adj_rem = (
                calc_remaining_years(today_bal, last_rate, b["n_py"], user_pmt)
                if user_pmt > 0 else base_remaining_yrs
            )
            extra_per_pmt = max(0.0, user_pmt - calc_monthly_sc)
            adj_extra = list(sc_extra) + [
                {"period": p, "amount": extra_per_pmt}
                for p in range(max(today_p, 1),
                               int(b["amort_years"] * b["n_py"]) + 1)
            ]
            _, s_adj = build_amortization(
                b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                accel=b["accel"], start_date=b["start_date"],
                extra_payments=adj_extra or None,
                rate_changes=all_rcs or None,
            )
        else:
            adj_rem = base_remaining_yrs
            s_adj   = s_sc

        adj_end   = date.today() + relativedelta(
            years=int(adj_rem), months=int((adj_rem % 1) * 12)
        )
        delta_yrs = round(adj_rem - base_remaining_yrs, 1)
        colour    = "mc-g" if adj_rem <= base_remaining_yrs else "mc-r"

        with res_r:
            # Row 1 — baseline (static)
            m1, m2, m3 = st.columns(3)
            m1.metric("Base Interest", f"${s_base.get('total_interest', 0):,.0f}",
                       help="Total interest with no changes to current setup")
            m2.metric("Current Remaining", f"{base_remaining_yrs:.1f} yrs",
                       help="Remaining amortization from today at current rate")
            m3.metric("Required Payment", f"${calc_monthly_sc:,.2f}",
                       help=f"Payment to maintain {base_remaining_yrs:.1f} yr amort at {last_rate:.2f}%")

            # Row 2 — scenario adjusted (green/red boxes)
            a1, a2, a3 = st.columns(3)
            sc_int   = s_adj.get("total_interest", 0)
            sc_int_d = sc_int - s_base.get("total_interest", 0)
            a1.markdown(
                f'<div class="mc {colour}"><h3>Adjusted Interest</h3>'
                f'<p>${sc_int:,.0f} '
                f'<span style="font-size:.8rem">({sc_int_d:+,.0f})</span></p></div>',
                unsafe_allow_html=True,
            )
            a2.markdown(
                f'<div class="mc {colour}"><h3>Adjusted Remaining</h3>'
                f'<p>{adj_rem:.1f} yrs '
                f'<span style="font-size:.8rem">'
                f'({"same" if abs(delta_yrs) < 0.05 else f"{delta_yrs:+.1f} yrs"})'
                f'</span></p></div>',
                unsafe_allow_html=True,
            )
            a3.markdown(
                f'<div class="mc {colour}"><h3>Mortgage-free by</h3>'
                f'<p>{adj_end.strftime("%b %Y")}</p></div>',
                unsafe_allow_html=True,
            )

        # ── Charts (always visible) ────────────────────────────────
        if not df_sc.empty:
            ch1, ch2 = st.columns([3, 2])
            with ch1:
                fig_bar = stacked_bar_pi(
                    df_sc, today_p, sc_term_end_p,
                    f"#{seq} {sc['name']} — P & I",
                )
                st.plotly_chart(fig_bar, use_container_width=True,
                                key=f"ch_sc_{sc_id}")
            with ch2:
                fig_r = go.Figure()
                fig_r.add_scatter(
                    x=df_sc["Date"], y=df_sc["Rate (%)"],
                    fill="tozeroy", name="Rate (%)",
                    line=dict(color="#27ae60"),
                )
                fig_r.update_layout(
                    title=f"#{seq} Rate over time",
                    xaxis_title="Date", yaxis_title="%",
                    height=300, margin=dict(t=35, b=20),
                )
                st.plotly_chart(fig_r, use_container_width=True,
                                key=f"ch_rt_{sc_id}")

        st.divider()

    # Apply card-level deletions
    for k in sc_del:
        del rcs[k]
    if sc_del:
        st.rerun()

    # ── Open dialog if a scenario is being edited ─────────────────
    editing_id = st.session_state.get("_editing_sc_id")
    if editing_id and editing_id in rcs:
        _edit_scenario_dialog()

    # Education collapsable
    with st.expander("📚 Canadian Mortgage Education"):
        st.markdown(
            "**Semi-annual compounding**: `(1 + r/200)²` — required by Canada's Interest Act.  \n"
            "**CMHC**: <10% down = 4.00% · 10–15% = 3.10% · 15–20% = 2.80% · ≥20% = nil.  \n"
            "**Break penalty**: Variable = 3 months interest · "
            "Fixed = greater of 3-month interest or IRD."
        )
