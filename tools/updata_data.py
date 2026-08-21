# -*- coding: utf-8 -*-
"""
新版更新脚本（TuShare）

更新顺序（单标的）：
1) 先用 `ts.pro_bar` 更新主行情底表（df_daily：OHLCV/amount 等 TuShare 原始日线列）。
2) 主行情写库成功后，再依次调用其它日频接口补充扩展列（daily_basic/moneyflow/adj_factor/...）。

失败/重试策略：
- 每个 step 独立重试（`STEP_RETRY_TIMES/STEP_RETRY_DELAY`）。
- 主行情 step 为 required：失败会中断该标的更新，并触发外层标的级别重试。
- 扩展列 step：重试耗尽后跳过该 step，继续后续 step；已成功写入的数据保留。
- Upsert 默认不会用 NULL 覆盖旧值（`IFNULL(VALUES(col), col)`），避免“部分接口缺数据”造成回写清空。
"""

import os
import re
import sys
import time
import functools
import io
import warnings
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Semaphore, Lock, current_thread

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import get_config  # noqa: E402
from data.tushare_client import create_tushare_client, redact_tushare_error  # noqa: E402
from tools import update_data_runner as _update_runner  # noqa: E402

# 过滤 TuShare 内部的 pandas FutureWarning（fillna method 参数已弃用）
warnings.filterwarnings('ignore', message='Series.fillna with \'method\' is deprecated')
warnings.filterwarnings('ignore', category=FutureWarning, module='tushare')

import pandas as pd
import tushare as ts
try:
    from joblib import Parallel
except Exception as e:
    raise RuntimeError("需要安装 joblib 才能运行并发：pip install joblib") from e
from pymysql import connect
import datetime as dt
# ========= tqdm（进度条）检测与降级 =========
# 是否安装了 tqdm；用于决定日志输出方式（tqdm.write / print）以及是否显示进度条
_HAS_TQDM = True
try:
    from tqdm import tqdm
except Exception:
    _HAS_TQDM = False
    class _DummyTQDM:
        def __init__(self, *args, **kwargs): pass
        def update(self, n=1): pass
        def close(self): pass
        def set_postfix_str(self, s="", refresh=False): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): pass
    def tqdm(*args, **kwargs):  # noqa: F811
        return _DummyTQDM()

def _log(msg: str):
    text = str(msg)
    if _HAS_TQDM:
        try:
            from tqdm import tqdm as _tqdm
            _tqdm.write(text)
            return
        except Exception:
            pass
    try:
        print(text)
    except UnicodeEncodeError:
        # Windows 控制台编码可能为 gbk，遇到特殊字符时用 replace 降级，避免程序崩溃
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            sys.stdout.buffer.write((text + "\n").encode(enc, errors="replace"))
        except Exception:
            # 最后兜底
            sys.stdout.write((text + "\n").encode(enc, errors="replace").decode(enc, errors="replace"))

# =========================
def _callable_name(fn) -> str:
    name = getattr(fn, "__name__", None)
    if name:
        return str(name)
    inner = getattr(fn, "func", None)  # functools.partial
    inner_name = getattr(inner, "__name__", None)
    if inner_name:
        return str(inner_name)
    return type(fn).__name__

def _joblib_parallel(n_jobs: int, tasks):
    return Parallel(
        n_jobs=max(1, int(n_jobs)),
        backend="threading",
        prefer="threads",
        batch_size=1,
        pre_dispatch="n_jobs",
    )(tasks)


def _effective_max_workers(requested: int, total_tasks: int) -> int:
    requested_n = max(1, int(requested))
    total_n = max(1, int(total_tasks))
    api_cap = max(1, int(API_MAX_CONCURRENCY))
    return min(requested_n, total_n, api_cap)

def _parse_tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,\s;]+", str(raw).strip())
    return [p.strip() for p in parts if p and p.strip()]

def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err)
    keyw = (
        "频率", "限流", "访问过于频繁", "每分钟", "每秒", "次数限制", "超过限制",
        "Too Many Requests", "429", "rate limit", "limit",
    )
    return any(k.lower() in msg.lower() for k in keyw)

class _TsTokenCallError(Exception):
    def __init__(self, token_index: int, original: Exception, *, token: str):
        super().__init__(redact_tushare_error(original, token=token))
        self.token_index = token_index
        self.original = original

