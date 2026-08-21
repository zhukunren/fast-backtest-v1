"""Pydantic数据模型 - 为核心数据结构建立类型契约

本模块使用 Pydantic V2 为系统中的关键数据结构定义模型，
实现自动验证、类型强制和详细的错误报告。

好处：
  - 自动数据验证，拒绝无效数据
  - 明确的数据契约，便于代码维护
  - 详细的错误信息，快速定位数据问题
  - IDE 支持，改善开发体验

使用方式：
  from core.models import SignalRecord
  signal = SignalRecord(
      trade_date="2025-01-29",
      stock_code="000001.SZ",
      direction="buy"
  )

"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from core.exceptions import DataValidationError


# ============= 交易相关模型 =============

class SignalRecord(BaseModel):
    """单个交易信号记录

    代表一个特定日期、特定股票的交易信号
    """

    trade_date: datetime = Field(..., description="交易日期")
    stock_code: str = Field(..., description="股票代码（如000001.SZ）")
    direction: Literal["", "buy", "sell"] = Field(
        default="", description="交易方向：buy/sell/空字符"
    )

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        """股票代码不能为空"""
        if not v or not v.strip():
            raise DataValidationError("stock_code 不能为空")
        return v.strip()

    @field_validator("direction", mode="before")
    @classmethod
    def validate_direction(cls, v: object) -> str:
        """方向值必须是 buy/sell/空字符串"""
        v_lower = str(v).lower().strip() if v else ""
        if v_lower not in ("", "buy", "sell"):
            raise DataValidationError(f"direction 只能是 'buy', 'sell' 或空字符串，got '{v}'")
        return v_lower

    model_config = {
        "title": "交易信号",
        "json_schema_extra": {
            "examples": [
                {
                    "trade_date": "2025-01-29T00:00:00",
                    "stock_code": "000001.SZ",
                    "direction": "buy",
                }
            ]
        },
    }


class PriceRecord(BaseModel):
    """单个K线数据记录

    代表某股票某日期的OHLCV数据
    """

    trade_date: datetime = Field(..., description="交易日期")
    stock_code: str = Field(..., description="股票代码")
    open: float = Field(..., gt=0, description="开盘价（必须 > 0）")
    close: float = Field(..., gt=0, description="收盘价（必须 > 0）")
    high: float = Field(..., gt=0, description="最高价（必须 > 0）")
    low: float = Field(..., gt=0, description="最低价（必须 > 0）")
    direction: str = Field(default="", description="方向标记（可选）")

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        """股票代码不能为空"""
        if not v or not v.strip():
            raise DataValidationError("stock_code 不能为空")
        return v.strip()

    @model_validator(mode="after")
    def validate_price_range(self) -> PriceRecord:
        """最高价、最低价和收盘价必须形成一致的 K 线。"""
        if self.high < self.low:
            raise DataValidationError(f"high({self.high}) 必须 >= low({self.low})")
        if self.close < self.low:
            raise DataValidationError(
                f"close({self.close}) 必须 >= low({self.low})"
            )
        if self.close > self.high:
            raise DataValidationError(
                f"close({self.close}) 必须 <= high({self.high})"
            )
        return self

    model_config = {
        "title": "价格记录",
        "json_schema_extra": {
            "examples": [
                {
                    "trade_date": "2025-01-29T00:00:00",
                    "stock_code": "000001.SZ",
                    "open": 10.5,
                    "close": 10.8,
                    "high": 11.0,
                    "low": 10.5,
                }
            ]
        },
    }


class TradeRecord(BaseModel):
    """单个交易记录

    代表一个完整的买入-卖出交易
    """

    entry_date: datetime = Field(..., description="买入日期")
    entry_price: float = Field(..., gt=0, description="买入价格")
    exit_date: Optional[datetime] = Field(
        default=None, description="卖出日期（未结束为 None）"
    )
    exit_price: Optional[float] = Field(
        default=None, gt=0, description="卖出价格（未结束为 None）"
    )
    return_pct: float = Field(
        default=0.0, description="收益率（小数形式，如 0.05 表示 5%）"
    )
    hold_days: int = Field(default=0, ge=0, description="持仓天数")
    entry_reason: str = Field(default="", description="买入原因")
    exit_reason: str = Field(default="", description="卖出原因")

    @field_validator("exit_date")
    @classmethod
    def validate_exit_date(cls, v: Optional[datetime], info) -> Optional[datetime]:
        """卖出日期必须 >= 买入日期"""
        if v and "entry_date" in info.data:
            if v < info.data["entry_date"]:
                raise DataValidationError(
                    f"exit_date({v}) 必须 >= entry_date({info.data['entry_date']})"
                )
        return v

    @field_validator("return_pct")
    @classmethod
    def validate_return_pct(cls, v: float) -> float:
        """收益率通常在 [-0.5, 3.0] 范围（允许极端情况）"""
        if v < -0.99:
            raise DataValidationError(
                f"return_pct({v}) 不能低于 -99%（意味着完全亏损）"
            )
        if v > 10:
            raise DataValidationError(
                f"return_pct({v}) 超过 1000%（可能数据错误）"
            )
        return v

    model_config = {
        "title": "交易记录",
        "json_schema_extra": {
            "examples": [
                {
                    "entry_date": "2025-01-10T00:00:00",
                    "entry_price": 10.5,
                    "exit_date": "2025-01-20T00:00:00",
                    "exit_price": 11.0,
                    "return_pct": 0.0476,
                    "hold_days": 10,
                }
            ]
        },
    }


class StrategySignalOutput(BaseModel):
    """策略函数输出的信号数据

    代表策略信号 frame 中的一行
    包含基础信号和排序特征
    """

    trade_date: datetime = Field(..., description="交易日期")
    stock_code: str = Field(..., description="股票代码")
    buy_signal_raw: float = Field(..., description="原始买入信号强度 [0-1]")
    sell_signal_raw: float = Field(..., description="原始卖出信号强度 [0-1]")
    buy_signal: int = Field(..., description="买入信号标志 (0/1)")
    sell_signal: int = Field(..., description="卖出信号标志 (0/1)")
    direction: str = Field(default="", description="处理后的方向")

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, v: str) -> str:
        """股票代码不能为空"""
        if not v or not v.strip():
            raise DataValidationError("stock_code 不能为空")
        return v.strip()

    @field_validator("buy_signal_raw", "sell_signal_raw")
    @classmethod
    def validate_signal_raw(cls, v: float) -> float:
        """原始信号必须在 [0, 1] 范围"""
        if not 0 <= v <= 1:
            raise DataValidationError(f"信号强度必须在 [0, 1] 范围，got {v}")
        return v

    @field_validator("buy_signal", "sell_signal")
    @classmethod
    def validate_signal(cls, v: int) -> int:
        """信号标志必须是 0 或 1"""
        if v not in (0, 1):
            raise DataValidationError(f"信号标志只能是 0 或 1，got {v}")
        return v

    model_config = {
        "title": "策略信号输出",
        "extra": "allow",  # 允许额外字段（sort_spec 列）
    }


# ============= 回测结果模型 =============

class BacktestMetrics(BaseModel):
    """回测性能指标

    包含所有关键的策略评估指标
    """

    cumulative_return: float = Field(..., description="累计收益率（小数形式）")
    max_drawdown: float = Field(..., description="最大回撤（小数形式，负数）")
    annualized_return: Optional[float] = Field(
        default=None, description="年化收益率"
    )
    annualized_volatility: Optional[float] = Field(
        default=None, description="年化波动率"
    )
    sharpe_ratio: Optional[float] = Field(default=None, description="夏普比率")
    sortino_ratio: Optional[float] = Field(
        default=None, description="索提诺比率"
    )
    calmar_ratio: Optional[float] = Field(default=None, description="卡玛比率")
    win_rate: Optional[float] = Field(
        default=None, description="胜率 [0-1]"
    )
    profit_loss_ratio: Optional[float] = Field(
        default=None, description="盈亏比"
    )
    trade_count: int = Field(default=0, ge=0, description="交易次数")
    final_net_value: float = Field(default=1.0, description="最终净值")

    @field_validator("win_rate")
    @classmethod
    def validate_win_rate(cls, v: Optional[float]) -> Optional[float]:
        """胜率必须在 [0, 1] 范围"""
        if v is not None and not 0 <= v <= 1:
            raise DataValidationError(f"win_rate 必须在 [0, 1] 范围，got {v}")
        return v

    model_config = {
        "title": "回测指标",
        "json_schema_extra": {
            "examples": [
                {
                    "cumulative_return": 0.5,
                    "max_drawdown": -0.25,
                    "annualized_return": 0.4,
                    "sharpe_ratio": 1.5,
                    "trade_count": 45,
                    "final_net_value": 1.5,
                }
            ]
        },
    }


# ============= 配置相关模型 =============

class RiskControlConfig(BaseModel):
    """风控配置

    定义回测过程中的风险控制阈值
    """

    warn_drawdown: float = Field(
        default=0.1, ge=0, le=1, description="预警最大回撤"
    )
    danger_drawdown: float = Field(
        default=0.2, ge=0, le=1, description="危险最大回撤"
    )
    lock_drawdown: float = Field(
        default=0.3, ge=0, le=1, description="锁定最大回撤"
    )

    @field_validator("danger_drawdown")
    @classmethod
    def validate_danger_greater_than_warn(cls, v: float, info) -> float:
        """危险回撤必须 > 预警回撤"""
        if "warn_drawdown" in info.data:
            if v <= info.data["warn_drawdown"]:
                raise DataValidationError(
                    f"danger_drawdown({v}) 必须 > warn_drawdown({info.data['warn_drawdown']})"
                )
        return v

    @field_validator("lock_drawdown")
    @classmethod
    def validate_lock_greater_than_danger(cls, v: float, info) -> float:
        """锁定回撤必须 > 危险回撤"""
        if "danger_drawdown" in info.data:
            if v <= info.data["danger_drawdown"]:
                raise DataValidationError(
                    f"lock_drawdown({v}) 必须 > danger_drawdown({info.data['danger_drawdown']})"
                )
        return v

    model_config = {
        "title": "风控配置",
        "json_schema_extra": {
            "examples": [
                {
                    "warn_drawdown": 0.1,
                    "danger_drawdown": 0.2,
                    "lock_drawdown": 0.3,
                }
            ]
        },
    }


__all__ = [
    "SignalRecord",
    "PriceRecord",
    "TradeRecord",
    "StrategySignalOutput",
    "BacktestMetrics",
    "RiskControlConfig",
]
