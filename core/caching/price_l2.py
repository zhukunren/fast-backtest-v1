"""Parquet-backed L2 cache helpers for local price data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from core.caching.price_frame import normalize_price_cache_code, normalized_price_cache_codes


PRICE_L2_CACHE_SCHEMA_VERSION = 1


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def price_l2_cache_enabled_from_env() -> bool:
    return _truthy_env("PRICE_L2_CACHE", default=False)


def price_l2_cache_write_enabled_from_env(*, default: bool) -> bool:
    return _truthy_env("PRICE_L2_CACHE_WRITE", default=default)


def default_price_l2_cache_dir(cfg) -> Path:
    paths = getattr(cfg, "paths", None)
    configured = os.environ.get("PRICE_L2_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    data_root = getattr(paths, "data_root", None)
    if data_root:
        return Path(data_root) / "_price_l2_cache" / "stock_data"
    stock_data = getattr(paths, "stock_data", None)
    if stock_data:
        return Path(stock_data).parent / "_price_l2_cache" / "stock_data"
    return Path("_price_l2_cache") / "stock_data"


@dataclass(frozen=True)
class PriceL2CacheRead:
    frame: pd.DataFrame
    path: Path
    manifest: dict[str, Any] | None


class PriceParquetL2Cache:
    """Small Parquet cache used as a local L2 layer behind in-memory caches."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    def parquet_path(self, stock_code: str) -> Path:
        code = normalize_price_cache_code(stock_code)
        return self.root / f"{code}_data.parquet"

    def manifest_path(self, stock_code: str) -> Path:
        code = normalize_price_cache_code(stock_code)
        return self.root / f"{code}_data.manifest.json"

    def available_codes(self) -> set[str]:
        if not self.root.exists() or not self.root.is_dir():
            return set()
        codes: list[str] = []
        for name in sorted(os.listdir(self.root), key=str.casefold):
            if not name.casefold().endswith("_data.parquet"):
                continue
            codes.append(name[: -len("_data.parquet")])
        return set(normalized_price_cache_codes(codes))

    def is_fresh_for_source(self, stock_code: str, source_csv_path: Path | str | None) -> bool:
        parquet_path = self.parquet_path(stock_code)
        if not parquet_path.exists():
            return False
        if source_csv_path is None:
            return True
        source_path = Path(source_csv_path)
        if not source_path.exists():
            return True
        try:
            return parquet_path.stat().st_mtime >= source_path.stat().st_mtime
        except Exception:
            return False

    def read_if_fresh(
        self,
        stock_code: str,
        *,
        source_csv_path: Path | str | None = None,
    ) -> PriceL2CacheRead | None:
        if not self.is_fresh_for_source(stock_code, source_csv_path):
            return None
        parquet_path = self.parquet_path(stock_code)
        frame = pd.read_parquet(parquet_path)
        manifest = self.read_manifest(stock_code)
        return PriceL2CacheRead(frame=frame, path=parquet_path, manifest=manifest)

    def read_manifest(self, stock_code: str) -> dict[str, Any] | None:
        path = self.manifest_path(stock_code)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def write(
        self,
        stock_code: str,
        frame: pd.DataFrame,
        *,
        source_csv_path: Path | str | None = None,
    ) -> Path:
        code = normalize_price_cache_code(stock_code)
        self.root.mkdir(parents=True, exist_ok=True)
        parquet_path = self.parquet_path(code)
        tmp_path = parquet_path.with_name(f"{parquet_path.name}.{os.getpid()}.tmp")
        try:
            frame.to_parquet(tmp_path, index=False)
            tmp_path.replace(parquet_path)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
        self._write_manifest(code, frame, parquet_path=parquet_path, source_csv_path=source_csv_path)
        return parquet_path

    def _write_manifest(
        self,
        stock_code: str,
        frame: pd.DataFrame,
        *,
        parquet_path: Path,
        source_csv_path: Path | str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "artifact_type": "price_l2_cache_manifest",
            "schema_version": PRICE_L2_CACHE_SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "stock_code": normalize_price_cache_code(stock_code),
            "parquet_path": str(parquet_path),
            "rows": int(len(frame)),
            "columns": [str(col) for col in frame.columns],
        }
        if "trade_date" in frame.columns and not frame.empty:
            trade_dates = pd.to_datetime(frame["trade_date"], errors="coerce")
            valid_dates = trade_dates.dropna()
            if not valid_dates.empty:
                payload["start_trade_date"] = valid_dates.min().strftime("%Y%m%d")
                payload["end_trade_date"] = valid_dates.max().strftime("%Y%m%d")
        if source_csv_path is not None:
            source_path = Path(source_csv_path)
            source_info: dict[str, Any] = {"path": str(source_path)}
            try:
                stat = source_path.stat()
                source_info["size_bytes"] = stat.st_size
                source_info["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            except Exception:
                source_info["exists"] = False
            else:
                source_info["exists"] = True
            payload["source_csv"] = source_info
        self.manifest_path(stock_code).write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )


__all__ = [
    "PRICE_L2_CACHE_SCHEMA_VERSION",
    "PriceL2CacheRead",
    "PriceParquetL2Cache",
    "default_price_l2_cache_dir",
    "price_l2_cache_enabled_from_env",
    "price_l2_cache_write_enabled_from_env",
]
