"""CLI for the canonical strategy contract validator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_strategy_module() -> str:
    from config import get_config

    return str(get_config(reload=True).strategy.module)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate STRATEGY_META as a declarative strategy contract."
    )
    parser.add_argument(
        "module",
        nargs="?",
        help="Strategy module import path or repo-scoped .py path.",
    )
    parser.add_argument(
        "--module",
        dest="module_opt",
        default="",
        help="Strategy module import path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat missing optional research-contract fields as a failing result.",
    )
    parser.add_argument(
        "--skip-static",
        action="store_true",
        help="Skip AST/static source checks.",
    )
    parser.add_argument(
        "--allow-get-config",
        action="store_true",
        help="Allow strategies that call get_config() during static checks.",
    )
    parser.add_argument("--json-out", default="", help="Optional output JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    from strategies.api import validate_strategy_module

    args = build_arg_parser().parse_args(argv)
    module = str(args.module_opt or args.module or "").strip() or _default_strategy_module()
    payload = validate_strategy_module(
        module,
        strict=bool(args.strict),
        include_static=not bool(args.skip_static),
        allow_get_config=bool(args.allow_get_config),
    )
    if str(args.json_out or "").strip():
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "ok": payload["ok"],
                "status": payload["status"],
                "module": payload["module"],
                "summary": payload["summary"],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
