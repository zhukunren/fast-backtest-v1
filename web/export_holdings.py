"""Build daily holding snapshots and summaries for web exports."""
from __future__ import annotations

from typing import Any

from backtest.result import RunResult
from web.export_common import _normalize_initial_asset_hint
from web.holdings import broker, common, slot_like


def _accounting_context(
    artifacts: RunResult,
    *,
    ordered_dates: list[str],
) -> tuple[
    dict[str, dict[tuple[str, str], dict[str, Any]]],
    dict[str, float],
]:
    states_by_date, realized_cum_by_date = broker.build_fill_accounting_snapshots(
        artifacts,
        ordered_dates=ordered_dates,
    )
    if not states_by_date:
        states_by_date = slot_like.build_cost_snapshots(artifacts)
    return states_by_date, realized_cum_by_date


def build_daily_holdings_payload(
    artifacts: RunResult,
    *,
    initial_asset_hint: Any = None,
) -> dict[str, Any] | None:
    initial_asset = _normalize_initial_asset_hint(initial_asset_hint)
    slot_holdings = slot_like.build_daily_holdings(
        artifacts,
        initial_asset_hint=initial_asset,
    )
    if slot_holdings:
        ordered_dates = common.resolve_holding_dates(
            artifacts=artifacts,
            holdings_by_date=slot_holdings,
            summary_by_date={},
        )
        states_by_date, realized_cum_by_date = _accounting_context(
            artifacts,
            ordered_dates=ordered_dates,
        )
        enriched_holdings = common.enrich_holdings_rows(
            holdings_by_date=slot_holdings,
            ordered_dates=ordered_dates,
            states_by_date=states_by_date,
        )
        summary_by_date = slot_like.build_daily_summary(
            artifacts,
            holdings_by_date=enriched_holdings,
            realized_cum_by_date=realized_cum_by_date,
            initial_asset_hint=initial_asset,
        )
        return common.assemble_daily_holdings_payload(
            artifacts,
            holdings_by_date=enriched_holdings,
            summary_by_date=summary_by_date,
        )

    broker_holdings = broker.build_daily_holdings(artifacts)
    if not broker_holdings:
        return None
    ordered_dates = common.resolve_holding_dates(
        artifacts=artifacts,
        holdings_by_date=broker_holdings,
        summary_by_date={},
    )
    states_by_date, realized_cum_by_date = _accounting_context(
        artifacts,
        ordered_dates=ordered_dates,
    )
    enriched_holdings = common.enrich_holdings_rows(
        holdings_by_date=broker_holdings,
        ordered_dates=ordered_dates,
        states_by_date=states_by_date,
    )
    summary_by_date = broker.build_daily_summary(
        artifacts,
        holdings_by_date=enriched_holdings,
        realized_cum_by_date=realized_cum_by_date,
    )
    return common.assemble_daily_holdings_payload(
        artifacts,
        holdings_by_date=enriched_holdings,
        summary_by_date=summary_by_date,
    )
