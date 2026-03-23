"""pages/tab_scenarios.py — Rate Change Scenarios (REQ #1–6)."""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
import uuid

from datetime import date as _date_cls
from dateutil.relativedelta import relativedelta
from modules import (
    calc_pmt, build_amortization,
    db_load_scenarios, db_delete_scenario, db_save_scenario,
    db_load_prepay_scenarios, db_save_setup,
)
from pages.scenario_editor import compute_adj_scenario, edit_scenario_dialog, _get_linked_pp


def _pct(part, total):
    return f"{part/total*100:.1f}%" if total else "—"


def _arrow(val, base, higher_is_worse=True):
    """Return colored arrow + value string. higher_is_worse=True for interest."""
    if abs(val - base) < 0.05 * max(abs(base), 1): return "same", ""
    worse = (val > base) if higher_is_worse else (val < base)
    arrow = "🔺" if val > base else "🔻"
    color = "#c0392b" if worse else "#1e8449"
    return f'<span style="color:{color}">{arrow} {abs(val-base):,.0f}</span>', color


def _mc(title, value, subtitle="", cls="mc", tooltip=""):
    """Render a metric card HTML div."""
    tip = f'title="{tooltip}"' if tooltip else ""
    return (f'<div class="{cls}" {tip} style="cursor:default">'
            f'<h3>{title}</h3><p>{value}</p>'
            + (f'<small>{subtitle}</small>' if subtitle else '')
            + '</div>')


