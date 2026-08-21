"""Machine-readable field catalog for daily strategy inputs and outputs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


FIELD_CATALOG_VERSION = "1.0"
BASE_INPUT_FIELDS = (
    "trade_date",
    "stock_code",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
STANDARD_SIGNAL_FIELDS = (
    "buy_signal",
    "buy_signal_raw",
    "direction",
    "sell_signal",
    "sell_signal_raw",
)
FRAMEWORK_RESERVED_FIELDS = (
    *BASE_INPUT_FIELDS,
    *STANDARD_SIGNAL_FIELDS,
    "next_up_limit_ok",
)
_FORBIDDEN_INPUT_FIELDS = (*STANDARD_SIGNAL_FIELDS, "next_up_limit_ok")
_RESERVED_CASEFOLD = frozenset(name.casefold() for name in FRAMEWORK_RESERVED_FIELDS)
_FORBIDDEN_INPUT_CASEFOLD = frozenset(name.casefold() for name in _FORBIDDEN_INPUT_FIELDS)


@dataclass(frozen=True, slots=True)
class StrategyField:
    name: str
    role: str
    dtype: str
    unit: str = ""
    aliases: tuple[str, ...] = ()
    nullable: bool = False
    source: str = "daily_price"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "dtype": self.dtype,
            "unit": self.unit,
            "aliases": list(self.aliases),
            "nullable": self.nullable,
            "source": self.source,
            "description": self.description,
        }


_FIELDS = (
    StrategyField("amount", "optional_input", "float", "CNY_thousand", description="Daily turnover amount."),
    StrategyField("close", "base_input", "float", "CNY"),
    StrategyField("high", "base_input", "float", "CNY"),
    StrategyField("low", "base_input", "float", "CNY"),
    StrategyField("net_amount", "optional_input", "float", "CNY_ten_thousand"),
    StrategyField("net_amount_rate", "optional_input", "float", "percentage_point"),
    StrategyField("net_mf_amount", "optional_input", "float", "CNY_ten_thousand"),
    StrategyField("open", "base_input", "float", "CNY"),
    StrategyField("pct_chg", "optional_input", "float", "percentage_point"),
    StrategyField("pre_close", "optional_input", "float", "CNY"),
    StrategyField("stock_code", "key", "string"),
    StrategyField("stock_name", "optional_input", "string", nullable=True),
    StrategyField("trade_date", "key", "datetime64[ns]"),
    StrategyField("volume", "base_input", "float", "share", aliases=("vol",)),
)
_CATALOG = {item.name.casefold(): item for item in _FIELDS}
_ALIASES = {
    alias.casefold(): item.name
    for item in _FIELDS
    for alias in item.aliases
}


def normalize_field_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    return _ALIASES.get(name.casefold(), name)


def normalize_field_names(values: Iterable[object] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(
        dict.fromkeys(
            name
            for name in (normalize_field_name(value) for value in values)
            if name
        )
    )


def field_definition(name: str) -> StrategyField | None:
    normalized = normalize_field_name(name)
    return _CATALOG.get(normalized.casefold())


def is_reserved_field(name: str) -> bool:
    normalized = str(name or "").strip()
    return normalized.casefold() in _RESERVED_CASEFOLD or normalized.startswith("__")


def _is_forbidden_input_field(name: str) -> bool:
    normalized = str(name or "").strip()
    return normalized.casefold() in _FORBIDDEN_INPUT_CASEFOLD or normalized.startswith("__")


def validate_declared_fields(
    *,
    required_fields: Iterable[object] | None,
    price_columns: Iterable[object] | None,
) -> tuple[dict[str, str], ...]:
    required = normalize_field_names(required_fields)
    requested = set(normalize_field_names(price_columns))
    base = set(BASE_INPUT_FIELDS)
    issues: list[dict[str, str]] = []
    for name in required:
        if _is_forbidden_input_field(name):
            issues.append(
                {
                    "severity": "error",
                    "code": "reserved_required_field",
                    "message": f"required_fields contains framework-owned field {name!r}",
                }
            )
        elif name not in base and name not in requested:
            issues.append(
                {
                    "severity": "error",
                    "code": "required_field_not_requested",
                    "message": f"required input field {name!r} must also be declared in price_columns",
                }
            )
    for name in requested:
        if _is_forbidden_input_field(name):
            issues.append(
                {
                    "severity": "error",
                    "code": "reserved_price_column",
                    "message": f"price_columns contains framework-owned field {name!r}",
                }
            )
    return tuple(issues)


def field_catalog_payload() -> dict[str, Any]:
    return {
        "catalog_version": FIELD_CATALOG_VERSION,
        "base_input_fields": list(BASE_INPUT_FIELDS),
        "reserved_fields": list(FRAMEWORK_RESERVED_FIELDS),
        "fields": [item.to_dict() for item in _FIELDS],
    }


__all__ = [
    "BASE_INPUT_FIELDS",
    "FIELD_CATALOG_VERSION",
    "FRAMEWORK_RESERVED_FIELDS",
    "STANDARD_SIGNAL_FIELDS",
    "StrategyField",
    "field_catalog_payload",
    "field_definition",
    "is_reserved_field",
    "normalize_field_name",
    "normalize_field_names",
    "validate_declared_fields",
]
