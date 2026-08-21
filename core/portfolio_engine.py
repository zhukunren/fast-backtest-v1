from __future__ import annotations

def normalize_portfolio_engine(value: object) -> str:
    return str(value or "").strip().lower() or "legacy"


__all__ = [
    "normalize_portfolio_engine",
]
