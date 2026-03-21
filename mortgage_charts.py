"""
mortgage_charts.py — Plotly chart helpers for Canadian Mortgage Analyzer
FIX #6: Legend moved to bottom; annotations at top no longer overlap.
"""
import numpy as np
import plotly.graph_objects as go


def _vline_x(v):
    try:
        import pandas as _pd
        return int(_pd.Timestamp(v).timestamp() * 1000)
    except Exception:
        return v


def _year_of(d) -> int:
    if hasattr(d, "year"):
        return d.year
    try:
        import pandas as _pd
        return _pd.Timestamp(d).year
    except Exception:
        return 0


def stacked_bar_pi(df, today_p: int, term_end_p: int,
                   title: str = "Principal & Interest") -> go.Figure:
    """
    Combined yearly stacked bar — 3 colour-coded segments.
    Past = grey, Current term = blue/red, Post-term = faded.

    FIX #6: Legend placed at BOTTOM so top margin is free for
    'Today' and 'Term End' annotations without any overlap.
    Annotations use y ≥ 1.04 (paper coords) above the plot area.
    """
    df2 = df.copy()
    df2["Seg"] = np.where(
        df2["Period"] <= today_p, "past",
        np.where(df2["Period"] <= term_end_p, "current", "post")
    )

    colours  = {"past": ("#888888", "#bbbbbb"),
                "current": ("#1a3c5e", "#e74c3c"),
                "post": ("#6a8dab", "#d89090")}
    opacities = {"past": 0.8, "current": 1.0, "post": 0.55}
    seg_names = {"past": "Past",
                 "current": "Current Term",
                 "post": "Post-term (projected)"}

    fig = go.Figure()
    for seg in ["past", "current", "post"]:
        g = (df2[df2["Seg"] == seg]
             .groupby("CalYear")
             .agg(P=("Principal", "sum"), I=("Interest", "sum"))
             .reset_index())
        if g.empty:
            continue
        cp, ci_ = colours[seg]
        op = opacities[seg]
        nm = seg_names[seg]

        fig.add_bar(
            x=g["CalYear"].astype(str), y=g["P"],
            name=f"Principal — {nm}",
            marker_color=cp, opacity=op, legendgroup=f"p_{seg}",
            text=g["P"].apply(lambda v: f"${v/1000:.0f}k"),
            textposition="inside", textfont_size=8,
        )
        fig.add_bar(
            x=g["CalYear"].astype(str), y=g["I"],
            name=f"Interest — {nm}",
            marker_color=ci_, opacity=op, legendgroup=f"i_{seg}",
            text=g["I"].apply(lambda v: f"${v/1000:.0f}k"),
            textposition="inside", textfont_size=8,
        )

    # ── Annotations: pushed to top of chart area, well above legend ──
    # Legend is now at bottom → annotations at y=1.04 / y=1.09 are clear
    if today_p > 0:
        yr_row = df2[df2["Period"] == today_p]["CalYear"]
        if not yr_row.empty:
            fig.add_annotation(
                x=str(yr_row.iloc[0]), y=1.06,
                xref="x", yref="paper",
                text="▼ Today",
                showarrow=False,
                font=dict(color="#27ae60", size=10, family="Arial Bold"),
                xanchor="center",
                bgcolor="rgba(255,255,255,0.75)",
                borderpad=2,
            )

    if 0 < term_end_p < len(df2):
        te_df = df2[df2["Period"] == min(term_end_p, len(df2) - 1)]
        if not te_df.empty:
            fig.add_annotation(
                x=str(te_df["CalYear"].iloc[0]), y=1.13,
                xref="x", yref="paper",
                text="▼ Term End",
                showarrow=False,
                font=dict(color="#f39c12", size=10, family="Arial Bold"),
                xanchor="center",
                bgcolor="rgba(255,255,255,0.75)",
                borderpad=2,
            )

    fig.update_layout(
        barmode="stack",
        title=dict(text=title, x=0.5, xanchor="center"),
        xaxis_title="Year",
        yaxis_title="($)",
        height=430,
        # FIX #6: Legend at BOTTOM — frees top margin for annotations
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.22,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
        ),
        margin=dict(t=55, b=115),
    )
    return fig