def render_tab_scenarios(conn, b):
    st.subheader("📈 Rate Change Scenarios")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** first.")
        return

    if not st.session_state.get("sc_loaded_from_db"):
        db_rows = db_load_scenarios(conn)
        rcs = {}
        for i, sc_row in enumerate(db_rows, 1):
            for rn in sc_row.get("renewals",[]): rn.setdefault("onetime_amount",0)
            sc_row["_seq"] = i
            sc_row["_key"] = str(uuid.uuid4())[:8]
            rcs[sc_row["_key"]] = sc_row
        st.session_state.rc_scenarios  = rcs
        st.session_state.sc_loaded_from_db = True

    if not st.session_state.get("pp_sc_loaded"):
        pp_rows = db_load_prepay_scenarios(conn)
        pp_dict = {}
        for i, sc_row in enumerate(pp_rows, 1):
            sc_row["_seq"] = i; sc_row["_key"] = str(uuid.uuid4())[:8]
            pp_dict[sc_row["_key"]] = sc_row
        st.session_state.pp_scenarios = pp_dict
        st.session_state.pp_sc_loaded = True

    rcs: dict = st.session_state.rc_scenarios
    pps: dict = st.session_state.pp_scenarios

    # Build lookup: db_id → prepay scenario
    pps_by_dbid = {s["db_id"]: s for s in pps.values() if s.get("db_id")}

    # ── Base amortization ─────────────────────────────────────────
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
    base_total_paid    = s_base.get("total_paid", base_grand_total)

    # ── REQ #4+5: Base metrics ONCE (with total paid) ─────────────
    st.markdown("#### 📊 Base Mortgage — Reference Metrics")
    bm1,bm2,bm3,bm4,bm5 = st.columns(5)
    bm1.markdown(_mc("Remaining Amort.", f"{base_remaining_yrs:.1f} yrs",
                     cls="mc",
                     tooltip="Years left on mortgage from today at the current rate with no changes"),
                 unsafe_allow_html=True)
    bm2.markdown(_mc("Total Interest (base)", f"${base_total_int:,.0f}",
                     f"{_pct(base_total_int, base_grand_total)} of P+I", cls="mc mc-r",
                     tooltip="Total interest payable over the full remaining amortization at the current rate"),
                 unsafe_allow_html=True)
    bm3.markdown(_mc("Total Principal", f"${base_total_princ:,.0f}",
                     f"{_pct(base_total_princ, base_grand_total)} of P+I", cls="mc mc-b",
                     tooltip="Original mortgage principal (insured amount)"),
                 unsafe_allow_html=True)
    bm4.markdown(_mc("Balance Today", f"${today_bal:,.0f}",
                     cls="mc",
                     tooltip="Outstanding mortgage balance as of today"),
                 unsafe_allow_html=True)
    bm5.markdown(_mc("Total to be Paid (base)", f"${base_total_paid:,.0f}",  # REQ #5
                     f"P+I over full amort.", cls="mc",
                     tooltip="Total of all payments (principal + interest) over the full remaining amortization"),
                 unsafe_allow_html=True)

    st.divider()

    # ── Header ────────────────────────────────────────────────────
    hdr1, hdr2 = st.columns([5,2])
    hdr1.caption("Click **✏️ Edit** on any scenario card to open the editor popup (includes prepayment linking).")
    if hdr2.button("➕ New Scenario", key="btn_new_rc", use_container_width=True,
                    help="Create a new rate-change scenario"):
        nk = str(uuid.uuid4())[:8]
        next_seq = max((s.get("_seq",0) for s in rcs.values()), default=0) + 1
        rcs[nk] = {"_key":nk,"_seq":next_seq,"db_id":None,
                   "name":f"Scenario {next_seq}","desc":"",
                   "renewals":[],"user_pmt":0,"pp":{},"linked_pp_db_id":0}
        st.session_state["_editing_sc_id"] = nk
        st.rerun()

    sc_del     = []
    sorted_scs = sorted(rcs.items(), key=lambda kv: kv[1].get("_seq",999))

    for sc_id, sc in sorted_scs:
        renewals  = sc.get("renewals", [])
        seq       = sc.get("_seq","?")

        # REQ #1: use per-scenario linked PP
        linked_pp = _get_linked_pp(sc, pps_by_dbid)

        df_sc, s_sc, s_adj, adj_rem, adj_end, delta_yrs, colour, calc_monthly_sc, sc_term_end_p, last_rate = \
            compute_adj_scenario(sc, b, linked_pp, None, s_base)
        eff_pmt       = float(sc.get("user_pmt",0)) or calc_monthly_sc
        sc_total_int  = s_adj.get("total_interest", 0)
        sc_grand_total = sc_total_int + base_total_princ

        # REQ #2: delta arrows
        int_arrow_html, int_color = _arrow(sc_total_int, base_total_int, higher_is_worse=True)
        rem_arrow_html, rem_color = _arrow(adj_rem, base_remaining_yrs, higher_is_worse=True)
        int_delta_num  = sc_total_int - base_total_int
        rem_delta_num  = adj_rem - base_remaining_yrs
        int_pct_change = f"{int_delta_num/base_total_int*100:+.1f}%" if base_total_int else ""
        rem_pct_change = f"{rem_delta_num/base_remaining_yrs*100:+.1f}%" if base_remaining_yrs else ""

        rates_str  = " → ".join(f"{rn['new_rate']:.2f}%" for rn in renewals) or f"{b['annual_rate']:.2f}% (base)"
        lump_total = sum(float(rn.get("onetime_amount",0)) for rn in renewals)
        pp_label   = f"  ·  PP: {linked_pp['name']}" if linked_pp else ""
        badge      = "🟢" if sc.get("db_id") else "🔴"
        saved_str  = "✓ Saved" if sc.get("db_id") else "⚠ Unsaved"

        # ── Compact card ──────────────────────────────────────────
        with st.expander(
            f"{badge} **#{seq} — {sc['name']}**  ·  {saved_str}{pp_label}",
            expanded=False,
        ):
            ci1, ci2 = st.columns([3,1])
            ci1.markdown(
                f"**Rates:** {rates_str}  \n"
                f"**Renewals:** {len(renewals)}  ·  "
                f"**One-time lumps:** ${lump_total:,.0f}  \n"
                f"**Payment:** ${eff_pmt:,.2f}/period  ·  "
                f"**Linked PP:** {linked_pp['name'] if linked_pp else 'None'}"
            )
            if sc.get("desc"): ci1.caption(sc["desc"])
            btn1,btn2,btn3 = ci2.columns(3)
            if btn1.button("✏️", key=f"edit_{sc_id}", use_container_width=True, help="Edit this scenario"):
                st.session_state["_editing_sc_id"] = sc_id
            # REQ #3: Clone
            if btn2.button("📋", key=f"clone_{sc_id}", use_container_width=True,
                            help="Clone (copy) this scenario"):
                import copy
                nk       = str(uuid.uuid4())[:8]
                next_seq = max((s.get("_seq",0) for s in rcs.values()), default=0) + 1
                cloned   = copy.deepcopy(sc)
                cloned["_key"]  = nk
                cloned["_seq"]  = next_seq
                cloned["db_id"] = None
                cloned["name"]  = sc["name"] + " (Copy)"
                # Give each renewal a new id
                for rn in cloned.get("renewals",[]): rn["id"] = str(uuid.uuid4())[:8]
                rcs[nk] = cloned
                st.rerun()
            if btn3.button("🗑️", key=f"del_{sc_id}", use_container_width=True, help="Delete this scenario"):
                if sc.get("db_id"): db_delete_scenario(conn, sc["db_id"])
                sc_del.append(sc_id)

        # ── REQ #2: Metrics (no Adj. Principal; deltas with arrows) ──
        t1, t2, t3, t4 = st.columns(4)

        # Interest tile
        int_colour_cls = "mc-g" if sc_total_int < base_total_int else ("mc-r" if sc_total_int > base_total_int else "mc")
        int_sub = (f'{int_arrow_html} ${abs(int_delta_num):,.0f} ({int_pct_change})'
                   f'  ·  {_pct(sc_total_int, sc_grand_total)} of P+I')
        t1.markdown(_mc("Adj. Interest", f"${sc_total_int:,.0f}", int_sub,
                        cls=f"mc {int_colour_cls}",
                        tooltip=f"Total interest over remaining amortization with this scenario's rates"
                                f" and payment. Base: ${base_total_int:,.0f}"),
                    unsafe_allow_html=True)

        # Remaining tile
        rem_colour_cls = "mc-g" if adj_rem < base_remaining_yrs else ("mc-r" if adj_rem > base_remaining_yrs else "mc")
        rem_sub = f'{rem_arrow_html} {abs(rem_delta_num):.1f} yrs ({rem_pct_change})'
        t2.markdown(_mc("Adj. Remaining", f"{adj_rem:.1f} yrs", rem_sub,
                        cls=f"mc {rem_colour_cls}",
                        tooltip=f"Remaining amortization with adjusted payment. "
                                f"Base: {base_remaining_yrs:.1f} yrs"),
                    unsafe_allow_html=True)

        t3.markdown(_mc("Mortgage-free by", adj_end.strftime("%b %Y"),
                        f"at ${eff_pmt:,.2f}/period",
                        cls=f"mc {colour}",
                        tooltip=f"Projected payoff date at ${eff_pmt:,.2f}/period with {last_rate:.2f}% rate"),
                    unsafe_allow_html=True)
        t4.markdown(_mc("Last Rate", f"{last_rate:.2f}%",
                        f"{len(renewals)} renewal(s)",
                        cls="mc",
                        tooltip="Interest rate in the final renewal term of this scenario"),
                    unsafe_allow_html=True)

        # ── REQ #4: Rate-over-time chart — compact, no P&I chart ──
        if not df_sc.empty:
            fig_r = go.Figure()
            fig_r.add_scatter(x=df_sc["Date"], y=df_sc["Rate (%)"], fill="tozeroy",
                               line=dict(color="#27ae60", width=1.5), name="Rate (%)")
            fig_r.update_layout(title=f"#{seq} Rate over time",
                                 xaxis_title=None, yaxis_title="%",
                                 height=160, margin=dict(t=28,b=18,l=40,r=10),
                                 showlegend=False)
            fig_r.update_xaxes(tickformat="%Y")
            st.plotly_chart(fig_r, use_container_width=True, key=f"ch_rt_{sc_id}")

        st.divider()

    for k in sc_del: del rcs[k]
    if sc_del: st.rerun()

    editing_id = st.session_state.get("_editing_sc_id")
    if editing_id and editing_id in rcs and not st.session_state.get("_dialog_shown"):
        st.session_state["_dialog_shown"] = True
        edit_scenario_dialog()

    # ── Finalize Scenario ────────────────────────────────────────
    _render_finalize_section(conn, b, rcs)

    with st.expander("📚 Canadian Mortgage Education"):
        st.markdown(
            "**Semi-annual compounding**: `(1 + r/200)²`  \n"
            "**CMHC**: <10% = 4.00% · 10–15% = 3.10% · 15–20% = 2.80% · ≥20% = nil  \n"
            "**Break penalty**: Variable = 3 months interest · Fixed = max(3mo, IRD)"
        )


