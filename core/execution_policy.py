from __future__ import annotations

from dataclasses import dataclass

from core.exceptions import ConfigurationError


@dataclass(frozen=True)
class ExecutionPolicy:
    buy_timing: str
    sell_timing: str
    shift_t1_sell: bool
    allow_intraday: bool

    def to_dict(self) -> dict:
        return {
            "buy_timing": self.buy_timing,
            "sell_timing": self.sell_timing,
            "shift_t1_sell": bool(self.shift_t1_sell),
            "allow_intraday": bool(self.allow_intraday),
        }


def _normalize_timing(value: str, *, field: str, allowed: set[str]) -> str:
    raw = str(value or "").strip().lower()
    if raw in allowed:
        return raw
    if raw == "":
        raise ConfigurationError(f"{field} 不能为空")
    opts = "/".join(sorted(allowed))
    raise ConfigurationError(f"{field} 无法识别: {value!r}（仅支持 {opts}）")


def get_execution_policy(cfg) -> ExecutionPolicy:
    """
    构建执行策略。

    配置优先级（高 → 低）：
    1) 环境变量（由 config/app_config.py 解析）
    2) config.ini
    3) 代码默认值
    """
    if cfg is None:
        raise ConfigurationError("get_execution_policy 需要显式传入 cfg。")

    buy_timing = _normalize_timing(
        cfg.run.exec_buy_timing,
        field="exec_buy_timing",
        allowed={"next_open", "signal_close"},
    )
    sell_timing = _normalize_timing(
        cfg.run.exec_sell_timing,
        field="exec_sell_timing",
        allowed={"signal_close", "next_open"},
    )
    shift_t1_sell = bool(cfg.run.exec_shift_t1)
    allow_intraday = bool(cfg.run.exec_allow_intraday)

    if allow_intraday and sell_timing != "signal_close":
        raise ConfigurationError("exec_allow_intraday=1 仅在 sell_timing=signal_close 时生效")

    return ExecutionPolicy(
        buy_timing=buy_timing,
        sell_timing=sell_timing,
        shift_t1_sell=shift_t1_sell,
        allow_intraday=allow_intraday,
    )