class _TsTokenPool:
    def __init__(self, tokens: list[str], per_token_concurrency: int, cooldown_seconds: int = 60):
        self.tokens = tokens
        self.pros = [create_tushare_client(t) for t in tokens]
        self.sems = [Semaphore(max(1, int(per_token_concurrency))) for _ in tokens]
        self.cooldown_until = [0.0 for _ in tokens]
        self.cooldown_seconds = int(cooldown_seconds)
        self._lock = Lock()
        self._rr = 0

    def _pick_index(self) -> int:
        now = time.time()
        with self._lock:
            n = len(self.tokens)
            for _ in range(n):
                i = self._rr
                self._rr = (self._rr + 1) % n
                if self.cooldown_until[i] <= now:
                    return i
            return min(range(n), key=lambda j: self.cooldown_until[j])

    def mark_rate_limited(self, token_index: int, cooldown_seconds: int | None = None):
        cd = int(self.cooldown_seconds if cooldown_seconds is None else cooldown_seconds)
        until = time.time() + max(1, cd)
        with self._lock:
            self.cooldown_until[token_index] = max(self.cooldown_until[token_index], until)

    def call(self, method_name: str, **kwargs):
        if not self.tokens:
            raise RuntimeError("TS_TOKEN_POOL 未初始化")
        idx = self._pick_index()
        wait = self.cooldown_until[idx] - time.time()
        if wait > 0:
            time.sleep(min(wait, self.cooldown_seconds))
        sem = self.sems[idx]
        sem.acquire()
        try:
            try:
                fn = getattr(self.pros[idx], method_name)
                return idx, fn(**kwargs)
            except Exception as e:
                raise _TsTokenCallError(idx, e, token=self.tokens[idx]) from None
        finally:
            sem.release()

    def call_query(self, api_name: str, **kwargs):
        if not self.tokens:
            raise RuntimeError("TS_TOKEN_POOL 未初始化")
        idx = self._pick_index()
        wait = self.cooldown_until[idx] - time.time()
        if wait > 0:
            time.sleep(min(wait, self.cooldown_seconds))
        sem = self.sems[idx]
        sem.acquire()
        try:
            try:
                return idx, self.pros[idx].query(api_name, **kwargs)
            except Exception as e:
                raise _TsTokenCallError(idx, e, token=self.tokens[idx]) from None
        finally:
            sem.release()

    def call_pro_bar(self, **kwargs):
        if not self.tokens:
            raise RuntimeError("TS_TOKEN_POOL 未初始化")
        idx = self._pick_index()
        wait = self.cooldown_until[idx] - time.time()
        if wait > 0:
            time.sleep(min(wait, self.cooldown_seconds))
        sem = self.sems[idx]
        sem.acquire()
        buf = io.StringIO()
        try:
            try:
                with redirect_stdout(buf):
                    df = ts.pro_bar(api=self.pros[idx], **kwargs)
                return idx, df
            except Exception as e:
                error = _ProBarError(e, buf.getvalue())
                raise _TsTokenCallError(idx, error, token=self.tokens[idx]) from None
        finally:
            sem.release()

# =========================
class _ProBarError(Exception):
    def __init__(self, original: Exception, output: str):
        super().__init__(str(original))
        self.original = original
        self.output = output or ""

    def __str__(self) -> str:
        out = self.output.strip()
        if out:
            return f"{self.original} | pro_bar: {out}"
        return str(self.original)

# =========================
# 全局配置（可通过环境变量覆盖）
# -------------------------
_APP_CONFIG = get_config()
_MYSQL_CONFIG = _APP_CONFIG.mysql
_TUSHARE_CONFIG = _APP_CONFIG.tushare

# TuShare token 必须通过环境变量提供：
# - 默认跟随主项目 [tushare] token_env/token_env_alt（config/config.ini 中默认为 TUSHARE_TOKEN/TS_TOKEN）
# - 也兼容 [tushare] token 明文配置，但不建议提交真实 token
_CLI_TS_TOKENS: list[str] = []


def _resolve_ts_tokens() -> list[str]:
    tokens = list(_CLI_TS_TOKENS) or _parse_tokens(getattr(_TUSHARE_CONFIG, "token", ""))
    seen: set[str] = set()
    return [token for token in tokens if token and (token not in seen and not seen.add(token))]


TS_TOKENS = _resolve_ts_tokens()
# MySQL 连接参数
MYSQL_HOST = _MYSQL_CONFIG.host       # MySQL 主机
MYSQL_PORT = int(_MYSQL_CONFIG.port)  # MySQL 端口
MYSQL_USER = _MYSQL_CONFIG.user       # MySQL 用户名
MYSQL_PWD = _MYSQL_CONFIG.password    # MySQL 密码
MYSQL_CHARSET = _MYSQL_CONFIG.charset or 'utf8mb4'
MYSQL_STOCK_DB = _MYSQL_CONFIG.stock_db
MYSQL_INDEX_DB = _MYSQL_CONFIG.index_db
MYSQL_ETF_DB = os.getenv("MYSQL_ETF_DB", getattr(_MYSQL_CONFIG, "etf_db", "etf_data"))