# ── Finalize helpers ──────────────────────────────────────────────

@st.dialog("⚠️ Finalize Scenario — Confirm", width="large")
def _finalize_confirm_dialog(conn, b, rcs, sc_key, finalized_rn):
    """Confirmation popup for finalizing a scenario's first renewal."""
    from modules import period_to_date
    rn    = finalized_rn
    sc    = rcs[sc_key]
    rn_start = _date_cls.fromisoformat(rn["date_str"]) if rn.get("date_str") else b["start_date"]
    rn_end   = rn_start + relativedelta(
        years=int(rn["term_years"]),
        months=int((float(rn["term_years"]) % 1) * 12),
    )

    st.markdown("### What will happen")
    st.markdown(
        f"**1. A new 'Additional Term' will be added to Setup:**  \n"
        f"   &nbsp;&nbsp;Start: **{rn_start.strftime('%b %d, %Y')}** · "
        f"Rate: **{rn['new_rate']:.2f}%** · "
        f"Type: **{rn.get('mtype','Fixed')}** · "
        f"Term: **{rn['term_years']} yrs** → ends **{rn_end.strftime('%b %d, %Y')}**"
    )
    st.markdown(
        f"**2. Scenario '{sc['name']}' first renewal will be removed** (it becomes the base term).  \n"
        f"**3. All OTHER scenarios:** any renewal starting before "
        f"**{rn_end.strftime('%b %d, %Y')}** will be removed (now in the past)."
    )
    st.markdown(
        f"**4. Setup will be auto-saved to DB** with the new additional term."
    )
    st.warning("⚠️ This action modifies the base Setup and all scenario renewal lists. It cannot be undone from the UI.")

    c1, c2 = st.columns(2)
    if c1.button("✅ Yes, Finalize", type="primary", use_container_width=True,
                  key="fin_confirm_yes"):
        _apply_finalize(conn, b, rcs, sc_key, rn, rn_end)
        st.session_state["_finalizing"] = False
        st.rerun()
    if c2.button("✕ Cancel", use_container_width=True, key="fin_confirm_no"):
        st.session_state["_finalizing"] = False
        st.rerun()


