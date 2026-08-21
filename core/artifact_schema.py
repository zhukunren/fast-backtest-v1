"""Schema version constants for persisted runtime artifacts."""
from __future__ import annotations


ARTIFACT_SCHEMA_VERSION = 2
RUN_METADATA_SCHEMA_VERSION = 2
ARTIFACT_MANIFEST_SCHEMA_VERSION = 2
COMPILED_PLAN_SCHEMA_VERSION = 2
RESEARCH_AUDIT_SCHEMA_VERSION = 2
ROBUSTNESS_REPORT_SCHEMA_VERSION = 2
NET_VALUE_SCHEMA_VERSION = 2
TRADES_SCHEMA_VERSION = 2
DAILY_HOLDINGS_SCHEMA_VERSION = 2
MARKET_STATE_SCHEMA_VERSION = 2
FILLS_LEDGER_SCHEMA_VERSION = 2


def artifact_schema_fields(artifact_type: str, schema_version: int) -> dict[str, object]:
    """Return common top-level schema markers for JSON artifacts."""
    return {
        "artifact_type": str(artifact_type),
        "schema_version": int(schema_version),
    }


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "COMPILED_PLAN_SCHEMA_VERSION",
    "DAILY_HOLDINGS_SCHEMA_VERSION",
    "FILLS_LEDGER_SCHEMA_VERSION",
    "MARKET_STATE_SCHEMA_VERSION",
    "NET_VALUE_SCHEMA_VERSION",
    "RESEARCH_AUDIT_SCHEMA_VERSION",
    "ROBUSTNESS_REPORT_SCHEMA_VERSION",
    "RUN_METADATA_SCHEMA_VERSION",
    "TRADES_SCHEMA_VERSION",
    "artifact_schema_fields",
]