# TuShare 拉取与并发控制
TS_MAX_ROWS = int(os.getenv('TS_MAX_ROWS', '6000'))               # TuShare 单次返回行数上限（触顶则拆分区间）
# 扩展接口（非主行情）部分 endpoint 单次仅返回约 2000 行；该阈值用于触发拆分区间拉取
TS_MAX_ROWS_EXT = int(os.getenv("TS_MAX_ROWS_EXT", "2000"))
API_MAX_CONCURRENCY = int(os.getenv('API_MAX_CONCURRENCY', '24')) # 全局最大并发（所有 token 合计）
API_MAX_CONCURRENCY_PER_TOKEN = int(
    os.getenv("API_MAX_CONCURRENCY_PER_TOKEN", str(max(1, API_MAX_CONCURRENCY // max(1, len(TS_TOKENS)))))
)
# 被限流后 token 冷却时间（秒）
TS_RATE_LIMIT_COOLDOWN = int(os.getenv("TS_RATE_LIMIT_COOLDOWN", "60"))
# 打印每次 TuShare 调用的接口、字段、代码和日期区间
LOG_TUSHARE_CALLS = os.getenv('LOG_TUSHARE_CALLS', '0') in ('1', 'true', 'True', 'YES', 'yes')
# 默认将空 DataFrame 视为有效响应；否则 optional endpoint 空结果会被重复请求，显著拖慢全市场更新
TS_RETRY_EMPTY_RESPONSE = os.getenv('TS_RETRY_EMPTY_RESPONSE', '0') in ('1', 'true', 'True', 'YES', 'yes')
PRO_BAR_ADJ = os.getenv("PRO_BAR_ADJ", "").strip()

# 写库与连接池
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '12000'))   # executemany 分批大小
DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '8'))   # 每个库的连接池大小
DB_POOL_GET_TIMEOUT = float(os.getenv('DB_POOL_GET_TIMEOUT', '30'))  # 获取连接最长等待秒数
DB_CONNECT_TIMEOUT = float(os.getenv('DB_CONNECT_TIMEOUT', '5'))      # 建立 MySQL 连接最长等待秒数
ONLY_UPDATE_MISSING = os.getenv('ONLY_UPDATE_MISSING', '0') in ('1', 'true', 'True', 'YES', 'yes')  # 仅补空值（不覆盖已有值）
CHECK_MISSING_GAPS = os.getenv('CHECK_MISSING_GAPS', '1') in ('1', 'true', 'True', 'YES', 'yes')    # 更新前检测库内缺失交易日
FILL_EMPTY_DAILY = os.getenv('FILL_EMPTY_DAILY', '1') in ('1', 'true', 'True', 'YES', 'yes')        # 将主行情关键列为空的已有日期纳入回补
DAILY_REQUIRED_COLS = tuple(_parse_tokens(os.getenv('DAILY_REQUIRED_COLS'))) or (
    'open', 'high', 'low', 'close', 'pre_close', 'volume', 'amount',
)
MISSING_SCAN_MAX_DAYS = int(os.getenv('MISSING_SCAN_MAX_DAYS', '0'))                                 # 0=按用户区间全扫描；>0=仅扫描最近N天
MISSING_LOG_LIMIT = int(os.getenv('MISSING_LOG_LIMIT', '12'))                                        # 日志中最多展示多少个缺失日期
TRADE_CAL_EXCHANGE = os.getenv('TRADE_CAL_EXCHANGE', 'SSE')                                          # 交易日历交易所

# TuShare 拉取区间拆分深度（防止触发 TS_MAX_ROWS）
TS_RANGE_MAX_DEPTH = int(os.getenv('TS_RANGE_MAX_DEPTH', '10'))
# 单标的内是否并发拉取（daily/daily_basic/moneyflow/...）
# 说明：当前已改为“先写主行情，再按接口顺序写扩展列”的串行流程；该开关保留但不再使用
FETCH_PARALLEL_PER_SYMBOL = os.getenv('FETCH_PARALLEL_PER_SYMBOL', '0') in ('1', 'true', 'True', 'YES', 'yes')
# 仅拉取，不写入 MySQL（用于调试/测试）
DRY_RUN = os.getenv('DRY_RUN', '0') in ('1', 'true', 'True', 'YES', 'yes')

# 分步骤写库的重试次数/退避基准（秒）
STEP_RETRY_TIMES = int(os.getenv("STEP_RETRY_TIMES", "3"))
STEP_RETRY_DELAY = float(os.getenv("STEP_RETRY_DELAY", "2.0"))

_TS_POOL: _TsTokenPool | None = None


def _ensure_tushare_runtime() -> _TsTokenPool:
    """Initialize TuShare clients only when the tool actually needs them."""
    global TS_TOKENS, _TS_POOL
    tokens = _resolve_ts_tokens()
    if not tokens:
        raise RuntimeError(
            "缺少 TuShare token：请按主项目配置提供 [tushare] token，"
            "或设置 [tushare] token_env/token_env_alt 指向的环境变量"
            "（默认 TUSHARE_TOKEN 或 TS_TOKEN）；"
            "也可通过 --tushare-token、--tushare-token-env 或 --tushare-token-prompt 传入；"
            "不要把 token 写入源码。"
        )
    if _TS_POOL is not None and TS_TOKENS == tokens:
        return _TS_POOL
    TS_TOKENS = tokens
    _TS_POOL = _TsTokenPool(
        tokens,
        per_token_concurrency=API_MAX_CONCURRENCY_PER_TOKEN,
        cooldown_seconds=TS_RATE_LIMIT_COOLDOWN,
    )
    return _TS_POOL


class _LazyPro:
    def __getattr__(self, name: str):
        return getattr(_ensure_tushare_runtime().pros[0], name)


# pro 兼容旧写法；首次访问时才初始化 TuShare。
pro = _LazyPro()

# =========================
# -------------------------
# 工具函数
# -------------------------
def safe_table_name(symbol_code: str) -> str:
    name = symbol_code.replace('.', '_').lower()
    if not re.fullmatch(r'[a-z0-9_]+', name):
        raise ValueError(f'非法表名: {name}')
    return name

def get_db_name(symbol_type: str) -> str:
    symbol_type = symbol_type.lower()
    if symbol_type == 'stock':
        return MYSQL_STOCK_DB
    if symbol_type == 'index':
        return MYSQL_INDEX_DB
    if symbol_type == 'etf':
        return MYSQL_ETF_DB
    raise ValueError("symbol_type 必须是 'stock'、'index' 或 'etf'")


