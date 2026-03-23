"""pages/tab_scenarios.py — Rate Change Scenarios (REQ #1–6)."""
import streamlit as st
import plotly.graph_objects as go
from datetime import date
import uuid

from modules import (
    calc_pmt, build_amortization,
    db_load_scenarios, db_delete_scenario,
    db_load_prepay_scenarios,
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

    with st.expander("📚 Canadian Mortgage Education"):
        st.markdown(
            "**Semi-annual compounding**: `(1 + r/200)²`  \n"
            "**CMHC**: <10% = 4.00% · 10–15% = 3.10% · 15–20% = 2.80% · ≥20% = nil  \n"
            "**Break penalty**: Variable = 3 months interest · Fixed = max(3mo, IRD)"
        )
