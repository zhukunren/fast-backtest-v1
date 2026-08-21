from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from core.exceptions import DataSourceError
from web import library_curves


pytestmark = pytest.mark.unit


def test_benchmark_fallback_prefers_environment_token(monkeypatch) -> None:
    seen_tokens: list[str | None] = []

    class _FakePro:
        def index_daily(self, **kwargs) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "ts_code": ["399006.SZ"],
                    "trade_date": ["20240102"],
                    "close": [100.0],
                }
            )

    def _fake_pro_api(token=None):
        seen_tokens.append(token)
        return _FakePro()

    monkeypatch.setenv("TUSHARE_TOKEN", "stale-inherited-token")
    monkeypatch.setattr(
        library_curves,
        "get_config",
        lambda: SimpleNamespace(tushare=SimpleNamespace(token="fake-configured-token")),
    )
    monkeypatch.setitem(
        sys.modules,
        "tushare.pro.data_pro",
        SimpleNamespace(pro_api=_fake_pro_api),
    )

    frame = library_curves._read_benchmark_daily_from_tushare(
        "399006.SZ",
        start_date="20240102",
        end_date="20240102",
    )

    assert seen_tokens == ["stale-inherited-token"]
    assert frame["trade_date"].tolist() == [pd.Timestamp("2024-01-02")]


def test_benchmark_fallback_rejects_missing_token_before_sdk(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        library_curves,
        "get_config",
        lambda: SimpleNamespace(tushare=SimpleNamespace(token="")),
    )
    monkeypatch.setitem(
        sys.modules,
        "tushare.pro.data_pro",
        SimpleNamespace(pro_api=lambda **kwargs: calls.append(kwargs)),
    )

    with pytest.raises(DataSourceError, match="tk.csv"):
        library_curves._read_benchmark_daily_from_tushare(
            "399006.SZ",
            start_date="20240102",
            end_date="20240102",
        )

    assert calls == []