def _apply_finalize(conn, b, rcs, sc_key, finalized_rn, rn_end):
    """Apply the finalize: update past_renewals, purge outdated renewals from all scenarios."""
    from modules import date_to_period

    rn_start = _date_cls.fromisoformat(finalized_rn["date_str"])

    # ── 1. Add to past_renewals ───────────────────────────────────
    new_past_rn = {
        "id": str(uuid.uuid4())[:8],
        "start_date_str": str(rn_start),
        "rate": float(finalized_rn["new_rate"]),
        "mtype": finalized_rn.get("mtype", "Fixed"),
        "term_years": float(finalized_rn["term_years"]),
    }
    st.session_state.past_renewals.append(new_past_rn)

    # ── 2. Save updated Setup to DB ───────────────────────────────
    sd = st.session_state.setup_data or {}
    payload = {
        "widget_state": (sd.get("widget_state") or {}),
        "past_renewals": st.session_state.past_renewals,
        "past_prepayments": st.session_state.past_prepayments,
    }
    db_save_setup(conn, payload)
    st.session_state.setup_data = payload
    st.session_state.setup_loaded = True

    # ── 3. Remove finalized renewal from its scenario ─────────────
    sc = rcs.get(sc_key)
    if sc:
        sc["renewals"] = [
            rn for rn in sc.get("renewals", [])
            if rn["id"] != finalized_rn["id"]
        ]
        # Re-save to DB if already saved
        if sc.get("db_id"):
            pp_empty = {"annual_lump":0,"lump_month":1,"lump_start_year":1,"lump_num_years":0,
                        "pay_increase_type":"None","pay_increase_val":0,"onetime_period":0,"onetime_amount":0}
            db_save_scenario(conn, sc["db_id"], sc["name"], sc.get("desc",""),
                             sc["renewals"], pp_empty, sc.get("user_pmt",0), sc.get("linked_pp_db_id",0))

    # ── 4. Remove outdated renewals from ALL other scenarios ──────
    for key, sc2 in list(rcs.items()):
        if key == sc_key:
            continue
        before = len(sc2.get("renewals",[]))
        sc2["renewals"] = [
            rn for rn in sc2.get("renewals", [])
            if not rn.get("is_terminal") and
               _date_cls.fromisoformat(rn["date_str"]) >= rn_end
            or rn.get("is_terminal")
        ]
        if sc2.get("db_id") and len(sc2["renewals"]) != before:
            pp_empty = {"annual_lump":0,"lump_month":1,"lump_start_year":1,"lump_num_years":0,
                        "pay_increase_type":"None","pay_increase_val":0,"onetime_period":0,"onetime_amount":0}
            db_save_scenario(conn, sc2["db_id"], sc2["name"], sc2.get("desc",""),
                             sc2["renewals"], pp_empty, sc2.get("user_pmt",0), sc2.get("linked_pp_db_id",0))

    # ── 5. Force full reload on next render ───────────────────────
    st.session_state["sc_loaded_from_db"] = False
    st.success("✅ Finalized! Setup updated. Please go to **Setup & Overview** tab to confirm and re-save.")