# 缓存：ts_code -> name/industry（延迟加载，减少重复查询）
_NAME_MAPS = {'stock': {}, 'index': {}, 'etf': {}}
_INDUSTRY_MAPS = {'stock': {}, 'index': {}, 'etf': {}}
_SYMBOL_META_WARMED = {'stock': False, 'index': False, 'etf': False}
_SYMBOL_META_LOCKS = {k: Lock() for k in _NAME_MAPS}
_TRADE_CAL_CACHE: dict[tuple[str, str, str], tuple[dt.date, ...]] = {}
_TRADE_CAL_LOCK = Lock()
ETF_BASIC_FIELDS = 'ts_code,extname,index_code,index_name,exchange,mgr_name'


def _normalize_etf_basic_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    if 'name' not in df.columns and 'extname' in df.columns:
        df['name'] = df['extname']
    return df


def _apply_symbol_meta(symbol_type: str, df: pd.DataFrame, mark_warm: bool = False):
    symbol_type = symbol_type.lower()
    if df is None or df.empty or 'ts_code' not in df.columns:
        return
    meta_df = df.dropna(subset=['ts_code']).copy()
    if meta_df.empty:
        return
    meta_df['ts_code'] = meta_df['ts_code'].astype(str)
    if 'name' in meta_df.columns:
        name_series = meta_df.set_index('ts_code')['name']
        _NAME_MAPS[symbol_type].update(
            {str(k): str(v) for k, v in name_series.items() if pd.notna(v) and str(v).strip()}
        )
    if 'industry' in meta_df.columns:
        industry_series = meta_df.set_index('ts_code')['industry']
        _INDUSTRY_MAPS[symbol_type].update(
            {str(k): str(v).strip() for k, v in industry_series.items() if pd.notna(v) and str(v).strip()}
        )
    if mark_warm:
        _SYMBOL_META_WARMED[symbol_type] = True


def _warm_symbol_meta(symbol_type: str):
    symbol_type = symbol_type.lower()
    if _SYMBOL_META_WARMED.get(symbol_type):
        return
    lock = _SYMBOL_META_LOCKS.get(symbol_type)
    if lock is None:
        return
    with lock:
        if _SYMBOL_META_WARMED.get(symbol_type):
            return
        if symbol_type == 'stock':
            df = ts_call(
                pro.stock_basic,
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,area,industry,list_date',
            )
        elif symbol_type == 'index':
            df = ts_call(pro.index_basic, fields='ts_code,name')
        elif symbol_type == 'etf':
            df = _normalize_etf_basic_df(
                ts_call(pro.etf_basic, list_status='L', fields=ETF_BASIC_FIELDS)
            )
        else:
            return
        _apply_symbol_meta(symbol_type, df, mark_warm=True)

def _is_pro_bar_fn(fn) -> bool:
    if fn is ts.pro_bar:
        return True
    if isinstance(fn, functools.partial) and fn.func is ts.pro_bar:
        return True
    return False


def _ts_api_name(fn) -> str:
    if _is_pro_bar_fn(fn):
        return "pro_bar"
    if isinstance(fn, functools.partial):
        if fn.keywords and "api_name" in fn.keywords:
            return str(fn.keywords["api_name"])
        if fn.args:
            return str(fn.args[0])
    return _callable_name(fn)


def _format_ts_fields(raw) -> str:
    if raw is None:
        return "默认字段"
    text = str(raw).replace("\n", "").replace(" ", "")
    max_len = 220
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...(+{len(text) - max_len} chars)"


def _format_ts_call_context(kwargs: dict) -> str:
    parts = []
    for key in ("ts_code", "start_date", "end_date", "trade_date", "exchange", "list_status", "market", "asset", "freq", "adj"):
        value = kwargs.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    parts.append(f"fields={_format_ts_fields(kwargs.get('fields'))}")
    return ", ".join(parts)


def _log_ts_call(api_name: str, kwargs: dict, attempt: int):
    if not LOG_TUSHARE_CALLS:
        return
    _log(
        f"[ts_call][pid={os.getpid()}][thread={current_thread().name}] "
        f"第{attempt}次调用 {api_name}: {_format_ts_call_context(kwargs)}"
    )


def ts_call(fn, **kwargs) -> pd.DataFrame:
    pool = _ensure_tushare_runtime()
    last = pd.DataFrame()
    fnn = _callable_name(fn)
    api_log_name = _ts_api_name(fn)
    for i in range(4):
        try:
            _log_ts_call(api_log_name, kwargs, i + 1)
            api_name = None
            if isinstance(fn, functools.partial):
                if fn.keywords and "api_name" in fn.keywords:
                    api_name = fn.keywords["api_name"]
                elif fn.args:
                    api_name = fn.args[0]

            if _is_pro_bar_fn(fn):
                if "retry_count" not in kwargs or kwargs.get("retry_count") is None:
                    kwargs["retry_count"] = 1
                token_idx, df = pool.call_pro_bar(**kwargs)
            elif api_name:
                token_idx, df = pool.call_query(str(api_name), **kwargs)
            else:
                method_name = getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", None)
                if method_name and method_name != "query" and hasattr(pro, method_name):
                    token_idx, df = pool.call(method_name, **kwargs)
                else:
                    df = fn(**kwargs)
            if df is None:
                df = pd.DataFrame()
            if not df.empty or not TS_RETRY_EMPTY_RESPONSE:
                return df
            last = df
        except _TsTokenCallError as e:
            if _is_rate_limit_error(e.original) and len(TS_TOKENS) > 1:
                pool.mark_rate_limited(e.token_index)
            last = pd.DataFrame()
            _log(f"[ts_call] {fnn} 调用异常：{e}，退避 {0.5*(2**i):.1f}s 后重试")
        except Exception as e:
            last = pd.DataFrame()
            _log(f"[ts_call] {fnn} 调用异常：{e}，退避 {0.5*(2**i):.1f}s 后重试")
        time.sleep(0.5 * (2 ** i))
    return last


