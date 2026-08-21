from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from core.exceptions import ValidationError
from core.external_validation import external_validation_json_schema, validate_external_validation_report


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate future external-data evidence reports.")
    parser.add_argument("report", nargs="?", help="JSON report to validate")
    parser.add_argument("--schema-out", default="", help="Write the canonical JSON schema")
    args = parser.parse_args(argv)

    if args.schema_out:
        _write_json(Path(args.schema_out), external_validation_json_schema())
    if not args.report:
        if args.schema_out:
            return 0
        parser.error("report is required unless --schema-out is provided")

    report_path = Path(args.report)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = validate_external_validation_report(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "candidate_commit": report.candidate_commit,
                "status": report.status,
                "trusted_external": report.market_snapshot.trusted_external,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
