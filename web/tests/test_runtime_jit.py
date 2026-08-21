from __future__ import annotations

from backtest.netvalue import positions
from backtest.trade_state_machine import TradeStateRuntime
from core import returns_calculator
from web import runtime_jit


def test_web_jit_wrappers_share_dispatchers_across_runtime_instances(monkeypatch) -> None:
    close_dispatcher = object()
    state_dispatcher = object()
    par_dispatcher = object()
    close_calls: list[str] = []
    state_calls: list[str] = []
    par_calls: list[str] = []

    monkeypatch.setattr(runtime_jit, "_INSTALLED", False)
    monkeypatch.setattr(runtime_jit, "_CLOSE_RESOLVED", False)
    monkeypatch.setattr(runtime_jit, "_CLOSE_COMPILER", None)
    monkeypatch.setattr(runtime_jit, "_CLOSE_DISPATCHER", None)
    monkeypatch.setattr(runtime_jit, "_STATE_RESOLVED", False)
    monkeypatch.setattr(runtime_jit, "_STATE_COMPILER", None)
    monkeypatch.setattr(runtime_jit, "_STATE_DISPATCHER", None)
    monkeypatch.setattr(returns_calculator, "_NUMBA_JIT_RESOLVED", False)
    monkeypatch.setattr(returns_calculator, "_par_jit", None)
    monkeypatch.setattr(
        positions,
        "_compile_close_return_jit",
        lambda: close_calls.append("compile") or close_dispatcher,
    )

    def fake_state_compiler(runtime):
        state_calls.append("compile")
        return state_dispatcher

    def fake_par_compiler():
        par_calls.append("compile")
        returns_calculator._NUMBA_JIT_RESOLVED = True
        returns_calculator._par_jit = par_dispatcher
        return par_dispatcher

    monkeypatch.setattr(TradeStateRuntime, "get_state_machine_jit", fake_state_compiler)
    monkeypatch.setattr(returns_calculator, "_get_par_jit", fake_par_compiler)
    runtime_jit.install_shared_jit_dispatchers()

    close_a = positions.create_net_value_runtime()
    close_b = positions.create_net_value_runtime()
    assert close_a.get_close_return_jit() is close_dispatcher
    assert close_b.get_close_return_jit() is close_dispatcher
    assert close_calls == ["compile"]

    state_a = TradeStateRuntime()
    state_b = TradeStateRuntime()
    assert state_a.get_state_machine_jit() is state_dispatcher
    assert state_b.get_state_machine_jit() is state_dispatcher
    assert state_calls == ["compile"]

    assert returns_calculator._get_par_jit() is par_dispatcher
    assert returns_calculator._get_par_jit() is par_dispatcher
    assert par_calls == ["compile"]