def _split_date_range_by_year(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start_dt = datetime.strptime(start_date, '%Y%m%d').date()
    end_dt = datetime.strptime(end_date, '%Y%m%d').date()
    parts: list[tuple[str, str]] = []
    for year in range(start_dt.year, end_dt.year + 1):
        seg_start = max(start_dt, datetime(year, 1, 1).date())
        seg_end = min(end_dt, datetime(year, 12, 31).date())
        if seg_start <= seg_end:
            parts.append((seg_start.strftime('%Y%m%d'), seg_end.strftime('%Y%m%d')))
    return parts


def _merge_ts_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    for dedup_col in ('trade_date', 'cal_date'):
        if dedup_col in merged.columns:
            merged = merged.drop_duplicates(subset=[dedup_col], keep='last')
            merged = merged.sort_values(dedup_col)
            break
    return merged


def _ts_call_range_safe(fn, start_date: str, end_date: str, depth: int, max_rows: int, **kwargs) -> pd.DataFrame:
    
    df = ts_call(fn, start_date=start_date, end_date=end_date, **kwargs)
    if df is None or df.empty:
        return pd.DataFrame()
    if len(df) < max_rows:
        return df

    if depth <= 0:
        _log(f"[ts_call_range] {_callable_name(fn)} 返回 {len(df)} 行 >= {max_rows}，已达最大拆分深度：{start_date}~{end_date}")
        return df

    start_dt = datetime.strptime(start_date, '%Y%m%d').date()
    end_dt = datetime.strptime(end_date, '%Y%m%d').date()
    if start_dt >= end_dt:
        _log(f"[ts_call_range] {_callable_name(fn)} 返回 {len(df)} 行 >= {max_rows}，且区间无法再拆分：{start_date}~{end_date}")
        return df

    if start_dt.year != end_dt.year:
        frames: list[pd.DataFrame] = []
        for seg_start, seg_end in _split_date_range_by_year(start_date, end_date):
            frames.append(_ts_call_range_safe(fn, seg_start, seg_end, depth - 1, max_rows=max_rows, **kwargs))
        return _merge_ts_frames(frames)

    mid_dt = start_dt + (end_dt - start_dt) // 2
    left_end = mid_dt.strftime('%Y%m%d')
    right_start_dt = mid_dt + timedelta(days=1)
    right_start = right_start_dt.strftime('%Y%m%d')
    left = _ts_call_range_safe(fn, start_date, left_end, depth - 1, max_rows=max_rows, **kwargs)
    right = _ts_call_range_safe(fn, right_start, end_date, depth - 1, max_rows=max_rows, **kwargs)
    return _merge_ts_frames([left, right])


def ts_call_range(fn, start_date: str, end_date: str, max_rows: int | None = None, **kwargs) -> pd.DataFrame:
    """
    TuShare 单次最多返回一定行数；若疑似触发行数上限则按日期区间拆分拉取并合并。
    """
    cap = TS_MAX_ROWS if max_rows is None else int(max_rows)
    cap = max(1, cap)
    return _ts_call_range_safe(fn, start_date, end_date, TS_RANGE_MAX_DEPTH, max_rows=cap, **kwargs)


def get_effective_start_date(conn, db_name: str, table: str, ts_code: str, user_start: str, week_freq='W-FRI'):
    """
    从库里取 MAX(trade_date)，按“用户起点 vs 最新日期+1”确定本次拉取起点。
    返回 (eff_start_yyyymmdd, latest_trade_date_or_none)。
    """
    try:
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                    (db_name, table))
        if cur.fetchone() is None:
            cur.close()
            return user_start, None

        cur.execute(f"SELECT MAX(trade_date) FROM `{table}` WHERE ts_code=%s", (ts_code,))
        row = cur.fetchone()
        cur.close()

        if not row or not row[0]:
            return user_start, None

        latest_dt: dt.date = row[0]
        user_dt = datetime.strptime(user_start, '%Y%m%d').date()
        next_dt = latest_dt + timedelta(days=1)
        eff_dt = max(user_dt, next_dt)
        return eff_dt.strftime('%Y%m%d'), latest_dt
    except Exception:
        return user_start, None


def _format_date_list(dates: list[dt.date], limit: int = MISSING_LOG_LIMIT) -> str:
    if not dates:
        return "-"
    shown = ', '.join(d.strftime('%Y-%m-%d') for d in dates[:max(1, int(limit))])
    rest = len(dates) - max(1, int(limit))
    if rest > 0:
        shown += f" ... (+{rest})"
    return shown


