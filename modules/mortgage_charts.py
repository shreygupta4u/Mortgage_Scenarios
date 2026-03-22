"""mortgage_charts.py — Plotly chart helpers."""
import numpy as np
import plotly.graph_objects as go
import pandas as pd


def _vline_x(v):
    try:
        return int(pd.Timestamp(v).timestamp() * 1000)
    except Exception:
        return v


def stacked_bar_pi(df, today_p, term_end_p, title="Principal & Interest"):
    """
    Combined yearly stacked bar — 3 colour-coded segments.
    FIX #6: legend moved to BOTTOM; annotations at top margin (no overlap).
    """
    df2 = df.copy()
    df2["Seg"] = np.where(df2["Period"] <= today_p, "past",
                   np.where(df2["Period"] <= term_end_p, "current", "post"))
    colours = {"past": ("#888888", "#bbbbbb"), "current": ("#1a3c5e", "#e74c3c"), "post": ("#6a8dab", "#d89090")}
    opacities = {"past": 0.8, "current": 1.0, "post": 0.55}
    seg_names = {"past": "Past", "current": "Current Term", "post": "Post-term (projected)"}
    fig = go.Figure()
    for seg in ["past", "current", "post"]:
        g = df2[df2["Seg"] == seg].groupby("CalYear").agg(P=("Principal", "sum"), I=("Interest", "sum")).reset_index()
        if g.empty: continue
        cp, ci_ = colours[seg]; op = opacities[seg]; nm = seg_names[seg]
        fig.add_bar(x=g["CalYear"].astype(str), y=g["P"], name=f"Principal — {nm}",
                    marker_color=cp, opacity=op, legendgroup=f"p_{seg}",
                    text=g["P"].apply(lambda v: f"${v/1000:.0f}k"),
                    textposition="inside", textfont_size=8)
        fig.add_bar(x=g["CalYear"].astype(str), y=g["I"], name=f"Interest — {nm}",
                    marker_color=ci_, opacity=op, legendgroup=f"i_{seg}",
                    text=g["I"].apply(lambda v: f"${v/1000:.0f}k"),
                    textposition="inside", textfont_size=8)

    # FIX #6: annotations at very top of plot area (above bars), well clear of legend at bottom
    annotations = []
    if today_p > 0:
        yr_row = df2[df2["Period"] == today_p]["CalYear"]
        if not yr_row.empty:
            annotations.append(dict(
                x=str(yr_row.iloc[0]), y=1.0, xref="x", yref="paper",
                text="▼ Today", showarrow=False,
                font=dict(color="#27ae60", size=11, family="Arial Bold"),
                xanchor="center", yanchor="bottom",
                bgcolor="rgba(39,174,96,0.12)", bordercolor="#27ae60", borderwidth=1,
            ))
    if 0 < term_end_p < len(df2):
        te_df = df2[df2["Period"] == min(term_end_p, len(df2) - 1)]
        if not te_df.empty:
            annotations.append(dict(
                x=str(te_df["CalYear"].iloc[0]), y=0.96, xref="x", yref="paper",
                text="▼ Term End", showarrow=False,
                font=dict(color="#f39c12", size=11, family="Arial Bold"),
                xanchor="center", yanchor="bottom",
                bgcolor="rgba(243,156,18,0.12)", bordercolor="#f39c12", borderwidth=1,
            ))

    fig.update_layout(
        barmode="stack",
        title=dict(text=title, y=0.98),
        xaxis_title="Year",
        yaxis_title="($)",
        height=400,
        # FIX #6: legend at bottom, not top — keeps chart top clear for annotations
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0,
                    font=dict(size=10)),
        margin=dict(t=50, b=100),
        annotations=annotations,
    )
    return fig
