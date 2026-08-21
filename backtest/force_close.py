from __future__ import annotations

from core.exceptions import DataValidationError


VALID_FORCE_CLOSE_MODES = ("mark_to_market", "tradable_only", "force")


def resolve_force_close_mode(cfg, *, default: str = "force") -> str:
    """Resolve portfolio window-end handling.

    `default="force"` preserves behavior for lightweight test configs that do
    not yet carry `portfolio.force_close_mode`. Real loaded config provides the
    field and defaults to `mark_to_market`.
    """
    portfolio = getattr(cfg, "portfolio", None)
    raw = getattr(portfolio, "force_close_mode", None)
    mode = str(raw if raw is not None else default).strip().lower().replace("-", "_")
    aliases = {
        "mark": "mark_to_market",
        "mtm": "mark_to_market",
        "none": "mark_to_market",
        "strict": "tradable_only",
        "tradable": "tradable_only",
    }
    mode = aliases.get(mode, mode)
    if mode not in VALID_FORCE_CLOSE_MODES:
        expected = "/".join(VALID_FORCE_CLOSE_MODES)
        raise DataValidationError(f"portfolio.force_close_mode invalid: {mode!r} (expected {expected})")
    return mode


__all__ = ["VALID_FORCE_CLOSE_MODES", "resolve_force_close_mode"]
