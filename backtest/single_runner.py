"""
Single-stock run mode.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.logging import log_warn
from backtest.netvalue.plot import show_plotly_figure
from strategies.protocol import SingleSignalStrategyLike

from .engine_one_stock import backtest_one_stock


def run_single_stock(
    *,
    cfg,
    spec,
    start_time: str,
    end_time: str,
    strategy: SingleSignalStrategyLike | None = None,
    execution_policy,
    write_outputs: bool = False,
    logger=None,
) -> pd.DataFrame | None:
    stock_code = cfg.backtest.default_stock_code
    payload = backtest_one_stock(
        stock_code,
        start_time,
        end_time,
        strategy=strategy,
        compute_metrics=True,
        return_payload=True,
        execution_policy=execution_policy,
        strategy_module_name=str(cfg.strategy.module),
        cfg=cfg,
        spec=spec,
        write_outputs=write_outputs,
    )
    if payload is None or not isinstance(payload, dict):
        return None

    equity_curve = payload.get("equity_curve")
    if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty:
        return None
    if "equity" not in equity_curve.columns:
        raise DataValidationError("single 模式缺少 equity_curve.equity，无法生成净值曲线。")

    if "trade_date" in equity_curve.columns:
        trade_date = ensure_trade_date_normalized(equity_curve, col="trade_date")["trade_date"]
    elif isinstance(equity_curve.index, pd.DatetimeIndex):
        trade_date = pd.to_datetime(equity_curve.index, errors="raise").normalize()
    else:
        raise DataValidationError("single 模式 equity_curve 缺少 trade_date（列或 DatetimeIndex）。")

    out = pd.DataFrame(
        {
            "trade_date": trade_date,
            "sum_net_value": pd.to_numeric(equity_curve["equity"], errors="raise").to_numpy(dtype="float64"),
        }
    )
    if bool(cfg.run.plot_net_value):
        _plot_single_stock_outputs(
            cfg=cfg,
            stock_code=str(stock_code),
            equity_curve=equity_curve,
            trades_df=payload.get("trades_df"),
            price_df=payload.get("price_df"),
            logger=logger,
        )
    return out


def _plot_single_stock_outputs(
    *,
    cfg,
    stock_code: str,
    equity_curve: pd.DataFrame,
    trades_df: object,
    price_df: object,
    logger=None,
) -> None:
    if "equity" in equity_curve.columns:
        if "trade_date" in equity_curve.columns:
            trade_dates = ensure_trade_date_normalized(equity_curve, col="trade_date")["trade_date"]
        elif isinstance(equity_curve.index, pd.DatetimeIndex):
            trade_dates = pd.to_datetime(equity_curve.index, errors="raise").normalize()
        else:
            trade_dates = None
        if trade_dates is not None:
            nv_df = pd.DataFrame(
                {
                    "trade_date": trade_dates,
                    "sum_net_value": pd.to_numeric(equity_curve["equity"], errors="raise").to_numpy(dtype="float64"),
                }
            )
            if not nv_df.empty:
                from .netvalue.plot import plot_net_value

                plot_net_value(nv_df, show_fn=show_plotly_figure)

    if not isinstance(price_df, pd.DataFrame) or price_df.empty:
        return
    if not {"trade_date", "open", "high", "low", "close"}.issubset(price_df.columns):
        return
    try:
        import plotly.graph_objects as go
    except Exception as e:
        log_warn(f"single 信号图绘制失败：plotly 不可用 ({e})", logger)
        return

    sig_df = price_df.copy()
    sig_df = ensure_trade_date_normalized(sig_df, col="trade_date")
    sig_df = sig_df.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        sig_df[col] = pd.to_numeric(sig_df[col], errors="coerce")

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=sig_df["trade_date"],
            open=sig_df["open"],
            high=sig_df["high"],
            low=sig_df["low"],
            close=sig_df["close"],
            name="K线",
        )
    )

    plotted_trade_markers = False
    if isinstance(trades_df, pd.DataFrame) and not trades_df.empty:
        buy_dates = pd.to_datetime(trades_df["买入日"], errors="coerce")
        sell_dates = pd.to_datetime(trades_df["卖出日"], errors="coerce")
        buy_prices = pd.to_numeric(trades_df["买入价"], errors="coerce")
        sell_prices = pd.to_numeric(trades_df["卖出价"], errors="coerce")

        fig.add_trace(
            go.Scatter(
                x=buy_dates,
                y=buy_prices,
                mode="markers",
                name="buy",
                marker={"symbol": "triangle-up", "size": 9, "color": "green"},
                text=[f"buy {price:.2f}" if pd.notna(price) else "buy" for price in buy_prices],
            )
        )
        fig.add_trace(
            go.Scatter(
                x=sell_dates,
                y=sell_prices,
                mode="markers",
                name="sell",
                marker={"symbol": "triangle-down", "size": 9, "color": "red"},
                text=[f"sell {price:.2f}" if pd.notna(price) else "sell" for price in sell_prices],
            )
        )
        plotted_trade_markers = True

    if not plotted_trade_markers and "direction" in sig_df.columns:
        sig_df["direction"] = sig_df["direction"].astype(str).str.strip().str.lower()
        buy_mask = sig_df["direction"] == "buy"
        if bool(buy_mask.any()):
            fig.add_trace(
                go.Scatter(
                    x=sig_df.loc[buy_mask, "trade_date"],
                    y=sig_df.loc[buy_mask, "close"],
                    mode="markers",
                    name="buy_signal",
                    marker={"symbol": "triangle-up", "size": 9, "color": "green"},
                )
            )
        sell_mask = sig_df["direction"] == "sell"
        if bool(sell_mask.any()):
            fig.add_trace(
                go.Scatter(
                    x=sig_df.loc[sell_mask, "trade_date"],
                    y=sig_df.loc[sell_mask, "close"],
                    mode="markers",
                    name="sell_signal",
                    marker={"symbol": "triangle-down", "size": 9, "color": "red"},
                )
            )

    repo_root = getattr(cfg.paths, "repo_root", None)
    if repo_root:
        output_root = Path(repo_root) / "output" / "tools"
    else:
        signal_data_root = getattr(cfg.paths, "signal_data", None)
        output_root = Path(signal_data_root).parent if signal_data_root else Path.cwd()
    plot_dir = output_root / "single_plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plot_slug = stock_code.replace(".", "_")
    html_path = plot_dir / f"{plot_slug}_candlestick_signals.html"

    fig.update_layout(
        title=f"{stock_code} K线与买卖点",
        xaxis_title="trade_date",
        yaxis_title="price",
        template="plotly",
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
    )
    fig.write_html(str(html_path), include_plotlyjs="cdn")
    show_plotly_figure(fig)
