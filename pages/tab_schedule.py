"""
pages/tab_schedule.py — Full Amortization Schedule tab.
Renamed from tab_amortization.py.
Function signature changed from render(tabs_list) → render_tab_schedule(conn, b).
Imports changed from flat → modules.X.
"""
import streamlit as st
from datetime import date

import plotly.graph_objects as go

from modules.mortgage_math import build_amortization, calc_pmt, _year_of
from modules.mortgage_charts import _vline_x
from modules.mortgage_db import db_load_scenarios
from pages.scenario_editor import compute_scenario, _get_linked_pp


def _build_schedule_df(sc, b):
    """Build the full amortization df for a scenario, honouring user_pmt as a
    fixed payment (not recalculated at rate renewals), consistent with the
    scenario page display."""
    pps_by_dbid = {
        s["db_id"]: s
        for s in st.session_state.get("pp_scenarios", {}).values()
        if s.get("db_id")
    }
    linked_pp = _get_linked_pp(sc, pps_by_dbid)

    _, _, all_rcs, sc_extra, last_rate, _ = compute_scenario(sc, b, linked_pp)

    today_p  = b["today_m"].get("period_today", 0)
    user_pmt = float(sc.get("user_pmt", 0))

    df, _ = build_amortization(
        b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
        accel=b["accel"], start_date=b["start_date"],
        extra_payments=sc_extra or None, rate_changes=all_rcs or None,
        fixed_pmt=user_pmt, fixed_pmt_from=max(today_p, 1),
    )
    return df


