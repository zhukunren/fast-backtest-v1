"""
系统级常量定义

所有硬编码的字符串常量统一在此定义，便于维护和修改。
"""

# ============================================================================
# 信号和交易方向
# ============================================================================

# 交易方向常量
DIRECTION_BUY = "buy"       # 买入
DIRECTION_SELL = "sell"     # 卖出
DIRECTION_NONE = ""         # 无信号

# 所有有效的方向值
VALID_DIRECTIONS = {DIRECTION_BUY, DIRECTION_SELL, DIRECTION_NONE}

# ============================================================================
# 核心列名
# ============================================================================

COL_TRADE_DATE = "trade_date"
COL_STOCK_CODE = "stock_code"

# ============================================================================
# 信号列名定义
# ============================================================================
# 信号经历多个阶段的转换：
#
# 阶段1 - 原始信号（策略生成）:
#   - buy_signal_raw: 原始买入条件（boolean）
#   - sell_signal_raw: 原始卖出条件（boolean）
#
# 阶段2 - 经过过滤的信号:
#   - buy_signal: 经过涨停板保护过滤后的买入信号（boolean）
#   - sell_signal: 卖出信号（boolean）
#
# 阶段3 - 最终方向:
#   - direction: 最终交易方向，值为 "buy" / "sell" / ""

SIGNAL_COL_BUY_RAW = "buy_signal_raw"        # 原始买入信号
SIGNAL_COL_SELL_RAW = "sell_signal_raw"      # 原始卖出信号
SIGNAL_COL_BUY = "buy_signal"                # 过滤后的买入信号
SIGNAL_COL_SELL = "sell_signal"              # 卖出信号
SIGNAL_COL_DIRECTION = "direction"           # 最终交易方向

# 所有必需的原始信号列
SIGNAL_COLS_RAW = {SIGNAL_COL_BUY_RAW, SIGNAL_COL_SELL_RAW}

# 所有处理后的信号列
SIGNAL_COLS_PROCESSED = {SIGNAL_COL_BUY, SIGNAL_COL_SELL, SIGNAL_COL_DIRECTION}

# 所有信号列
SIGNAL_COLS_ALL = SIGNAL_COLS_RAW | SIGNAL_COLS_PROCESSED

# ============================================================================
# 风控相关列
# ============================================================================

RISK_COL_NEXT_UP_LIMIT_OK = "next_up_limit_ok"  # 是否未触及涨停板

# ============================================================================
# 涨跌停幅度（统一口径）
# - *_PCT: 小数形式（0.098 表示 9.8%）
# - *_PERCENT: 百分比形式（9.8）
# ============================================================================

LIMIT_UP_PCT_MAIN = 0.098
LIMIT_UP_PCT_CYTECH = 0.198
LIMIT_UP_PCT_BEIJING = 0.298
LIMIT_UP_PCT_ST = 0.049

LIMIT_UP_PERCENT_MAIN = LIMIT_UP_PCT_MAIN * 100.0
LIMIT_UP_PERCENT_CYTECH = LIMIT_UP_PCT_CYTECH * 100.0
LIMIT_UP_PERCENT_BEIJING = LIMIT_UP_PCT_BEIJING * 100.0
LIMIT_UP_PERCENT_ST = LIMIT_UP_PCT_ST * 100.0

# A 股日线回测默认价格最小变动单位（人民币元）
PRICE_TICK_A_SHARE = 0.01

# ============================================================================
# 数据必需列
# ============================================================================

# OHLCV 基础列
OHLCV_COLS = {"open", "high", "low", "close", "volume", "stock_code", "trade_date"}

# ============================================================================
# 默认值
# ============================================================================

DEFAULT_LOT_SIZE = 100
DEFAULT_TRADE_CALENDAR_MISSING_WARN_RATIO = 0.05

# 头寸利用率阈值（接近满仓时视为非活跃）
POSITION_UTILIZATION_THRESHOLD = 0.999999

# ============================================================================
# 单位和阈值
# ============================================================================

# 停损幅度（负值表示跌幅）
STOP_LOSS_THRESHOLD = -0.07  # 7% 停损

# 回撤风控阈值（负值）
DEFAULT_WARN_DD = -0.05       # 警告回撤
DEFAULT_DANGER_DD = -0.08     # 危险回撤
DEFAULT_LOCK_DD = -0.12       # 锁定回撤

# 浮点容差
FLOAT_TOLERANCE = 1e-9

# 比例/FSM 状态切换容差。保留比通用浮点容差更严格的历史口径，
# 但通过命名常量避免在风控、仓位比例比较中散落 magic number。
RATIO_TOLERANCE = 1e-12
