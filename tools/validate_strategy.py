"""用途:
- 对指定策略模块执行结构化验证协议，生成 JSON 和 Markdown 报告。
- 适合在策略提交、回归验证或对外同步前做统一校验。

用法:
- 使用默认协议验证当前配置策略：`python tools/validate_strategy.py`
- 指定模块和输出目录：`python tools/validate_strategy.py --module strategy_library.generated.demo --output-root output/tools/strategy_validation/demo`"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_validation_deps():
    from app.strategy_validation_service import StrategyValidationService
    from config import get_config

    return get_config, StrategyValidationService


def main() -> int:
    get_config, StrategyValidationService = _load_validation_deps()
    cfg = get_config(reload=True)
    parser = argparse.ArgumentParser(
        description="按结构化协议对策略模块执行验证，并输出 JSON / Markdown 报告。",
        formatter_class=argparse.RawTextHelpFormatter,
        allow_abbrev=False,
        epilog=(
            "示例:\n"
            "  python tools/validate_strategy.py\n"
            "  python tools/validate_strategy.py --module strategy_library.generated.demo --output-root output/tools/strategy_validation/demo"
        ),
    )
    parser.add_argument(
        "--module",
        default=cfg.strategy.module,
        help="策略模块 import path；默认取配置中的 `[strategy].module`。",
    )
    parser.add_argument("--campaign-id", default="", help="Pre-registered Codex research campaign id.")
    parser.add_argument("--candidate-id", default="", help="Candidate id bound to immutable source content.")
    parser.add_argument(
        "--campaign-db",
        default="",
        help=(
            "Optional campaign SQLite path below output/codex_campaigns; "
            "overrides research_governance.campaign_db."
        ),
    )
    parser.add_argument(
        "--promotion",
        action="store_true",
        help="Require promotion-grade authoring and research-contract checks.",
    )
    parser.add_argument(
        "--protocol",
        default=str(ROOT / "docs" / "validation" / "strategy_validation_protocol_template.json"),
        help="验证协议 JSON 路径。",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="可选：结果输出根目录；默认写入 `output/strategy_validation/<run_id>_<module>/`。",
    )
    args = parser.parse_args()

    service = StrategyValidationService()
    protocol = service.load_protocol(args.protocol)
    report = service.run(
        base_cfg=cfg,
        module=str(args.module).strip(),
        protocol=protocol,
        output_root=(Path(args.output_root) if str(args.output_root).strip() else None),
        campaign_id=str(args.campaign_id or "").strip(),
        candidate_id=str(args.candidate_id or "").strip(),
        campaign_db=(Path(args.campaign_db) if str(args.campaign_db).strip() else None),
        promotion=bool(args.promotion),
    )
    summary = report.get("summary") or {}
    print(f"module={report.get('module')}")
    print(f"protocol={report.get('protocol_version')}")
    print(f"decision={summary.get('decision')}")
    print(f"output_root={report.get('output_root')}")
    print(f"json_report={Path(report.get('output_root') or '') / 'validation_report.json'}")
    print(f"markdown_report={Path(report.get('output_root') or '') / 'validation_report.md'}")
    return 0 if bool(summary.get("passed")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