def _render_finalize_section(conn, b, rcs):
    """Section at bottom of scenarios tab to pick and finalize a scenario."""
    st.divider()
    st.markdown("#### 🔒 Finalize a Scenario Term")
    st.caption(
        "Promote a scenario's **first renewal** to a confirmed base term. "
        "It will be added to **Setup → Additional Renewal Terms** and all other scenarios "
        "will have their now-past renewals removed automatically."
    )

    saved_scs = {k: v for k, v in rcs.items() if v.get("db_id") and v.get("renewals")}
    non_term_by_sc = {
        k: [rn for rn in v["renewals"] if not rn.get("is_terminal")]
        for k, v in saved_scs.items()
    }
    eligible = {k: v for k, v in non_term_by_sc.items() if v}

    if not eligible:
        st.info("No saved scenarios with renewals available to finalize.")
        return

    sc_labels = {k: f"#{rcs[k].get('_seq','?')} — {rcs[k]['name']}" for k in eligible}
    chosen_key = st.selectbox(
        "Select scenario to finalize:",
        list(eligible.keys()),
        format_func=lambda k: sc_labels[k],
        key="fin_sc_sel",
        help="The FIRST renewal in this scenario will become a confirmed base term in Setup",
    )

    chosen_sc   = rcs[chosen_key]
    first_rn    = eligible[chosen_key][0]
    rn_start    = _date_cls.fromisoformat(first_rn["date_str"]) if first_rn.get("date_str") else b["start_date"]
    rn_end_date = rn_start + relativedelta(
        years=int(first_rn["term_years"]),
        months=int((float(first_rn["term_years"]) % 1) * 12),
    )

    st.markdown(
        f'<div class="inf">'
        f'📋 First renewal of <b>{chosen_sc["name"]}</b>:  '
        f'Starts <b>{rn_start.strftime("%b %d, %Y")}</b> · '
        f'Rate <b>{first_rn["new_rate"]:.2f}%</b> ({first_rn.get("mtype","Fixed")}) · '
        f'Term <b>{first_rn["term_years"]} yrs</b> → ends <b>{rn_end_date.strftime("%b %d, %Y")}</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if st.button("🔒 Finalize this renewal as base term",
                  key="btn_finalize",
                  type="primary",
                  help="Opens a confirmation popup before making any changes"):
        st.session_state["_finalizing"]     = True
        st.session_state["_fin_sc_key"]     = chosen_key
        st.session_state["_fin_rn"]         = first_rn

    if (st.session_state.get("_finalizing")
            and st.session_state.get("_fin_sc_key")
            and not st.session_state.get("_dialog_shown")):
        fin_rn  = st.session_state["_fin_rn"]
        fin_key = st.session_state["_fin_sc_key"]
        if fin_key in rcs:
            st.session_state["_dialog_shown"] = True
            _finalize_confirm_dialog(conn, b, rcs, fin_key, fin_rn)
