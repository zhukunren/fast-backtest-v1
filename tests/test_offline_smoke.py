from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.offline_smoke_service import run_offline_smoke_backtest
from backtest.result import CurvePayload


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def main() -> int:
    run = run_offline_smoke_backtest()
    payload = run.result.payload
    merged = payload.equity_curve if isinstance(payload, CurvePayload) else None

    if merged is None or merged.empty:
        print("[FAIL] offline smoke: merged_df empty")
        return 1
    if "sum_net_value" not in merged.columns:
        print("[FAIL] offline smoke: sum_net_value missing")
        return 1

    print("[OK] offline smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
