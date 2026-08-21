"""
Net value plotting helpers.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Plotly renderer helpers (merged from core/plotly_renderer.py)
# ---------------------------------------------------------------------------

DEFAULT_PLOTLY_RENDERER = "browser"


def configure_plotly_renderer(default_renderer: str = DEFAULT_PLOTLY_RENDERER) -> str | None:
    try:
        import plotly.io as pio
    except Exception:
        return None

    env_renderer = os.environ.get("PLOTLY_RENDERER", "").strip()
    renderer = env_renderer or default_renderer
    if pio.renderers.default != renderer:
        pio.renderers.default = renderer
    return renderer


def show_plotly_figure(fig: Any, default_renderer: str = DEFAULT_PLOTLY_RENDERER) -> str | None:
    renderer = configure_plotly_renderer(default_renderer)
    # Benchmark and other explicitly headless callers still need the figure
    # to be built, but must not open a browser or emit a large renderer payload
    # into a captured worker pipe.  The default remains the historical display
    # behavior unless the caller opts out through the environment.
    show_env = os.environ.get("PLOTLY_SHOW", "").strip().casefold()
    if show_env in {"0", "false", "no", "off"}:
        return renderer
    try:
        fig.show()
    except Exception:
        return renderer
    return renderer


# ---------------------------------------------------------------------------
# Net value chart
# ---------------------------------------------------------------------------

def plot_net_value(df, win_rates=None, show_fn=show_plotly_figure):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    df = df.copy()
    df["trade_date_str"] = pd.to_datetime(df["trade_date"]).dt.strftime("%y-%m-%d")

    if "utilization_rate" in df.columns and df["utilization_rate"].max() > 1:
        df["utilization_rate"] = df["utilization_rate"] / 100.0

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.05,
        specs=[[{}], [{"secondary_y": True}]],
        subplot_titles=("净值走势图", "仓位/市值"),
    )

    position_col = None
    if "avg_position_value" in df.columns:
        position_col = "avg_position_value"
    elif "position_value" in df.columns:
        position_col = "position_value"

    for col in df.columns:
        if col not in ("trade_date", "trade_date_str", "utilization_rate", "position_value", "avg_position_value"):
            if isinstance(win_rates, dict) and "日胜率" in win_rates:
                legend_name = col
            else:
                if isinstance(win_rates, dict) and col in win_rates:
                    raw = win_rates[col]
                    rate = raw * 100 if raw <= 1 else raw
                    legend_name = f"{col} | 胜率 {rate:.1f}%"
                else:
                    legend_name = col

            fig.add_trace(
                go.Scatter(
                    x=df["trade_date_str"],
                    y=df[col],
                    mode="lines",
                    name=legend_name,
                ),
                row=1,
                col=1,
            )

    if position_col is not None:
        fig.add_trace(
            go.Bar(
                x=df["trade_date_str"],
                y=df[position_col],
                name="持仓市值",
                opacity=0.6,
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
        if "utilization_rate" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["trade_date_str"],
                    y=df["utilization_rate"],
                    mode="lines",
                    name="资金利用率",
                ),
                row=2,
                col=1,
                secondary_y=True,
            )
    elif "utilization_rate" in df.columns:
        fig.add_trace(
            go.Bar(
                x=df["trade_date_str"],
                y=df["utilization_rate"],
                name="资金利用率",
                opacity=0.6,
            ),
            row=2,
            col=1,
            secondary_y=False,
        )

    main_title = "净值与资金利用率"
    subtitle = ""
    if isinstance(win_rates, dict):
        if "日胜率" in win_rates:
            parts = []
            for key in ("日胜率", "周胜率", "月胜率", "最大回撤"):
                if key in win_rates:
                    parts.append(f"{key}: {win_rates[key]:.2f}%")
            subtitle = " | ".join(parts)
        else:
            parts = []
            for k, raw in win_rates.items():
                rate = raw * 100 if raw <= 1 else raw
                parts.append(f"{k}: {rate:.1f}%")
            subtitle = " | ".join(parts)

    title_text = f"{main_title}<br><sup>{subtitle}</sup>" if subtitle else main_title

    fig.update_layout(
        title=dict(text=title_text, x=0.5),
        template="plotly",
        hovermode="x unified",
        showlegend=True,
    )

    n = len(df)
    step = max(1, n // 10)
    tickvals = df["trade_date_str"].iloc[::step].tolist()

    fig.update_xaxes(
        title_text="日期",
        type="category",
        tickmode="array",
        tickvals=tickvals,
    )

    fig.update_yaxes(title_text="净值", row=1, col=1)
    fig.update_yaxes(title_text="市值", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="资金利用率", tickformat=".0%", row=2, col=1, secondary_y=True)

    if show_fn is not None:
        show_fn(fig)


__all__ = ["plot_net_value", "configure_plotly_renderer", "show_plotly_figure"]