def get_open_trade_dates(start_date: str, end_date: str, exchange: str = TRADE_CAL_EXCHANGE) -> list[dt.date]:
    key = (exchange, start_date, end_date)
    with _TRADE_CAL_LOCK:
        cached = _TRADE_CAL_CACHE.get(key)
    if cached is not None:
        return list(cached)

    df_cal = ts_call_range(
        pro.trade_cal,
        start_date=start_date,
        end_date=end_date,
        exchange=exchange,
        fields='cal_date,is_open',
    )
    if df_cal is None or df_cal.empty or 'cal_date' not in df_cal.columns:
        with _TRADE_CAL_LOCK:
            _TRADE_CAL_CACHE[key] = tuple()
        return []

    df_cal = df_cal.copy()
    df_cal['cal_date'] = pd.to_datetime(df_cal['cal_date'], format='%Y%m%d', errors='coerce').dt.date
    df_cal = df_cal.dropna(subset=['cal_date'])
    if 'is_open' in df_cal.columns:
        df_cal['is_open'] = pd.to_numeric(df_cal['is_open'], errors='coerce').fillna(0).astype(int)
        df_cal = df_cal[df_cal['is_open'] == 1]

    dates = tuple(sorted({d for d in df_cal['cal_date'].tolist() if isinstance(d, dt.date)}))
    with _TRADE_CAL_LOCK:
        _TRADE_CAL_CACHE[key] = dates
    return list(dates)


def read_existing_trade_dates(conn, db_name: str, table: str, ts_code: str,
                              start_date: str, end_date: str) -> list[dt.date]:
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (db_name, table),
            )
            if cur.fetchone() is None:
                return []

            start_dt = datetime.strptime(start_date, '%Y%m%d').date()
            end_dt = datetime.strptime(end_date, '%Y%m%d').date()
            cur.execute(
                f"SELECT trade_date FROM `{table}` "
                "WHERE ts_code=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
                (ts_code, start_dt, end_dt),
            )
            rows = cur.fetchall()
            return [r[0] for r in rows if r and isinstance(r[0], dt.date)]
        finally:
            cur.close()
    except Exception as e:
        _log(f"[{ts_code}] 读取库内 trade_date 失败：{e}")
        return []


def read_empty_daily_dates(conn, db_name: str, table: str, ts_code: str,
                           start_date: str, end_date: str) -> list[dt.date]:
    if not FILL_EMPTY_DAILY:
        return []
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (db_name, table),
            )
            if cur.fetchone() is None:
                return []

            cur.execute(f"SHOW COLUMNS FROM `{table}`;")
            schema_cols = {str(row[0]).lower() for row in cur.fetchall()}
            required = [c for c in DAILY_REQUIRED_COLS if c.lower() in schema_cols]
            if not required:
                return []

            start_dt = datetime.strptime(start_date, '%Y%m%d').date()
            end_dt = datetime.strptime(end_date, '%Y%m%d').date()
            null_check = " OR ".join(f"`{c}` IS NULL" for c in required)
            cur.execute(
                f"SELECT trade_date FROM `{table}` "
                f"WHERE ts_code=%s AND trade_date BETWEEN %s AND %s AND ({null_check}) "
                "ORDER BY trade_date",
                (ts_code, start_dt, end_dt),
            )
            rows = cur.fetchall()
            return [r[0] for r in rows if r and isinstance(r[0], dt.date)]
        finally:
            cur.close()
    except Exception as e:
        _log(f"[{ts_code}] 读取主行情空值日期失败：{e}")
        return []


def dtype_for(col: str) -> str | None:
    c = col.lower()
    if c in ('ts_code',):
        return 'VARCHAR(20)'
    if c in ('name',):
        return 'VARCHAR(100)'
    if c in ('trade_date',):
        return None  # DATE

    if c in ('industry', 'area', 'suspend_type', 'suspend_timing'):
        return 'VARCHAR(100)'

    if c in ('adj_factor',):
        return 'DECIMAL(20,6)'

    if c in (
        'volume', 'week_volume', 'month_volume',
        'total_share', 'float_share', 'free_share',
        'buy_sm_vol', 'sell_sm_vol',
        'buy_md_vol', 'sell_md_vol',
        'buy_lg_vol', 'sell_lg_vol',
        'buy_elg_vol', 'sell_elg_vol',
        'net_mf_vol',
        'selling', 'buying',
        'rqyl', 'rqchl', 'rqmcl',
    ):
        return 'BIGINT'
    if c in (
        'amount', 'total_mv', 'circ_mv',
        'float_mv',
        'net_mf_amount', 'net_amount',
        'buy_sm_amount', 'sell_sm_amount',
        'buy_md_amount', 'sell_md_amount',
        'buy_lg_amount', 'sell_lg_amount',
        'buy_elg_amount', 'sell_elg_amount',
        'rzye', 'rqye', 'rzmre', 'rzche', 'rzrqye',
    ):
        return 'DECIMAL(20,2)'
    return 'DECIMAL(20,4)'


# 表结构缓存：db.table -> {col: mysql_type}，避免频繁 SHOW COLUMNS
_TABLE_SCHEMA_CACHE: dict[str, dict[str, str]] = {}


