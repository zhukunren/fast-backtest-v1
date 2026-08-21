"""Per-symbol orchestration and CLI for :mod:`tools.updata_data`."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import time
import datetime as dt
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

import pandas as pd
import tushare as ts
from joblib import delayed

_apply_symbol_meta: Any = None
_effective_max_workers: Any = None
_format_date_list: Any = None
_INDUSTRY_MAPS: Any = None
_joblib_parallel: Any = None
_log: Any = None
_NAME_MAPS: Any = None
_normalize_etf_basic_df: Any = None
_parse_tokens: Any = None
_resolve_ts_tokens: Any = None
_warm_symbol_meta: Any = None
API_MAX_CONCURRENCY: Any = None
CHECK_MISSING_GAPS: Any = None
DB_POOLS: Any = None
ETF_BASIC_FIELDS: Any = None
get_db_name: Any = None
get_effective_start_date: Any = None
get_open_trade_dates: Any = None
MISSING_SCAN_MAX_DAYS: Any = None
preflight_mysql_for_symbol_types: Any = None
prepare_bar_df: Any = None
pro: Any = None
PRO_BAR_ADJ: Any = None
read_empty_daily_dates: Any = None
read_existing_trade_dates: Any = None
run_step: Any = None
safe_table_name: Any = None
tqdm: Any = None
TRADE_CAL_EXCHANGE: Any = None
ts_call: Any = None
ts_call_range: Any = None
TS_MAX_ROWS_EXT: Any = None
write_df_to_mysql: Any = None

MYSQL_HOST: Any = None
MYSQL_PORT: Any = None
MYSQL_USER: Any = None
MYSQL_PWD: Any = None
MYSQL_CHARSET: Any = None
MYSQL_STOCK_DB: Any = None
MYSQL_INDEX_DB: Any = None
MYSQL_ETF_DB: Any = None
_CLI_TS_TOKENS: Any = None
TS_TOKENS: Any = None
_TS_POOL: Any = None
DRY_RUN: Any = None
_BOUND_RUNTIME_NAMESPACE: dict[str, Any] | None = None


@dataclass
class UpdateContext:
    symbol_code: str
    start_date: str
    end_date: str
    symbol_type: str
    db_name: str
    table: str
    end_ts: pd.Timestamp
    week_freq: str = "W-FRI"
    gap_info: dict | None = None


@dataclass(frozen=True)
class ExtendedDataSpec:
    name: str
    api_fn: object
    fields: str
    columns: tuple[str, ...]
    required: bool = False
    fail_on_empty: bool = True


_RUNTIME_BINDING_NAMES = (
    "_apply_symbol_meta", "_effective_max_workers", "_format_date_list", "_INDUSTRY_MAPS",
    "_joblib_parallel", "_log", "_NAME_MAPS", "_normalize_etf_basic_df", "_parse_tokens",
    "_resolve_ts_tokens", "_warm_symbol_meta", "API_MAX_CONCURRENCY", "CHECK_MISSING_GAPS",
    "DB_POOLS", "ETF_BASIC_FIELDS", "get_db_name", "get_effective_start_date", "get_open_trade_dates",
    "MISSING_SCAN_MAX_DAYS", "preflight_mysql_for_symbol_types", "prepare_bar_df", "pro", "PRO_BAR_ADJ",
    "read_empty_daily_dates", "read_existing_trade_dates", "run_step", "safe_table_name", "tqdm",
    "TRADE_CAL_EXCHANGE", "ts_call", "ts_call_range", "TS_MAX_ROWS_EXT", "write_df_to_mysql",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PWD", "MYSQL_CHARSET", "MYSQL_STOCK_DB",
    "MYSQL_INDEX_DB", "MYSQL_ETF_DB", "_CLI_TS_TOKENS", "TS_TOKENS", "_TS_POOL", "DRY_RUN",
)


def bind_runtime(namespace: dict[str, Any]) -> None:
    """Bind data-service dependencies once before invoking the runner."""
    missing = [name for name in _RUNTIME_BINDING_NAMES if name not in namespace]
    if missing:
        raise RuntimeError(f"update runner runtime bindings missing: {', '.join(missing)}")
    for name in _RUNTIME_BINDING_NAMES:
        globals()[name] = namespace[name]
    global _BOUND_RUNTIME_NAMESPACE
    _BOUND_RUNTIME_NAMESPACE = namespace


def _publish_runtime(*names: str) -> None:
    if _BOUND_RUNTIME_NAMESPACE is None:
        return
    _BOUND_RUNTIME_NAMESPACE.update({name: globals()[name] for name in names})

def inspect_missing_trade_dates(conn, db_name: str, table: str, ts_code: str,
                                symbol_type: str, user_start: str,
                                end_date: str, latest_dt: dt.date | None) -> dict:
    result = {
        'scan_start': None,
        'scan_end': None,
        'expected_count': 0,
        'existing_count': 0,
        'missing_dates': [],
        'empty_daily_dates': [],
        'suspended_dates': [],
    }
    if latest_dt is None or not CHECK_MISSING_GAPS:
        return result

    user_start_dt = datetime.strptime(user_start, '%Y%m%d').date()
    scan_end_dt = min(datetime.strptime(end_date, '%Y%m%d').date(), latest_dt)
    if user_start_dt > scan_end_dt:
        return result

    if MISSING_SCAN_MAX_DAYS > 0:
        lookback_start = scan_end_dt - timedelta(days=max(0, MISSING_SCAN_MAX_DAYS - 1))
        user_start_dt = max(user_start_dt, lookback_start)

    existing_dates = read_existing_trade_dates(
        conn,
        db_name,
        table,
        ts_code,
        user_start_dt.strftime('%Y%m%d'),
        scan_end_dt.strftime('%Y%m%d'),
    )
    if not existing_dates:
        return result

    scan_start_dt = max(user_start_dt, existing_dates[0])
    existing_dates = [d for d in existing_dates if d >= scan_start_dt]
    existing_set = set(existing_dates)

    open_dates_all = get_open_trade_dates(user_start, end_date, exchange=TRADE_CAL_EXCHANGE)
    open_dates = [d for d in open_dates_all if scan_start_dt <= d <= scan_end_dt]
    if not open_dates:
        return result

    missing_dates = [d for d in open_dates if d not in existing_set]
    empty_daily_dates = [
        d for d in read_empty_daily_dates(
            conn,
            db_name,
            table,
            ts_code,
            scan_start_dt.strftime('%Y%m%d'),
            scan_end_dt.strftime('%Y%m%d'),
        )
        if d in existing_set and d in set(open_dates)
    ]
    suspended_dates: list[dt.date] = []
    if missing_dates and symbol_type == 'stock':
        suspended_set = get_suspended_trade_dates(
            ts_code,
            missing_dates[0].strftime('%Y%m%d'),
            missing_dates[-1].strftime('%Y%m%d'),
        )
        if suspended_set:
            suspended_dates = [d for d in missing_dates if d in suspended_set]
            if suspended_dates:
                suspended_lookup = set(suspended_dates)
                missing_dates = [d for d in missing_dates if d not in suspended_lookup]

    result.update({
        'scan_start': scan_start_dt,
        'scan_end': scan_end_dt,
        'expected_count': len(open_dates),
        'existing_count': len(existing_dates),
        'missing_dates': sorted({*missing_dates, *empty_daily_dates}),
        'empty_daily_dates': empty_daily_dates,
        'suspended_dates': suspended_dates,
    })
    return result


def merge_on_trade_date(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    if right is None or right.empty:
        return left
    if left is None or left.empty:
        return right
    if 'trade_date' not in left.columns or 'trade_date' not in right.columns:
        return left

    right = right.copy()
    dup_cols = [c for c in right.columns if c != 'trade_date' and c in left.columns]
    if dup_cols:
        right = right.drop(columns=dup_cols)
    return pd.merge(left, right, on='trade_date', how='left')


def chunk_iter(seq, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]



def get_stock_list():
    df = ts_call(
        pro.stock_basic,
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,area,industry,list_date',
    )
    if df is None or df.empty:
        return []
    _apply_symbol_meta('stock', df, mark_warm=True)
    df = df.dropna(subset=['ts_code']).copy()
    df['ts_code'] = df['ts_code'].astype(str)
    return df['ts_code'].tolist()


def get_etf_list():
    df = _normalize_etf_basic_df(
        ts_call(pro.etf_basic, list_status='L', fields=ETF_BASIC_FIELDS)
    )
    if df is None or df.empty:
        return []
    _apply_symbol_meta('etf', df, mark_warm=True)
    df = df.dropna(subset=['ts_code']).copy()
    df['ts_code'] = df['ts_code'].astype(str)
    return df['ts_code'].tolist()


def get_index_list():
    df = ts_call(pro.index_basic, fields='ts_code,name')
    if df is None or df.empty:
        return []
    _apply_symbol_meta('index', df, mark_warm=True)
    df = df.dropna(subset=['ts_code']).copy()
    df['ts_code'] = df['ts_code'].astype(str)
    return df['ts_code'].tolist()


def _empty_gap_info() -> dict:
    return {
        'scan_start': None,
        'scan_end': None,
        'expected_count': 0,
        'existing_count': 0,
        'missing_dates': [],
        'empty_daily_dates': [],
        'suspended_dates': [],
    }


def _asset_for_symbol_type(symbol_type: str) -> str:
    return 'E' if symbol_type == 'stock' else ('I' if symbol_type == 'index' else 'FD')


def _create_update_context(symbol_code: str, start_date: str, end_date: str,
                           symbol_type: str, week_freq: str) -> UpdateContext:
    symbol_type = str(symbol_type).lower()
    if symbol_type not in ('stock', 'index', 'etf'):
        raise ValueError("symbol_type 必须是 'stock'、'index' 或 'etf'")

    end_ts = pd.to_datetime(end_date, format='%Y%m%d', errors='coerce')
    if pd.isna(end_ts):
        raise ValueError(f"end_date 无法解析：{end_date}")

    return UpdateContext(
        symbol_code=str(symbol_code).strip(),
        start_date=str(start_date).strip(),
        end_date=str(end_date).strip(),
        symbol_type=symbol_type,
        db_name=get_db_name(symbol_type),
        table=safe_table_name(str(symbol_code).strip()),
        end_ts=end_ts.normalize(),
        week_freq=week_freq,
        gap_info=_empty_gap_info(),
    )


def _log_gap_info(symbol_code: str, gap_info: dict):
    gap_dates = gap_info.get('missing_dates') or []
    if gap_dates:
        _log(
            f"[{symbol_code}] 缺口扫描 {gap_info['scan_start']:%Y-%m-%d}~{gap_info['scan_end']:%Y-%m-%d}："
            f"交易日 {gap_info['expected_count']}，库内 {gap_info['existing_count']}，"
            f"需回补 {len(gap_dates)} 个 -> {_format_date_list(gap_dates)}"
        )
    empty_daily_dates = gap_info.get('empty_daily_dates') or []
    if empty_daily_dates:
        _log(
            f"[{symbol_code}] 发现主行情空值 {len(empty_daily_dates)} 个交易日 -> "
            f"{_format_date_list(empty_daily_dates)}"
        )
    suspended_dates = gap_info.get('suspended_dates') or []
    if suspended_dates:
        _log(
            f"[{symbol_code}] 缺口中有 {len(suspended_dates)} 个停牌日已排除："
            f"{_format_date_list(suspended_dates)}"
        )


def _prepare_update_context(ctx: UpdateContext) -> UpdateContext | None:
    if DRY_RUN:
        return ctx

    conn = None
    try:
        if ctx.db_name not in DB_POOLS.pools:
            DB_POOLS.init_pool(ctx.db_name)
        conn = DB_POOLS.get(ctx.db_name)

        eff_start, latest_dt = get_effective_start_date(
            conn, ctx.db_name, ctx.table, ctx.symbol_code, ctx.start_date, week_freq=ctx.week_freq
        )
        gap_info = inspect_missing_trade_dates(
            conn, ctx.db_name, ctx.table, ctx.symbol_code, ctx.symbol_type,
            ctx.start_date, ctx.end_date, latest_dt
        )
        ctx.gap_info = gap_info
        gap_dates = gap_info.get('missing_dates') or []
        _log_gap_info(ctx.symbol_code, gap_info)

        if latest_dt is not None:
            latest_ts = pd.to_datetime(latest_dt).normalize()
            if latest_ts >= ctx.end_ts and not gap_dates:
                _log(
                    f"[{ctx.symbol_code}] 最新 trade_date={latest_ts:%Y-%m-%d} >= "
                    f"目标 end_date={ctx.end_ts:%Y-%m-%d}，且未发现缺口，跳过"
                )
                return None

        eff_start_dt = datetime.strptime(eff_start, '%Y%m%d').date()
        if gap_dates:
            eff_start_dt = min(eff_start_dt, gap_dates[0])
            _log(
                f"[{ctx.symbol_code}] 本次更新起点调整为 {eff_start_dt:%Y-%m-%d}"
                f"（增量起点 {eff_start}，最早缺口 {gap_dates[0]:%Y-%m-%d}）"
            )
        ctx.start_date = eff_start_dt.strftime('%Y%m%d')
        return ctx
    finally:
        if conn is not None:
            DB_POOLS.put(ctx.db_name, conn)


def _fetch_main_daily(ctx: UpdateContext) -> pd.DataFrame:
    kwargs = {
        'ts_code': ctx.symbol_code,
        'start_date': ctx.start_date,
        'end_date': ctx.end_date,
        'freq': 'D',
        'asset': _asset_for_symbol_type(ctx.symbol_type),
    }
    if PRO_BAR_ADJ:
        kwargs['adj'] = PRO_BAR_ADJ

    df_daily = prepare_bar_df(ts_call_range(ts.pro_bar, **kwargs))
    if df_daily.empty:
        return df_daily

    start_ts = pd.to_datetime(ctx.start_date, format='%Y%m%d', errors='coerce')
    if pd.notna(start_ts):
        df_daily = df_daily[df_daily['trade_date'] >= start_ts.normalize()]
    return df_daily[df_daily['trade_date'] <= ctx.end_ts]


def _attach_symbol_meta(ctx: UpdateContext, df_daily: pd.DataFrame) -> pd.DataFrame:
    _warm_symbol_meta(ctx.symbol_type)
    df_daily = df_daily.rename(columns={'vol': 'volume'}).copy()
    df_daily['name'] = _NAME_MAPS.get(ctx.symbol_type, {}).get(ctx.symbol_code, '未知')
    df_daily['industry'] = _INDUSTRY_MAPS.get(ctx.symbol_type, {}).get(ctx.symbol_code)
    df_daily['ts_code'] = ctx.symbol_code
    return df_daily


def _write_main_daily(ctx: UpdateContext, df_daily: pd.DataFrame) -> int:
    base_cols = [
        'ts_code', 'name', 'trade_date',
        'industry',
        'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg',
        'volume', 'amount',
    ]
    rows = run_step(
        ctx.symbol_code,
        "主行情写库",
        lambda: write_df_to_mysql(ctx.symbol_code, ctx.db_name, ctx.table, df_daily, base_cols),
        required=True,
    )
    _log(f"[{ctx.symbol_code}] 主行情更新完成，共 {rows} 条")
    return int(rows or 0)


def _verify_gap_backfill(ctx: UpdateContext):
    gap_dates = (ctx.gap_info or {}).get('missing_dates') or []
    if DRY_RUN or not gap_dates:
        return

    conn = None
    try:
        if ctx.db_name not in DB_POOLS.pools:
            DB_POOLS.init_pool(ctx.db_name)
        conn = DB_POOLS.get(ctx.db_name)
        persisted_dates = set(
            read_existing_trade_dates(
                conn,
                ctx.db_name,
                ctx.table,
                ctx.symbol_code,
                gap_dates[0].strftime('%Y%m%d'),
                gap_dates[-1].strftime('%Y%m%d'),
            )
        )
        remaining = [d for d in gap_dates if d not in persisted_dates]
        if remaining:
            _log(
                f"[{ctx.symbol_code}] 缺口回补后仍缺 {len(remaining)} 个交易日："
                f"{_format_date_list(remaining)}；可能是停牌或 TuShare 无主行情返回"
            )
        else:
            _log(f"[{ctx.symbol_code}] 检测到的 {len(gap_dates)} 个缺失交易日已补齐")
    finally:
        if conn is not None:
            DB_POOLS.put(ctx.db_name, conn)


def _stock_extended_specs() -> list[ExtendedDataSpec]:
    basic_fields = (
        'trade_date,'
        'turnover_rate,turnover_rate_f,volume_ratio,'
        'pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,'
        'total_share,float_share,free_share,total_mv,circ_mv'
    )
    mf_fields = (
        'trade_date,'
        'buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,'
        'buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,'
        'buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,'
        'buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,'
        'net_mf_vol,net_mf_amount'
    )
    bak_fields = (
        'trade_date,'
        'pct_change,vol_ratio,turn_over,swing,'
        'selling,buying,'
        'industry,area,'
        'float_mv,avg_price,'
        'strength,activity,avg_turnover,attack,interval_3,interval_6'
    )
    return [
        ExtendedDataSpec(
            "daily_basic",
            pro.daily_basic,
            basic_fields,
            ('ts_code', 'trade_date',
             'turnover_rate', 'turnover_rate_f', 'volume_ratio',
             'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
             'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv'),
        ),
        ExtendedDataSpec(
            "moneyflow",
            pro.moneyflow,
            mf_fields,
            ('ts_code', 'trade_date',
             'buy_sm_vol', 'buy_sm_amount', 'sell_sm_vol', 'sell_sm_amount',
             'buy_md_vol', 'buy_md_amount', 'sell_md_vol', 'sell_md_amount',
             'buy_lg_vol', 'buy_lg_amount', 'sell_lg_vol', 'sell_lg_amount',
             'buy_elg_vol', 'buy_elg_amount', 'sell_elg_vol', 'sell_elg_amount',
             'net_mf_vol', 'net_mf_amount'),
        ),
        ExtendedDataSpec(
            "adj_factor",
            pro.adj_factor,
            'trade_date,adj_factor',
            ('ts_code', 'trade_date', 'adj_factor'),
        ),
        ExtendedDataSpec(
            "stk_limit",
            pro.stk_limit,
            'trade_date,up_limit,down_limit',
            ('ts_code', 'trade_date', 'up_limit', 'down_limit'),
        ),
        ExtendedDataSpec(
            "suspend_d",
            pro.suspend_d,
            'trade_date,suspend_timing,suspend_type',
            ('ts_code', 'trade_date', 'suspend_timing', 'suspend_type'),
            fail_on_empty=False,
        ),
        ExtendedDataSpec(
            "margin_detail",
            pro.margin_detail,
            'trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl,rqmcl,rzrqye',
            ('ts_code', 'trade_date', 'rzye', 'rqye', 'rzmre', 'rqyl', 'rzche', 'rqchl', 'rqmcl', 'rzrqye'),
            fail_on_empty=False,
        ),
        ExtendedDataSpec(
            "bak_daily",
            pro.bak_daily,
            bak_fields,
            ('ts_code', 'trade_date',
             'pct_change', 'vol_ratio', 'turn_over', 'swing', 'selling', 'buying',
             'industry', 'area', 'float_mv', 'avg_price', 'strength', 'activity',
             'avg_turnover', 'attack', 'interval_3', 'interval_6'),
            fail_on_empty=False,
        ),
    ]


def fetch_and_write_extended_data(ctx: UpdateContext, base_dates: pd.DataFrame,
                                  spec: ExtendedDataSpec) -> int:
    df_api = normalize_trade_date_df(
        ts_call_range(
            spec.api_fn,
            ts_code=ctx.symbol_code,
            start_date=ctx.start_date,
            end_date=ctx.end_date,
            max_rows=TS_MAX_ROWS_EXT,
            fields=spec.fields,
        )
    )
    if df_api.empty:
        if spec.fail_on_empty:
            raise RuntimeError(f"{spec.name} 返回空")
        return 0

    df_out = merge_on_trade_date(base_dates, df_api)
    df_out['ts_code'] = ctx.symbol_code
    return write_df_to_mysql(ctx.symbol_code, ctx.db_name, ctx.table, df_out, list(spec.columns))


def _update_stock_extended_columns(ctx: UpdateContext, df_daily: pd.DataFrame):
    base_dates = df_daily[['trade_date']].copy()
    for spec in _stock_extended_specs():
        run_step(
            ctx.symbol_code,
            f"{spec.name} 写库",
            lambda spec=spec: fetch_and_write_extended_data(ctx, base_dates, spec),
            required=spec.required,
        )


def update_database(symbol_code, start_date, end_date, symbol_type='stock',
                    week_freq='W-FRI', align_periods=True):
    """
    更新单只标的的数据到 MySQL：
    1) 先更新 df_daily（主行情底表，仅写 TuShare 原始日线列）
    2) 底表写入成功后，再按接口顺序补充其它日频列（每步带重试）
    """
    ctx = _create_update_context(symbol_code, start_date, end_date, symbol_type, week_freq)
    ctx = _prepare_update_context(ctx)
    if ctx is None:
        return

    df_daily = _fetch_main_daily(ctx)
    if df_daily is None or df_daily.empty:
        _log(f"[{symbol_code}] 主行情为空")
        return

    df_daily = _attach_symbol_meta(ctx, df_daily)
    _write_main_daily(ctx, df_daily)
    _verify_gap_backfill(ctx)

    if ctx.symbol_type == 'stock':
        _update_stock_extended_columns(ctx, df_daily)


def update_with_retry(code, retries, start_date, end_date, symbol_type, week_freq='W-FRI'):
    """带重试机制的更新封装"""
    for i in range(1, retries + 1):
        try:
            update_database(code, start_date, end_date, symbol_type, week_freq=week_freq, align_periods=True)
            return None
        except Exception as e:
            _log(f"[{code}] 第{i}次失败：{e}，{5 * i}s后重试")
            time.sleep(5 * i)
    return code


def main(start_date, end_date,
         update_stock=True, update_index=False, update_etf=False,
         week_freq='W-FRI', max_workers=API_MAX_CONCURRENCY,
         code_lists: dict[str, list[str]] | None = None):
    """
    主入口：并行更新所选标的类型，并显示进度条。
    """
    requested_types = list(code_lists.keys()) if code_lists is not None else []
    if code_lists is None:
        if update_stock:
            requested_types.append('stock')
        if update_index:
            requested_types.append('index')
        if update_etf:
            requested_types.append('etf')
    preflight_mysql_for_symbol_types(requested_types)

    if CHECK_MISSING_GAPS:
        cal_dates = get_open_trade_dates(start_date, end_date, exchange=TRADE_CAL_EXCHANGE)
        if cal_dates:
            _log(
                f"[trade_cal] 已缓存 {start_date}~{end_date} 交易日历，"
                f"共 {len(cal_dates)} 个交易日（exchange={TRADE_CAL_EXCHANGE}）"
            )
        else:
            _log(
                f"[trade_cal] {start_date}~{end_date} 交易日历为空，"
                "本轮将退化为仅按最新日期做增量更新"
            )
    lists = {} if code_lists is None else {k: list(v) for k, v in code_lists.items()}
    if code_lists is None:
        if update_stock:
            lists['stock'] = get_stock_list()
        if update_index:
            #lists['index'] = get_index_list()
            lists['index'] = ['000001.SH', '000300.SH', '000905.SH', '399001.SZ', '399006.SZ']
        if update_etf:
            lists['etf'] = get_etf_list()
    for symbol_type, codes in lists.items():
        total = len(codes)
        if not total:
            _log(f"\n{symbol_type}: 无代码，跳过")
            continue

        _warm_symbol_meta(symbol_type)
        effective_workers = _effective_max_workers(max_workers, total)
        if effective_workers != max(1, int(max_workers)):
            _log(
                f"{symbol_type}: max_workers={max_workers} 已收敛到 {effective_workers} "
                f"（任务数={total}，API_MAX_CONCURRENCY={API_MAX_CONCURRENCY}）"
            )

        _log(f"\n开始更新 {symbol_type} 数据，总数：{total}")

        with tqdm(total=total, desc=f"{symbol_type} 更新中", unit="stk", dynamic_ncols=True, leave=True) as pbar:
            pbar_lock = Lock()

            def _one(code):
                res = update_with_retry(code, 3, start_date, end_date, symbol_type, week_freq)
                with pbar_lock:
                    pbar.update(1)
                return res

            results = _joblib_parallel(
                n_jobs=effective_workers,
                tasks=[delayed(_one)(code) for code in codes],
            )
            failed = [c for c in results if c]

        # 对失败的再重试一次（也显示进度）
        if failed:
            _log(f"{symbol_type}: 以下标的更新失败，正在重试（{len(failed)}）")
            with tqdm(total=len(failed), desc=f"{symbol_type} 失败重试", unit="stk", dynamic_ncols=True, leave=True) as pbar2:
                pbar2_lock = Lock()

                def _one_retry(code):
                    res = update_with_retry(code, 2, start_date, end_date, symbol_type, week_freq)
                    with pbar2_lock:
                        pbar2.update(1)
                    return res

                retry_results = _joblib_parallel(
                    n_jobs=effective_workers,
                    tasks=[delayed(_one_retry)(code) for code in failed],
                )
                still_failed = [c for c in retry_results if c]

            if still_failed:
                _log(f"{symbol_type}: 仍失败（{len(still_failed)}）-> 请手动排查：{still_failed}")
                _log(still_failed)
            else:
                _log(f"{symbol_type}: 重试后全部成功 OK")
        else:
            _log(f"{symbol_type}: OK 全部成功")

def _parse_yyyymmdd(s: str) -> str:
    s = str(s).strip()
    if not re.fullmatch(r"\d{8}", s):
        raise argparse.ArgumentTypeError("日期格式必须为 YYYYMMDD")
    return s


def _dedupe_cli_tokens(values: list[str]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        tokens.extend(_parse_tokens(value))
    seen: set[str] = set()
    return [token for token in tokens if token and (token not in seen and not seen.add(token))]


def _read_required_env_secret(env_name: str | None, option_name: str) -> str:
    name = str(env_name or "").strip()
    if not name:
        raise ValueError(f"{option_name} 需要指定环境变量名")
    value = os.environ.get(name)
    if value is None or value == "":
        raise ValueError(f"{option_name} 指定的环境变量 {name} 未设置或为空")
    return value


def _apply_cli_runtime_overrides(args: argparse.Namespace) -> None:
    global MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PWD, MYSQL_CHARSET
    global MYSQL_STOCK_DB, MYSQL_INDEX_DB, MYSQL_ETF_DB
    global _CLI_TS_TOKENS, TS_TOKENS, _TS_POOL

    if args.mysql_host:
        MYSQL_HOST = str(args.mysql_host).strip()
    if args.mysql_port is not None:
        MYSQL_PORT = int(args.mysql_port)
    if args.mysql_user:
        MYSQL_USER = str(args.mysql_user).strip()
    if args.mysql_charset:
        MYSQL_CHARSET = str(args.mysql_charset).strip()
    if args.mysql_stock_db:
        MYSQL_STOCK_DB = str(args.mysql_stock_db).strip()
    if args.mysql_index_db:
        MYSQL_INDEX_DB = str(args.mysql_index_db).strip()
    if args.mysql_etf_db:
        MYSQL_ETF_DB = str(args.mysql_etf_db).strip()

    if args.mysql_password is not None:
        MYSQL_PWD = str(args.mysql_password)
    elif args.mysql_password_env:
        MYSQL_PWD = _read_required_env_secret(args.mysql_password_env, "--mysql-password-env")
    elif args.mysql_password_prompt:
        MYSQL_PWD = getpass.getpass("MySQL password: ")

    token_values: list[str] | None = None
    if args.tushare_tokens is not None:
        token_values = [str(value) for value in args.tushare_tokens]
    elif args.tushare_token_env:
        token_values = [_read_required_env_secret(args.tushare_token_env, "--tushare-token-env")]
    elif args.tushare_token_prompt:
        token_values = [getpass.getpass("TuShare token: ")]

    if token_values is not None:
        tokens = _dedupe_cli_tokens(token_values)
        if not tokens:
            raise ValueError("TuShare token 为空")
        _CLI_TS_TOKENS = tokens
        TS_TOKENS = tokens
        _TS_POOL = None
    _publish_runtime(
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PWD",
        "MYSQL_CHARSET", "MYSQL_STOCK_DB", "MYSQL_INDEX_DB", "MYSQL_ETF_DB",
        "_CLI_TS_TOKENS", "TS_TOKENS", "_TS_POOL",
    )

def cli_main(argv: list[str] | None = None) -> int:
    """
    命令行入口：优先用于测试/小批量更新。
    - 不传 `--codes` 时：按 stock/index/etf 列表更新（同 main）。
    - 传 `--codes` 时：只更新指定代码（需配合 `--symbol-type`）。
    """
    global DRY_RUN

    p = argparse.ArgumentParser(prog="新版更新数据库.py")
    p.add_argument("--start-date", type=_parse_yyyymmdd, required=True, help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", type=_parse_yyyymmdd, required=True, help="结束日期 YYYYMMDD")
    p.add_argument("--week-freq", default="W-FRI", help="周频对齐方式（默认 W-FRI）")
    p.add_argument(
        "--max-workers",
        type=int,
        default=API_MAX_CONCURRENCY,
        help=f"并发线程数（默认 {API_MAX_CONCURRENCY}，实际不会超过 API_MAX_CONCURRENCY）",
    )
    p.add_argument("--dry-run", action="store_true", help="只拉取，不写 MySQL（也跳过读库判断）")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--stock", action="store_true", help="更新股票（全量）")
    g.add_argument("--index", action="store_true", help="更新指数（全量/示例列表）")
    g.add_argument("--etf", action="store_true", help="更新ETF（全量）")

    p.add_argument("--codes", nargs="*", default=None, help="仅更新指定 ts_code 列表（覆盖全量列表）")
    p.add_argument("--symbol-type", choices=["stock", "index", "etf"], default="stock", help="--codes 的标的类型（默认 stock）")

    mysql_group = p.add_argument_group("MySQL 运行时覆盖")
    mysql_group.add_argument("--mysql-host", help="MySQL 主机（覆盖配置文件）")
    mysql_group.add_argument("--mysql-port", type=int, help="MySQL 端口（覆盖配置文件）")
    mysql_group.add_argument("--mysql-user", help="MySQL 用户名（覆盖配置文件）")
    mysql_group.add_argument("--mysql-password", help="MySQL 密码（仅本次运行使用）")
    mysql_group.add_argument("--mysql-password-env", help="从指定环境变量读取 MySQL 密码")
    mysql_group.add_argument("--mysql-password-prompt", action="store_true", help="启动后交互式输入 MySQL 密码")
    mysql_group.add_argument("--mysql-charset", help="MySQL 字符集（覆盖配置文件）")
    mysql_group.add_argument("--mysql-stock-db", help="股票库名（覆盖配置文件）")
    mysql_group.add_argument("--mysql-index-db", help="指数库名（覆盖配置文件）")
    mysql_group.add_argument("--mysql-etf-db", help="ETF 库名（覆盖 MYSQL_ETF_DB/默认 etf_data）")

    tushare_group = p.add_argument_group("TuShare 运行时覆盖")
    tushare_group.add_argument(
        "--tushare-token",
        "--ts-token",
        dest="tushare_tokens",
        action="append",
        default=None,
        metavar="TOKEN",
        help="TuShare token；多个 token 可重复传参，或用逗号/空格/分号分隔",
    )
    tushare_group.add_argument("--tushare-token-env", help="从指定环境变量读取 TuShare token")
    tushare_group.add_argument("--tushare-token-prompt", action="store_true", help="启动后交互式输入 TuShare token")

    args = p.parse_args(argv)

    try:
        _apply_cli_runtime_overrides(args)
    except ValueError as exc:
        p.error(str(exc))

    if args.dry_run:
        DRY_RUN = True
        _publish_runtime("DRY_RUN")

    if args.codes:
        code_lists = {args.symbol_type: [str(c).strip() for c in args.codes if c and str(c).strip()]}
        main(
            start_date=args.start_date,
            end_date=args.end_date,
            update_stock=False,
            update_index=False,
            update_etf=False,
            week_freq=args.week_freq,
            max_workers=args.max_workers,
            code_lists=code_lists,
        )
        return 0

    update_stock = True
    update_index = False
    update_etf = False
    if args.stock or args.index or args.etf:
        update_stock = bool(args.stock)
        update_index = bool(args.index)
        update_etf = bool(args.etf)

    main(
        start_date=args.start_date,
        end_date=args.end_date,
        update_stock=update_stock,
        update_index=update_index,
        update_etf=update_etf,
        week_freq=args.week_freq,
        max_workers=args.max_workers,
    )
    return 0

def normalize_trade_date_df(dfx: pd.DataFrame) -> pd.DataFrame:
    """
    将 TuShare “日频风格” DataFrame 规范化：
    - trade_date: YYYYMMDD -> datetime64（归一到 00:00:00）
    - 去重/排序：按 trade_date 取 last
    """
    if dfx is None or dfx.empty or 'trade_date' not in dfx.columns:
        return pd.DataFrame()
    dfx = dfx.copy()
    dfx['trade_date'] = pd.to_datetime(dfx['trade_date'], format='%Y%m%d', errors='coerce').dt.normalize()
    dfx = dfx.dropna(subset=['trade_date'])
    dfx = dfx.sort_values('trade_date').drop_duplicates(subset=['trade_date'], keep='last')
    return dfx


def get_suspended_trade_dates(symbol_code: str, start_date: str, end_date: str) -> set[dt.date]:
    df_suspend = normalize_trade_date_df(
        ts_call_range(
            pro.suspend_d,
            ts_code=symbol_code,
            start_date=start_date,
            end_date=end_date,
            max_rows=TS_MAX_ROWS_EXT,
            fields='trade_date',
        )
    )
    if df_suspend.empty:
        return set()
    return {d.date() for d in df_suspend['trade_date'].tolist() if pd.notna(d)}