def render_tab_schedule(conn, b):
    st.subheader("📅 Full Amortization Schedule")
    if not b:
        st.info("⬅️ Complete **Setup & Overview** tab and click 💾 Save Setup to DB first.")
        return

    today_ym = date.today().strftime("%Y-%m")

    db_sc_list = db_load_scenarios(conn)
    sc_opts = ["Current Setup (base rates)"] + [s["name"] for s in db_sc_list]
    chosen_sc = st.selectbox(
        "Display schedule for:", sc_opts, key="sch_sc_sel",
        help="Choose a saved scenario to see how the schedule changes"
    )
    if chosen_sc == "Current Setup (base rates)":
        df_sch_full, _ = build_amortization(
            b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
            accel=b["accel"], start_date=b["start_date"],
            extra_payments=b.get("past_extra") or None,
            rate_changes=b.get("past_renewal_rcs") or None,
        )
    else:
        saved = next((s for s in db_sc_list if s["name"] == chosen_sc), None)
        if saved:
            df_sch_full = _build_schedule_df(saved, b)
        else:
            df_sch_full, _ = build_amortization(
                b["principal"], b["annual_rate"], b["n_py"], b["amort_years"],
                accel=b["accel"], start_date=b["start_date"],
                extra_payments=b.get("past_extra") or None,
                rate_changes=b.get("past_renewal_rcs") or None,
            )

    def get_ym(d):
        return d.strftime("%Y-%m") if hasattr(d, "strftime") else str(d)[:7]

    today_rows = df_sch_full[df_sch_full["Date"].apply(get_ym) == today_ym]
    today_period = int(today_rows["Period"].iloc[0]) if not today_rows.empty else None

    view_mode = st.selectbox(
        "View Mode",
        ["Hierarchy (default)", "All Periods", "Monthly Summary", "Yearly Summary"],
        key="sch_view_mode",
        help="Hierarchy groups into Past / Current (±4 months) / Future"
    )
    do_hl = st.checkbox("Highlight current month", True, key="sch_hl",
                        help="Highlight today's row in yellow")

    if view_mode == "Hierarchy (default)" and today_period:
        past_df    = df_sch_full[df_sch_full["Period"] < max(1, today_period - 4)].copy()
        current_df = df_sch_full[
            (df_sch_full["Period"] >= max(1, today_period - 4)) &
            (df_sch_full["Period"] <= today_period)
        ].copy()
        future_df  = df_sch_full[df_sch_full["Period"] > today_period].copy()

        # Past segment
        with st.expander(
            f"◀ PAST — {len(past_df)} payments (before recent history)",
            expanded=False
        ):
            if not past_df.empty:
                d2 = past_df[["CalYear", "Period", "Date",
                              "Payment", "Interest", "Principal",
                              "Balance", "Rate (%)"]].copy()
                d2["Date"] = d2["Date"].apply(
                    lambda d: d.strftime("%b %Y") if hasattr(d, "strftime") else str(d)[:7]
                )
                d2 = d2.rename(columns={"CalYear": "Year"})
                mc_ = [c for c in d2.columns if c not in ["Year", "Period", "Date", "Rate (%)"]]
                st.dataframe(
                    d2.style.format({c: "${:,.2f}" for c in mc_}),
                    use_container_width=True, height=min(80 + len(d2) * 28, 420)
                )

        # Current segment (expanded)
        bal_today = float(
            df_sch_full[df_sch_full["Period"] == today_period]["Balance"].iloc[0]
        ) if today_period else 0
        with st.expander(
            f"★ CURRENT — last 4 months + today ({date.today().strftime('%B %Y')}) "
            f"· Balance: ${bal_today:,.0f}",
            expanded=True
        ):
            if not current_df.empty:
                d3 = current_df[["Period", "Date", "Payment", "Interest",
                                 "Principal", "Balance", "Rate (%)", "Cum Interest"]].copy()
                d3["Date"] = d3["Date"].apply(
                    lambda d: d.strftime("%b %Y") if hasattr(d, "strftime") else str(d)[:7]
                )
                mc_ = [c for c in d3.columns if c not in ["Period", "Date", "Rate (%)"]]

                def _hl_c(row):
                    if do_hl and str(row.get("Date", ""))[:7] == today_ym:
                        return ["background:#FFF3CD;font-weight:bold"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    d3.style.apply(_hl_c, axis=1).format({c: "${:,.2f}" for c in mc_}),
                    use_container_width=True, height=min(60 + len(d3) * 35, 280)
                )

        # Future segment
        with st.expander(
            f"▶ FUTURE — {len(future_df)} remaining payments",
            expanded=False
        ):
            if not future_df.empty:
                future_df["CalYear"] = future_df["Date"].apply(_year_of)
                d4 = future_df[["CalYear", "Period", "Date", "Payment",
                                "Interest", "Principal", "Balance", "Rate (%)"]].copy()
                d4["Date"] = d4["Date"].apply(
                    lambda d: d.strftime("%b %Y") if hasattr(d, "strftime") else str(d)[:7]
                )
                d4 = d4.rename(columns={"CalYear": "Year"})
                mc_ = [c for c in d4.columns if c not in ["Year", "Period", "Date", "Rate (%)"]]

                def _hl_f(row):
                    return ["background:#e8f4fd;font-style:italic;color:#555"] * len(row)

                st.dataframe(
                    d4.style.apply(_hl_f, axis=1).format({c: "${:,.2f}" for c in mc_}),
                    use_container_width=True, height=min(80 + len(d4) * 28, 460)
                )

    else:
        # Flat table modes
        if view_mode == "Yearly Summary":
            disp = (
                df_sch_full
                .groupby("CalYear")
                .agg(
                    Payments=("Payment", "count"),
                    Total_Paid=("Total Paid", "sum"),
                    Interest=("Interest", "sum"),
                    Principal=("Principal", "sum"),
                    Ending_Balance=("Balance", "last"),
                )
                .reset_index()
            )
            disp.columns = ["Year", "Payments", "Total Paid", "Interest",
                             "Principal", "Ending Balance"]
            cur_y = date.today().year

            def _hl(row):
                yr = int(row.get("Year", 0))
                if do_hl and yr == cur_y:
                    return ["background:#FFF3CD;font-weight:bold"] * len(row)
                if yr > cur_y:
                    return ["background:#e8f4fd;font-style:italic;color:#666"] * len(row)
                return [""] * len(row)

        elif view_mode == "Monthly Summary" and b["n_py"] > 12:
            df_sch_full["YM"] = df_sch_full["Date"].apply(get_ym)
            disp = (
                df_sch_full
                .groupby("YM")
                .agg(
                    Total_Paid=("Total Paid", "sum"),
                    Interest=("Interest", "sum"),
                    Principal=("Principal", "sum"),
                    Ending_Balance=("Balance", "last"),
                )
                .reset_index()
            )

            def _hl(row):
                ym = str(row.get("YM", ""))
                if do_hl and ym == today_ym:
                    return ["background:#FFF3CD;font-weight:bold"] * len(row)
                if ym > today_ym:
                    return ["background:#e8f4fd;font-style:italic;color:#555"] * len(row)
                return [""] * len(row)

        else:
            show_all = st.checkbox("Show full schedule from start", False, key="sch_show_all",
                                   help="Default view starts near today's period")
            disp = df_sch_full[
                ["Period", "Date", "Payment", "Interest", "Principal",
                 "Balance", "Rate (%)", "Cum Interest"]
            ].copy()
            disp["Date"] = disp["Date"].apply(
                lambda d: d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            )
            if not show_all and today_period:
                disp = disp.iloc[max(0, today_period - 4):].reset_index(drop=True)

            def _hl(row):
                ds = str(row.get("Date", ""))[:7]
                if do_hl and ds == today_ym:
                    return ["background:#FFF3CD;font-weight:bold"] * len(row)
                if ds > today_ym:
                    return ["background:#e8f4fd;font-style:italic;color:#555"] * len(row)
                return [""] * len(row)

        mc_ = [
            c for c in disp.columns
            if c not in ["Period", "Year", "Payments", "YM", "Date", "Rate (%)"]
        ]
        st.dataframe(
            disp.style.apply(_hl, axis=1).format({c: "${:,.2f}" for c in mc_}),
            use_container_width=True, height=500
        )

    if today_period:
        bal_t = float(
            df_sch_full[df_sch_full["Period"] == today_period]["Balance"].iloc[0]
        )
        rem_p = len(df_sch_full) - today_period
        rem_y = round(rem_p / b["n_py"], 1)
        st.markdown(
            f'<div class="ok">🟡 Current: Period <b>{today_period}</b> '
            f'({date.today().strftime("%B %Y")}) · Balance: <b>${bal_t:,.0f}</b> · '
            f'Remaining: <b>{rem_y:.1f} yrs</b> · '
            f'<i style="color:#555">Blue-italic = future projections</i></div>',
            unsafe_allow_html=True
        )

    fig_bal = go.Figure()
    fig_bal.add_scatter(
        x=df_sch_full["Date"], y=df_sch_full["Balance"],
        fill="tozeroy", name="Balance", line=dict(color="#1a3c5e")
    )
    fig_bal.add_scatter(
        x=df_sch_full["Date"], y=df_sch_full["Cum Interest"],
        name="Cum Interest", line=dict(color="#e74c3c", dash="dash")
    )
    if today_period:
        td_d = df_sch_full[df_sch_full["Period"] == today_period]["Date"].iloc[0]
        fig_bal.add_vline(
            x=_vline_x(td_d), line_dash="dash", line_color="#27ae60",
            annotation_text="Today", annotation_position="top right"
        )
    fig_bal.update_layout(
        title=f"Balance & Cumulative Interest — {chosen_sc}",
        xaxis_title="Date", yaxis_title="($)",
        height=360, margin=dict(t=60, b=40)
    )
    st.plotly_chart(fig_bal, use_container_width=True, key="ch_sch_bal")
    st.download_button(
        "⬇️ Download CSV",
        df_sch_full.to_csv(index=False).encode(),
        "schedule.csv", "text/csv",
        help="Download the full amortization schedule as CSV"
    )