# 用于解析 decimal(p,s) 的正则
_DECIMAL_RE = re.compile(r"decimal\((\d+),(\d+)\)")


def _base_sql_type(mysql_type: str) -> str:
    return str(mysql_type).strip().lower().split('(')[0].split()[0]


def _need_type_upgrade(current_type: str, desired_type: str) -> bool:
    cur = str(current_type).strip().lower()
    des = str(desired_type).strip().lower()
    cur_base = _base_sql_type(cur)
    des_base = _base_sql_type(des)

    if des_base == 'bigint':
        return cur_base != 'bigint'

    if des_base == 'decimal':
        if cur_base != 'decimal':
            return True
        m_cur = _DECIMAL_RE.search(cur)
        m_des = _DECIMAL_RE.search(des)
        if not m_cur or not m_des:
            return True
        p_cur, s_cur = int(m_cur.group(1)), int(m_cur.group(2))
        p_des, s_des = int(m_des.group(1)), int(m_des.group(2))
        return (p_cur < p_des) or (s_cur < s_des)

    return False


def ensure_table_and_columns(conn, db_name: str, table: str, save_cols: list[str]):
    cache_key = f"{db_name}.{table}"
    schema = _TABLE_SCHEMA_CACHE.get(cache_key)
    if schema is None:
        cur = conn.cursor()
        try:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS `{table}` (
                    `ts_code` VARCHAR(20) NOT NULL,
                    `name`    VARCHAR(100),
                    `industry` VARCHAR(100),
                    `trade_date` DATE NOT NULL,
                    PRIMARY KEY (`ts_code`, `trade_date`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute(f"SHOW COLUMNS FROM `{table}`;")
            schema = {str(row[0]).lower(): str(row[1]).lower() for row in cur.fetchall()}
            _TABLE_SCHEMA_CACHE[cache_key] = schema.copy()
        finally:
            cur.close()

    to_add: list[tuple[str, str]] = []
    to_modify: list[tuple[str, str]] = []
    for col in save_cols:
        col_l = col.lower()
        dtp = dtype_for(col_l)
        if col_l not in schema:
            if dtp:
                to_add.append((col_l, dtp))
            continue
        if not dtp:
            continue
        des_base = _base_sql_type(dtp)
        if des_base not in ('bigint', 'decimal'):
            continue
        cur_t = schema.get(col_l, '')
        if _need_type_upgrade(cur_t, dtp):
            to_modify.append((col_l, dtp))

    if not to_add and not to_modify:
        return

    cur = conn.cursor()
    try:
        for col, dtp in to_add:
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {dtp};")
            schema[col] = dtp.lower()
        for col, dtp in to_modify:
            cur.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` {dtp};")
            schema[col] = dtp.lower()
        conn.commit()
        _TABLE_SCHEMA_CACHE[cache_key] = schema.copy()
    finally:
        cur.close()


def compose_upsert_sql(table: str, cols: list[str], only_update_missing: bool) -> str:
    cols_fmt = ', '.join(f'`{c}`' for c in cols)
    vals_fmt = ', '.join(['%s'] * len(cols))
    if only_update_missing:
        update_fmt = ', '.join(
            f'`{c}`=IFNULL(`{c}`, VALUES(`{c}`))'
            for c in cols if c not in ('trade_date', 'ts_code')
        )
    else:
        update_fmt = ', '.join(
            f'`{c}`=IFNULL(VALUES(`{c}`), `{c}`)'
            for c in cols if c not in ('trade_date', 'ts_code')
        )
    return f"INSERT INTO `{table}` ({cols_fmt}) VALUES ({vals_fmt}) ON DUPLICATE KEY UPDATE {update_fmt};"


def df_to_db_values(df: pd.DataFrame, cols: list[str]) -> list[list]:
    sub = df[cols].replace([float('inf'), float('-inf')], pd.NA)
    sub = sub.astype(object).where(pd.notna(sub), None)
    return sub.values.tolist()

def run_step(symbol_code: str, step_name: str, fn, retries: int = STEP_RETRY_TIMES, delay: float = STEP_RETRY_DELAY, required: bool = False):
    last_err: Exception | None = None
    for i in range(1, max(1, int(retries)) + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            wait = float(delay) * i
            _log(f"[{symbol_code}] {step_name} 第{i}次失败：{e}，{wait:.1f}s后重试")
            time.sleep(wait)
    msg = f"[{symbol_code}] {step_name} 重试{retries}次仍失败"
    if required:
        raise RuntimeError(msg) from last_err
    _log(msg + "，跳过")
    return None


def write_df_to_mysql(symbol_code: str, db_name: str, table: str, df: pd.DataFrame, cols: list[str],
                      only_update_missing: bool | None = None) -> int:
    """
    将 df 中指定 cols 以 upsert 方式写入 MySQL。
    - 自动补列/升精度（ensure_table_and_columns）
    - 自动忽略“全为空”的非主键列，避免无意义更新
    """
    if df is None or df.empty:
        return 0

    df_reset = df.reset_index(drop=True)
    available = [c for c in cols if c in df_reset.columns]
    if not available:
        return 0

    key_cols = {'ts_code', 'trade_date'}
    keep_cols: list[str] = []
    for c in available:
        if c in key_cols:
            keep_cols.append(c)
            continue
        try:
            if df_reset[c].isna().all():
                continue
        except Exception:
            pass
        keep_cols.append(c)

    if set(keep_cols).issubset(key_cols):
        return 0

    df_reset = df_reset[keep_cols]
    df_reset['trade_date'] = pd.to_datetime(df_reset['trade_date'], errors='coerce').dt.date
    df_reset = df_reset.dropna(subset=['trade_date'])
    if df_reset.empty:
        return 0

    if DRY_RUN:
        _log(f"[{symbol_code}] DRY_RUN=1，跳过写库；列数={len(keep_cols)}")
        try:
            _log(f"[{symbol_code}] 列：{keep_cols}")
            _log(df_reset.tail(3))
        except Exception:
            pass
        return int(len(df_reset))

    if db_name not in DB_POOLS.pools:
        DB_POOLS.init_pool(db_name)
    conn = DB_POOLS.get(db_name)
    cursor = None
    had_error = False
    try:
        cursor = conn.cursor()
        ensure_table_and_columns(conn, db_name, table, keep_cols)
        sql = compose_upsert_sql(table, keep_cols, only_update_missing=ONLY_UPDATE_MISSING if only_update_missing is None else bool(only_update_missing))
        data = df_to_db_values(df_reset, keep_cols)
        for batch in chunk_iter(data, BATCH_SIZE):
            cursor.executemany(sql, batch)
        conn.commit()
        return int(len(data))
    except Exception:
        had_error = True
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        if had_error:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            try:
                conn = DB_POOLS._create_conn(db_name)
            except Exception as e:
                _log(f"[{symbol_code}] 写库失败后重建数据库连接失败：{e}")
        if conn is not None:
            DB_POOLS.put(db_name, conn)


class ConnPools:
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        self._lock = Lock()
        self.pools: dict[str, Queue] = {}  # db_name -> Queue[conn]

    def _create_conn(self, db: str):
        return connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PWD,
            database=db,
            charset=MYSQL_CHARSET,
            autocommit=False,
            connect_timeout=max(1, int(DB_CONNECT_TIMEOUT)),
            read_timeout=30,
            write_timeout=30,
        )

    def init_pool(self, db: str):
        with self._lock:
            if db in self.pools:
                return
            q: Queue = Queue(maxsize=self.pool_size)
            for _ in range(self.pool_size):
                q.put(self._create_conn(db))
            self.pools[db] = q

    def get(self, db: str, timeout: float = DB_POOL_GET_TIMEOUT):
        try:
            conn = self.pools[db].get(timeout=max(0.1, float(timeout)))
        except Empty as e:
            raise RuntimeError(f"数据库连接池耗尽：{db}，等待 {timeout}s 后仍无可用连接") from e

        try:
            conn.ping(reconnect=True)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            conn = self._create_conn(db)
        return conn

    def put(self, db: str, conn):
        if conn is None:
            return
        try:
            self.pools[db].put_nowait(conn)
        except Full:
            try:
                conn.close()
            except Exception:
                pass


# 数据库连接池（每个 db_name 一个 Queue）
DB_POOLS = ConnPools(pool_size=DB_POOL_SIZE)


def assert_mysql_available(db_names):
    if DRY_RUN:
        return
    for db_name in sorted({str(db).strip() for db in db_names if str(db).strip()}):
        conn = None
        try:
            conn = connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PWD,
                database=db_name,
                charset=MYSQL_CHARSET,
                autocommit=True,
                connect_timeout=max(1, int(DB_CONNECT_TIMEOUT)),
                read_timeout=10,
                write_timeout=10,
            )
        except Exception as e:
            raise RuntimeError(
                f"无法连接 MySQL：{MYSQL_HOST}:{MYSQL_PORT}/{db_name}。"
                "请确认 MySQL 服务已启动、端口和账号密码正确，并且目标数据库已创建；"
                "连接参数来自主项目配置 config/config.ini、config/config.local.ini "
                "以及 [mysql] password_env 指向的环境变量（默认 MYSQL_PASSWORD）；"
                "也可通过 --mysql-password、--mysql-password-env 或 --mysql-password-prompt 传入密码。"
                f" 原始错误：{e}"
            ) from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def preflight_mysql_for_symbol_types(symbol_types):
    if DRY_RUN:
        return
    db_names = []
    for symbol_type in symbol_types:
        try:
            db_names.append(get_db_name(str(symbol_type).lower()))
        except Exception:
            pass
    assert_mysql_available(db_names)

def prepare_bar_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if 'trade_date' not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d', errors='coerce')
    df = df.dropna(subset=['trade_date'])
    df['trade_date'] = df['trade_date'].dt.normalize()
    df = df.sort_values('trade_date').drop_duplicates(subset=['trade_date'], keep='last')

    for c in ('open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount'):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    return df


_update_runner.bind_runtime(globals())
chunk_iter = _update_runner.chunk_iter
main = _update_runner.main
cli_main = _update_runner.cli_main


if __name__ == '__main__':
    if len(sys.argv) == 1:
        main(
            start_date='20000101',
            end_date='20261231',
            update_stock=True,
            update_index=True,
            update_etf=True,
            week_freq='W-FRI',
            max_workers=API_MAX_CONCURRENCY,
        )
    else:
        raise SystemExit(cli_main())
